"""Gesamte Verarbeitung eines Videos: von der Originaldatei bis zur ZIP-Datei."""
from __future__ import annotations

import logging
import shutil
import zipfile
from pathlib import Path
from typing import Callable

from app import costs
from app.config import Settings
from app.errors import UserError
from app.pipeline import ai_rank, editing, highlights, media, reframe, render, subtitles
from app.pipeline.transcript import Transcript, transcribe_openai

log = logging.getLogger(__name__)
Progress = Callable[[float, str], None]

MUSIC_EXT = {".mp3", ".m4a", ".aac", ".wav", ".ogg"}


def list_music(settings: Settings) -> list[str]:
    if not settings.music_dir.is_dir():
        return []
    return sorted(p.name for p in settings.music_dir.iterdir() if p.suffix.lower() in MUSIC_EXT)


def get_transcript(wav: Path, work: Path, settings: Settings, duration: float) -> tuple[Transcript, float]:
    """Gibt (Transkript, Kosten in USD) zurück. Ohne API-Schlüssel: leeres Transkript."""
    if not settings.use_openai():
        return Transcript(), 0.0
    chunks = media.wav_to_mp3_chunks(wav, work / "chunks")
    tr = transcribe_openai(chunks, settings.openai_api_key, settings.language)
    return tr, costs.whisper_cost(duration)


def process(job_dir: Path, source: Path, options: dict, settings: Settings,
            progress: Progress, tracker: costs.CostTracker) -> dict:
    work = job_dir / "work"
    out_dir = job_dir / "output"
    work.mkdir(exist_ok=True)
    out_dir.mkdir(exist_ok=True)
    notes: list[str] = []

    progress(2, "Video wird geprüft")
    info = media.probe(source)
    size_mb = source.stat().st_size // (1024 * 1024)
    (job_dir / "video.txt").write_text(
        f"Video {info.width}x{info.height}, {info.fps:.0f} fps, {info.duration:.0f} s, {size_mb} MB, "
        f"{info.codec} {info.pix_fmt}")
    if info.duration > settings.max_video_minutes * 60:
        raise UserError(f"Das Video ist {info.duration / 60:.1f} Minuten lang. Erlaubt sind höchstens "
                        f"{settings.max_video_minutes:.0f} Minuten (MAX_VIDEO_MINUTES).")
    if info.duration < settings.clip_min_s:
        raise UserError(f"Das Video ist zu kurz (mindestens {settings.clip_min_s:.0f} Sekunden).")

    n_max = max(1, min(settings.max_clips, int(options.get("clips", settings.max_clips))))
    n_min = min(settings.min_clips, n_max)

    estimate = (costs.whisper_cost(info.duration) if settings.use_openai() else 0.0)
    if settings.use_claude():
        estimate += ai_rank.estimate_cost(settings.claude_model, 30)
    tracker.check(estimate)

    progress(5, "Ton wird extrahiert")
    wav = work / "audio.wav"
    if info.has_audio:
        media.extract_audio_wav(source, wav)
        rms, window = media.audio_rms(wav)
    else:
        import numpy as np
        rms, window = np.zeros(max(1, int(info.duration / 0.1)), dtype="float32"), 0.1
        notes.append("Das Video hat keine Tonspur – keine Untertitel möglich.")

    progress(10, "Sprache wird erkannt")
    transcript, cost = (get_transcript(wav, work, settings, info.duration) if info.has_audio
                        else (Transcript(), 0.0))
    tracker.add(cost)
    if transcript.empty and info.has_audio:
        notes.append("Keine Spracherkennung aktiv (OPENAI_API_KEY fehlt) – Auswahl nur nach "
                     "Lautstärke und Szenenwechseln, ohne Untertitel.")
    (work / "transcript.json").write_text(transcript.to_json(), encoding="utf-8")

    progress(35, "Szenenwechsel werden gesucht")
    scenes = media.detect_scenes(source)

    progress(45, "Spannende Momente werden bewertet")
    if not transcript.empty:
        cands = highlights.candidates_from_transcript(transcript, rms, window, scenes,
                                                      settings.clip_min_s, settings.clip_max_s)
    else:
        cands = []
    if not cands:
        cands = highlights.candidates_from_audio(info.duration, rms, window, scenes,
                                                 settings.clip_min_s, settings.clip_max_s, n_min)
    if not cands:
        raise UserError("Es konnten keine geeigneten Ausschnitte gefunden werden.")

    chosen = None
    if settings.use_claude() and not transcript.empty:
        short = highlights.shortlist(cands, 30)
        try:
            res = ai_rank.rank_with_claude(short, n_min, n_max, settings.anthropic_api_key,
                                           settings.claude_model)
            tracker.add(res.cost_usd)
            chosen = res.highlights
            notes.append("Auswahl durch KI (Claude).")
        except Exception as exc:  # KI optional – immer auf Heuristik zurückfallen
            log.warning("KI-Bewertung fehlgeschlagen: %s", exc)
            notes.append("KI-Bewertung nicht verfügbar – automatische Auswahl per Heuristik.")
    if not chosen:
        chosen = highlights.select(cands, n_min, n_max)
    if len(chosen) < n_min:
        notes.append("Das Video gibt nur einen passenden Ausschnitt her." if len(chosen) == 1 else
                     f"Das Video gibt nur {len(chosen)} passende Ausschnitte her.")

    all_words = editing.drop_disfluencies(transcript.words) if options.get("remove_pauses", True) else transcript.words
    silences = media.silence_intervals(rms, window) if info.has_audio else []
    music = _pick_music(options.get("music"), settings)

    clips = []
    for i, h in enumerate(chosen, start=1):
        base = 50 + 45 * (i - 1) / len(chosen)
        progress(base, f"Short {i} von {len(chosen)}: Bildausschnitt")
        limit = chosen[i].start - 0.5 if i < len(chosen) else info.duration  # nicht in den nächsten Clip
        intervals = _cut_list(h, all_words, silences, transcript, limit, options, settings)

        faces = reframe.detect_faces(source, h.start, h.end, info.width, info.height)
        fallback = reframe.overall_focus(faces)
        focuses = [reframe.focus_for(faces, iv.start, iv.end, fallback) for iv in intervals]
        pieces = render.plan_pieces(intervals, focuses, zooms=options.get("zooms", True))

        ass_path = None
        if options.get("subtitles", True) and not transcript.empty:
            clip_words = [w for w in all_words if h.start <= (w.start + w.end) / 2 <= h.end]
            mapped = editing.remap_words(clip_words, intervals)
            if mapped:
                ass_path = out_dir / f"short_{i}.ass"
                ass_path.write_text(subtitles.build_ass(mapped), encoding="utf-8")

        progress(base + 10 / len(chosen), f"Short {i} von {len(chosen)}: Rendern")
        out = out_dir / f"short_{i}.mp4"
        render.render_clip(source, info, pieces, out, ass_path, music,
                           sfx=options.get("sfx", True), preset=settings.x264_preset)
        render.make_thumbnail(out, out_dir / f"short_{i}.jpg")
        if ass_path:
            ass_path.unlink(missing_ok=True)
        clips.append({
            "index": i, "file": out.name, "thumb": f"short_{i}.jpg",
            "title": h.title or highlights.default_title(h),
            "source_start": round(h.start, 2), "source_end": round(h.end, 2),
            "duration": round(editing.total_duration(intervals), 2),
            "score": round(h.score, 3),
            "reasons": {k: (round(v, 2) if isinstance(v, float) else v) for k, v in h.reasons.items()},
            "faces": bool(faces),
        })

    progress(97, "ZIP-Datei wird erstellt")
    zip_path = out_dir / "shorts.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED) as zf:
        for c in clips:
            zf.write(out_dir / c["file"], arcname=c["file"])
    shutil.rmtree(work, ignore_errors=True)
    return {"clips": clips, "zip": zip_path.name, "notes": notes,
            "video": {"duration": round(info.duration, 1), "width": info.width, "height": info.height}}


