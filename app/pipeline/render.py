"""Rendert einen Short mit FFmpeg: 1080x1920, Schnitte, Punch-in-Zooms, Untertitel, Effekte, Musik."""
from __future__ import annotations

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


def build_command(src: Path, info: VideoInfo, pieces: list[Piece], out: Path,
                  ass_name: str | None, music: Path | None, sfx: bool, preset: str = "veryfast") -> list[str]:
    seek = max(0.0, pieces[0].start - 0.05)
    span = pieces[-1].end - seek + 0.1
    total = sum(p.duration for p in pieces)
    inputs = ["-ss", f"{seek:.3f}", "-t", f"{span:.3f}", "-i", str(src)]
    n_in = 1
    if not info.has_audio:
        inputs += ["-f", "lavfi", "-t", f"{span:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
        audio_src = f"{n_in}:a"
        n_in += 1
    else:
        audio_src = "0:a:0"

    f: list[str] = []
    concat_in = []
    for i, p in enumerate(pieces):
        a, b = p.start - seek, p.end - seek
        cw, ch, x, y = crop_box(info.width, info.height, p.focus, p.zoom)
        f.append(f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS,"
                 f"crop={cw}:{ch}:{x}:{y},scale={TARGET_W}:{TARGET_H}:flags=lanczos,setsar=1,fps=30[v{i}]")
        fades = ""
        if p.hard_cut:
            fades += ",afade=t=in:d=0.02"
        if i + 1 < len(pieces) and pieces[i + 1].hard_cut:
            fades += f",afade=t=out:st={max(0.0, p.duration - 0.03):.3f}:d=0.03"
        f.append(f"[{audio_src}]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS,"
                 f"aresample=48000,aformat=channel_layouts=stereo{fades}[a{i}]")
        concat_in.append(f"[v{i}][a{i}]")
    f.append(f"{''.join(concat_in)}concat=n={len(pieces)}:v=1:a=1[vc][ac]")
    f.append(f"[vc]ass={ass_name}[vout]" if ass_name else "[vc]null[vout]")

    mix = ["[ac]"]
    times = sfx_times(pieces) if sfx else []
    if times:
        inputs += ["-f", "lavfi", "-t", "0.4", "-i", "anoisesrc=c=pink:a=0.6:d=0.4:r=48000"]
        sfx_idx = n_in
        n_in += 1
        f.append(f"[{sfx_idx}:a]highpass=f=600,lowpass=f=4000,afade=t=in:d=0.15,"
                 f"afade=t=out:st=0.15:d=0.25,volume=0.18,aformat=channel_layouts=stereo,"
                 f"asplit={len(times)}" + "".join(f"[s{k}]" for k in range(len(times))))
        for k, t in enumerate(times):
            ms = int(t * 1000)
            f.append(f"[s{k}]adelay={ms}|{ms}[sd{k}]")
            mix.append(f"[sd{k}]")
    if music is not None:
        inputs += ["-stream_loop", "-1", "-i", str(music)]
        mus_idx = n_in
        n_in += 1
        f.append(f"[{mus_idx}:a]atrim=0:{total:.3f},aresample=48000,aformat=channel_layouts=stereo,"
                 f"volume=0.10,afade=t=out:st={max(0.0, total - 1.5):.3f}:d=1.5[mus]")
        mix.append("[mus]")
    if len(mix) > 1:
        f.append(f"{''.join(mix)}amix=inputs={len(mix)}:duration=first:normalize=0[amixed]")
        last = "[amixed]"
    else:
        last = "[ac]"
    f.append(f"{last}loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[aout]")

    return ["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", ";".join(f),
            "-map", "[vout]", "-map", "[aout]", "-c:v", "libx264", "-preset", preset, "-crf", "21",
            "-pix_fmt", "yuv420p", "-profile:v", "high", "-r", "30", "-c:a", "aac", "-b:a", "160k",
            "-movflags", "+faststart", "-t", f"{total:.3f}", str(out)]


def render_clip(src: Path, info: VideoInfo, pieces: list[Piece], out: Path, ass_path: Path | None,
                music: Path | None, sfx: bool, preset: str = "veryfast") -> None:
    cmd = build_command(src, info, pieces, out, ass_path.name if ass_path else None, music, sfx, preset)
    # Im Job-Ordner ausführen, damit der Untertitel-Pfad nicht escaped werden muss
    import subprocess
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(out.parent))
    if proc.returncode != 0:
        raise RuntimeError("FFmpeg-Rendering fehlgeschlagen:\n" + (proc.stderr or "")[-2000:])


def make_thumbnail(clip: Path, out: Path) -> None:
    run(["ffmpeg", "-y", "-v", "error", "-ss", "1", "-i", str(clip), "-frames:v", "1",
         "-vf", "scale=360:-2", "-q:v", "4", str(out)])
