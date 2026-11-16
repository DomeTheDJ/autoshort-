"""Hilfsfunktionen rund um FFmpeg/ffprobe und Audio-Analyse."""
from __future__ import annotations

import json
import re
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.errors import UserError


@dataclass
class VideoInfo:
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool
    rotation: int = 0


def run(cmd: list[str], timeout: float | None = None) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        tail = (proc.stderr or "")[-1500:]
        raise RuntimeError(f"Befehl fehlgeschlagen: {cmd[0]}\n{tail}")
    return proc


def probe(path: Path) -> VideoInfo:
    try:
        proc = run(["ffprobe", "-v", "error", "-print_format", "json",
                    "-show_streams", "-show_format", str(path)], timeout=120)
    except RuntimeError as exc:
        raise UserError("Die Datei konnte nicht als Video gelesen werden. "
                        "Bitte eine MP4- oder MOV-Datei hochladen.") from exc
    data = json.loads(proc.stdout or "{}")
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise UserError("In der Datei wurde keine Videospur gefunden.")
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    duration = float(data.get("format", {}).get("duration") or video.get("duration") or 0)
    rate = video.get("avg_frame_rate") or video.get("r_frame_rate") or "30/1"
    try:
        num, den = rate.split("/")
        fps = float(num) / float(den) if float(den) else 30.0
    except (ValueError, ZeroDivisionError):
        fps = 30.0
    rotation = 0
    for sd in video.get("side_data_list", []) or []:
        if "rotation" in sd:
            rotation = int(sd["rotation"])
    if "rotate" in (video.get("tags") or {}):
        rotation = int(video["tags"]["rotate"])
    w, h = int(video.get("width", 0)), int(video.get("height", 0))
    if abs(rotation) % 180 == 90:  # Handy-Hochkantvideos
        w, h = h, w
    if duration <= 0 or w <= 0 or h <= 0:
        raise UserError("Das Video scheint leer oder beschädigt zu sein.")
    return VideoInfo(duration=duration, width=w, height=h, fps=fps or 30.0,
                     has_audio=has_audio, rotation=rotation)


def extract_audio_wav(src: Path, dst: Path, sample_rate: int = 16000) -> Path:
    run(["ffmpeg", "-y", "-v", "error", "-vn", "-i", str(src), "-ac", "1",
         "-ar", str(sample_rate), "-c:a", "pcm_s16le", str(dst)])
    return dst


def wav_to_mp3_chunks(wav: Path, out_dir: Path, chunk_seconds: int = 600) -> list[tuple[Path, float]]:
    """Teilt das Audio in kleine MP3-Stücke (API-Limit 25 MB). Gibt (Datei, Startzeit) zurück."""
    out_dir.mkdir(parents=True, exist_ok=True)
    run(["ffmpeg", "-y", "-v", "error", "-i", str(wav), "-f", "segment",
         "-segment_time", str(chunk_seconds), "-c:a", "libmp3lame", "-b:a", "48k",
         str(out_dir / "chunk_%03d.mp3")])
    chunks = sorted(out_dir.glob("chunk_*.mp3"))
    return [(c, i * float(chunk_seconds)) for i, c in enumerate(chunks)]


def audio_rms(wav: Path, window: float = 0.1) -> tuple[np.ndarray, float]:
    """Lautstärke (RMS) pro Zeitfenster. Gibt (Werte, Fensterlänge) zurück."""
    with wave.open(str(wav), "rb") as wf:
        rate = wf.getframerate()
        frames = wf.readframes(wf.getnframes())
    samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    hop = max(1, int(rate * window))
    n = len(samples) // hop
    if n == 0:
        return np.zeros(1, dtype=np.float32), window
    blocks = samples[: n * hop].reshape(n, hop)
    return np.sqrt((blocks ** 2).mean(axis=1) + 1e-12), window


def detect_scenes(src: Path, threshold: float = 0.3) -> list[float]:
    """Zeitpunkte von Szenenwechseln (Sekunden). Läuft auf kleiner Auflösung, damit es schnell ist."""
    proc = subprocess.run(
        # nur Schlüsselbilder dekodieren: viel schneller und sparsamer (wichtig bei 4K)
        ["ffmpeg", "-v", "info", "-threads", "1", "-skip_frame", "nokey", "-i", str(src), "-an", "-sn",
         "-vf", f"scale=160:-2,select='gt(scene,{threshold})',showinfo", "-vsync", "vfr", "-f", "null", "-"],
        capture_output=True, text=True)
    return [float(m) for m in re.findall(r"pts_time:([0-9.]+)", proc.stderr or "")]


def silence_intervals(rms: np.ndarray, window: float, min_len: float = 0.7,
                      rel_threshold: float = 0.12) -> list[tuple[float, float]]:
    """Findet stille Abschnitte anhand der Lautstärke (Fallback ohne Transkript)."""
    if rms.size == 0:
        return []
    ref = float(np.percentile(rms, 90)) or 1e-6
    quiet = rms < ref * rel_threshold
    out: list[tuple[float, float]] = []
    start = None
    for i, q in enumerate(quiet):
        if q and start is None:
            start = i
        elif not q and start is not None:
            if (i - start) * window >= min_len:
                out.append((start * window, i * window))
            start = None
    if start is not None and (len(quiet) - start) * window >= min_len:
        out.append((start * window, len(quiet) * window))
    return out


def sample_frames(src: Path, start: float, end: float, fps: float,
                  src_w: int, src_h: int, width: int = 320) -> list[tuple[float, np.ndarray]]:
    """Liefert (Zeit, Graustufenbild) für die Gesichtserkennung – über eine FFmpeg-Pipe."""
    height = max(2, int(round(width * src_h / src_w / 2)) * 2)
    duration = max(0.1, end - start)
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-threads", "1", "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(src),
         "-vf", f"fps={fps},scale={width}:{height},format=gray", "-f", "rawvideo", "-"],
        capture_output=True)
    if proc.returncode != 0 or not proc.stdout:
        return []
    frame_size = width * height
    count = len(proc.stdout) // frame_size
    buf = np.frombuffer(proc.stdout[: count * frame_size], dtype=np.uint8)
    frames = buf.reshape(count, height, width)
    return [(start + i / fps, frames[i]) for i in range(count)]