def _cut_list(h, words, silences, transcript: Transcript, limit: float, options: dict,
              settings: Settings) -> list[editing.Interval]:
    """Schnittliste für einen Clip. Wird der Clip durch das Entfernen von Pausen kürzer als die
    Mindestlänge, wird er bis zum nächsten Satzende verlängert (höchstens bis `limit`).
    Reicht das nicht, werden nur sehr lange Pausen gekürzt bzw. gar nichts geschnitten."""
    if not options.get("remove_pauses", True):
        return [editing.Interval(h.start, h.end)]
    seg_ends = [s.end for s in transcript.segments]
    while True:
        if not transcript.empty:
            intervals = editing.keep_intervals_from_words(words, h.start, h.end)
        else:
            intervals = editing.keep_intervals_from_silence(silences, h.start, h.end)
        total = editing.total_duration(intervals)
        if total >= settings.clip_min_s:
            break
        nxt = next((e for e in seg_ends if e > h.end + 0.05), None)
        new_end = min(limit, nxt if nxt is not None else h.end + 2.0)
        if new_end <= h.end + 0.05 or new_end - h.start > settings.clip_max_s * 1.5:
            break
        h.end = new_end
    if editing.total_duration(intervals) < settings.clip_min_s and not transcript.empty:
        intervals = editing.keep_intervals_from_words(words, h.start, h.end, max_pause=1.5)
    if editing.total_duration(intervals) < settings.clip_min_s:
        intervals = [editing.Interval(h.start, min(h.end, h.start + settings.clip_max_s))]
    if editing.total_duration(intervals) > settings.clip_max_s:  # Sicherheitsnetz
        trimmed, acc = [], 0.0
        for iv in intervals:
            room = settings.clip_max_s - acc
            if room <= 0.3:
                break
            trimmed.append(editing.Interval(iv.start, min(iv.end, iv.start + room)))
            acc += trimmed[-1].duration
        intervals = trimmed
    return intervals


def _pick_music(name: str | None, settings: Settings) -> Path | None:
    if not name:
        return None
    if name not in list_music(settings):  # nur Dateien aus dem Musikordner, kein Pfadtrick
        return None
    return settings.music_dir / name
