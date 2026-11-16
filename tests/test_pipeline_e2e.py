"""Echter Durchlauf mit FFmpeg: Testvideo → Shorts → ZIP (Spracherkennung simuliert)."""
import hashlib
import json
import subprocess
import zipfile

import pytest

from app.costs import CostTracker
from app.errors import UserError
from app.pipeline import run as pipeline
from app.pipeline.transcript import Transcript
from tests.conftest import fake_transcript, make_video


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def probe(p):
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams",
                          "-show_format", str(p)], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


@pytest.mark.slow
def test_full_pipeline_with_fake_transcript(tmp_path, settings, monkeypatch):
    src = make_video(tmp_path / "input.mp4", seconds=80)
    before = sha(src)
    monkeypatch.setattr(pipeline, "get_transcript", lambda *a, **k: (fake_transcript(80), 0.0))
    monkeypatch.setattr(settings, "use_openai", lambda: True)
    # lizenzfreie Test-„Musik“ erzeugen
    settings.music_dir.mkdir()
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=f=220:d=5",
                    str(settings.music_dir / "test.mp3")], check=True)
    job = tmp_path / "job"
    job.mkdir()
    steps = []
    result = pipeline.process(job, src, {"clips": 3, "music": "test.mp3"}, settings,
                              lambda p, m: steps.append((p, m)), CostTracker(tmp_path / "c.json", 1.0))

    assert sha(src) == before, "Original darf nicht verändert werden"
    assert 1 <= len(result["clips"]) <= 3
    assert [p for p, _ in steps] == sorted(p for p, _ in steps), "Fortschritt muss steigen"
    for c in result["clips"]:
        info = probe(job / "output" / c["file"])
        v = next(s for s in info["streams"] if s["codec_type"] == "video")
        a = next(s for s in info["streams"] if s["codec_type"] == "audio")
        assert (v["width"], v["height"]) == (1080, 1920)
        assert a["codec_name"] == "aac"
        dur = float(info["format"]["duration"])
        assert 14.5 <= dur <= 60.5, dur
        assert (job / "output" / c["thumb"]).is_file()
        # Pausen wurden entfernt → Clip kürzer als der Originalbereich
        assert c["duration"] < c["source_end"] - c["source_start"]
    with zipfile.ZipFile(job / "output" / result["zip"]) as zf:
        assert sorted(zf.namelist()) == sorted(c["file"] for c in result["clips"])
    assert not (job / "work").exists(), "Temporäre Dateien müssen gelöscht sein"


@pytest.mark.slow
def test_pipeline_without_transcript_and_without_audio(tmp_path, settings):
    src = make_video(tmp_path / "stumm.mp4", seconds=40, size="640x360", audio=False)
    job = tmp_path / "job2"
    job.mkdir()
    result = pipeline.process(job, src, {"clips": 1}, settings, lambda p, m: None,
                              CostTracker(tmp_path / "c.json", 1.0))
    assert len(result["clips"]) == 1
    info = probe(job / "output" / "short_1.mp4")
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (v["width"], v["height"]) == (1080, 1920)
    assert any("Tonspur" in n for n in result["notes"])


def test_too_long_video_is_rejected(tmp_path, settings):
    src = make_video(tmp_path / "lang.mp4", seconds=20, size="320x240")
    settings.max_video_minutes = 0.2  # 12 s
    with pytest.raises(UserError, match="höchstens"):
        pipeline.process(tmp_path, src, {}, settings, lambda p, m: None, CostTracker(tmp_path / "c.json", 1.0))


def test_budget_limit_blocks_job(tmp_path, settings, monkeypatch):
    src = make_video(tmp_path / "v.mp4", seconds=20, size="320x240")
    monkeypatch.setattr(settings, "use_openai", lambda: True)
    tracker = CostTracker(tmp_path / "c.json", daily_budget=0.0001)
    with pytest.raises(UserError, match="Tageslimit"):
        pipeline.process(tmp_path, src, {}, settings, lambda p, m: None, tracker)


def test_invalid_file_gives_clear_error(tmp_path, settings):
    bad = tmp_path / "kaputt.mp4"
    bad.write_bytes(b"kein video")
    with pytest.raises(UserError, match="MP4"):
        pipeline.process(tmp_path, bad, {}, settings, lambda p, m: None, CostTracker(tmp_path / "c.json", 1.0))


def test_empty_transcript_default(tmp_path, settings):
    tr, cost = pipeline.get_transcript(tmp_path / "x.wav", tmp_path, settings, 60)
    assert isinstance(tr, Transcript) and tr.empty and cost == 0.0
