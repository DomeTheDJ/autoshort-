"""Rendert einen Short mit FFmpeg: 1080x1920, Schnitte, Punch-in-Zooms, Untertitel, Effekte, Musik."""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.pipeline.editing import Interval
from app.pipeline.media import VideoInfo, run
from app.pipeline.reframe import TARGET_H, TARGET_W, Focus, crop_box

ZOOM_IN = 1.12
PIECE_MAX = 5.0  # alle ~5 s ein dezenter Zoomwechsel
SFX_MIN_GAP = 4.0


@dataclass
class Piece:
    start: float
    end: float
    zoom: float
    focus: Focus
    hard_cut: bool  # True = vorher wurde etwas herausgeschnitten

    @property
    def duration(self) -> float:
        return self.end - self.start


def plan_pieces(intervals: list[Interval], focuses: list[Focus], zooms: bool = True) -> list[Piece]:
    """Teilt die Schnittliste in Stücke und wechselt den Zoom abwechselnd (1.0 / 1.12)."""
    pieces: list[Piece] = []
    level = 0
    for idx, (iv, focus) in enumerate(zip(intervals, focuses)):
        n = max(1, int(iv.duration // PIECE_MAX)) if zooms else 1
        step = iv.duration / n
        for k in range(n):
            s = iv.start + k * step
            e = iv.end if k == n - 1 else s + step
            zoom = ZOOM_IN if (zooms and level % 2 == 1) else 1.0
            pieces.append(Piece(s, e, zoom, focus, hard_cut=(k == 0 and idx > 0)))
            level += 1
    return pieces


def sfx_times(pieces: list[Piece]) -> list[float]:
    """Zeitpunkte (im fertigen Clip) für dezente Whoosh-Effekte bei Zoomwechseln."""
    times, t, last = [], 0.0, -SFX_MIN_GAP
    for i, p in enumerate(pieces):
        if i > 0 and p.zoom != pieces[i - 1].zoom and t - last >= SFX_MIN_GAP:
            times.append(max(0.0, t - 0.12))
            last = t
        t += p.duration
    return times


FPS = 30
# Wenig Threads und kurzer Lookahead = wenig Arbeitsspeicher (wichtig für 512-MB-Server).
# Auf stärkeren Servern kann FFMPEG_THREADS erhöht werden.
THREADS = os.environ.get("FFMPEG_THREADS", "1")
X264_PARAMS = os.environ.get("X264_PARAMS", "rc-lookahead=5")


def piece_command(src: Path, info: VideoInfo, p: Piece, fade_in: bool, fade_out: bool,
                  out: Path, preset: str) -> list[str]:
    """Ein einzelnes Schnittstück rendern (Bildausschnitt, Zoom, 1080x1920).
    Jedes Stück in einem eigenen FFmpeg-Aufruf → der Speicherbedarf bleibt klein,
    auch bei 4K-Handyvideos."""
    frames = max(1, round(p.duration * FPS))
    dur = frames / FPS
    cw, ch, x, y = crop_box(info.width, info.height, p.focus, p.zoom)
    cmd = ["ffmpeg", "-y", "-v", "error", "-threads", THREADS,
           "-ss", f"{p.start:.3f}", "-t", f"{dur + 0.2:.3f}", "-i", str(src)]
    audio = "0:a:0"
    if not info.has_audio:
        cmd += ["-f", "lavfi", "-t", f"{dur + 0.2:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
        audio = "1:a"
    fades = ""
    if fade_in:
        fades += ",afade=t=in:d=0.02"
    if fade_out:
        fades += f",afade=t=out:st={max(0.0, dur - 0.03):.3f}:d=0.03"
    graph = (f"[0:v]crop={cw}:{ch}:{x}:{y},scale={TARGET_W}:{TARGET_H}:flags=bicubic,setsar=1,"
             f"fps={FPS}[v];"
             f"[{audio}]aresample=48000,aformat=sample_fmts=s16:channel_layouts=stereo,apad,"
             f"atrim=0:{dur:.4f}{fades}[a]")
    return cmd + ["-filter_complex", graph, "-filter_complex_threads", "1",
                  "-map", "[v]", "-map", "[a]", "-frames:v", str(frames),
                  "-c:v", "libx264", "-preset", preset, "-crf", "18", "-pix_fmt", "yuv420p",
                  "-profile:v", "high", "-threads", THREADS, "-x264-params", X264_PARAMS,
                  "-c:a", "pcm_s16le", str(out)]


def finish_command(joined: Path, total: float, times: list[float], out: Path, ass_name: str | None,
                   music: Path | None, preset: str) -> list[str]:
    """Untertitel, Soundeffekte, Musik und Lautheit auf den zusammengefügten Clip anwenden."""
    inputs = ["-threads", THREADS, "-i", str(joined)]
    n_in = 1
    f: list[str] = []
    mix = ["[0:a]"]
    if times:
        inputs += ["-f", "lavfi", "-t", "0.4", "-i", "anoisesrc=c=pink:a=0.6:d=0.4:r=48000"]
        f.append(f"[{n_in}:a]highpass=f=600,lowpass=f=4000,afade=t=in:d=0.15,"
                 f"afade=t=out:st=0.15:d=0.25,volume=0.18,aformat=channel_layouts=stereo,"
                 f"asplit={len(times)}" + "".join(f"[s{k}]" for k in range(len(times))))
        for k, t in enumerate(times):
            ms = int(t * 1000)
            f.append(f"[s{k}]adelay={ms}|{ms}[sd{k}]")
            mix.append(f"[sd{k}]")
        n_in += 1
    if music is not None:
        inputs += ["-stream_loop", "-1", "-i", str(music)]
        f.append(f"[{n_in}:a]atrim=0:{total:.3f},aresample=48000,aformat=channel_layouts=stereo,"
                 f"volume=0.10,afade=t=out:st={max(0.0, total - 1.5):.3f}:d=1.5[mus]")
        mix.append("[mus]")
        n_in += 1
    if len(mix) > 1:
        f.append(f"{''.join(mix)}amix=inputs={len(mix)}:duration=first:normalize=0[amixed]")
        last = "[amixed]"
    else:
        last = "[0:a]"
    f.append(f"{last}loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[aout]")
    if ass_name:
        f.append(f"[0:v]ass={ass_name}[vout]")
        video = ["-map", "[vout]", "-c:v", "libx264", "-preset", preset, "-crf", "21",
                 "-pix_fmt", "yuv420p", "-profile:v", "high", "-threads", THREADS,
                 "-x264-params", X264_PARAMS]
    else:
        video = ["-map", "0:v", "-c:v", "copy"]  # spart Rechenzeit
    return ["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", ";".join(f),
            "-filter_complex_threads", "1", *video, "-map", "[aout]",
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", "-t", f"{total:.3f}", str(out)]


def _run(cmd: list[str], cwd: Path) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(cwd))
    if proc.returncode != 0:
        raise RuntimeError("FFmpeg-Rendering fehlgeschlagen:\n" + (proc.stderr or "")[-2000:])


def render_clip(src: Path, info: VideoInfo, pieces: list[Piece], out: Path, ass_path: Path | None,
                music: Path | None, sfx: bool, preset: str = "veryfast") -> None:
    src, out = src.resolve(), out.resolve()
    music = music.resolve() if music else None
    tmp = out.parent / f".{out.stem}_parts"
    tmp.mkdir(exist_ok=True)
    try:
        lines = []
        for i, p in enumerate(pieces):
            part = tmp / f"p{i:03d}.mkv"
            fade_in = p.hard_cut
            fade_out = i + 1 < len(pieces) and pieces[i + 1].hard_cut
            _run(piece_command(src, info, p, fade_in, fade_out, part, preset), tmp)
            lines.append(f"file '{part.name}'")
        (tmp / "list.txt").write_text("\n".join(lines) + "\n")
        joined = tmp / "joined.mkv"
        _run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", "list.txt",
              "-c", "copy", str(joined)], tmp)
        total = sum(max(1, round(p.duration * FPS)) / FPS for p in pieces)
        times = sfx_times(pieces) if sfx else []
        # Im Job-Ordner ausführen, damit der Untertitel-Pfad nicht escaped werden muss
        _run(finish_command(joined, total, times, out, ass_path.name if ass_path else None, music, preset),
             out.parent)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def make_thumbnail(clip: Path, out: Path) -> None:
    run(["ffmpeg", "-y", "-v", "error", "-ss", "1", "-i", str(clip), "-frames:v", "1",
         "-vf", "scale=360:-2", "-q:v", "4", str(out)])
