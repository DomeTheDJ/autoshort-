import os
import subprocess
from pathlib import Path

import pytest

os.environ["AUTOSHORTS_NO_APP"] = "1"  # kein globales App-Objekt beim Import

from app.config import Settings  # noqa: E402
from app.pipeline.transcript import Segment, Transcript, Word  # noqa: E402


def make_video(path: Path, seconds: int = 80, size: str = "1280x720", audio: bool = True) -> Path:
    """Erzeugt ein Testvideo: bewegtes Testbild, Ton mit regelmäßigen Pausen."""
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=30:duration={seconds}"]
    if audio:
        # 440-Hz-Ton, der alle 4 s für 1,2 s stumm ist (simuliert Sprechpausen)
        cmd += ["-f", "lavfi", "-i",
                f"aevalsrc='if(lt(mod(t,4),2.8),0.4*sin(2*PI*440*t),0)':s=48000:d={seconds}"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    if audio:
        cmd += ["-c:a", "aac", "-shortest"]
    cmd.append(str(path))
    subprocess.run(cmd, check=True)
    return path


def fake_transcript(seconds: int = 80) -> Transcript:
    """Künstliches Transkript: jede 4 s ein Satz mit Wort-Zeitstempeln."""
    sentences = [
        "Warum macht niemand diesen einfachen Trick?",
        "Ich ich zeige dir jetzt äh wie das geht.",
        "Das ist wirklich krass und plötzlich klappt alles!",
        "Und dann passiert etwas Unerwartetes.",
        "Am Ende ist die Lösung ganz einfach.",
    ]
    segs = []
    t = 0.0
    i = 0
    while t + 2.8 <= seconds:
        text = sentences[i % len(sentences)]
        toks = text.split()
        step = 2.6 / len(toks)
        words = [Word(tok, t + k * step, t + k * step + step * 0.8) for k, tok in enumerate(toks)]
        segs.append(Segment(text=text, start=words[0].start, end=words[-1].end, words=words))
        t += 4.0
        i += 1
    return Transcript(segments=segs)


@pytest.fixture
def settings(tmp_path) -> Settings:
    s = Settings()
    s.data_dir = tmp_path / "data"
    s.music_dir = tmp_path / "music"
    s.app_password = "test-passwort"
    s.secret_key = "x" * 32
    s.openai_api_key = ""
    s.anthropic_api_key = ""
    s.secure_cookies = False
    s.x264_preset = "ultrafast"
    s.jobs_dir.mkdir(parents=True, exist_ok=True)
    return s
