import numpy as np

from app.pipeline import highlights
from tests.conftest import fake_transcript


def test_candidates_respect_length_limits():
    tr = fake_transcript(200)
    rms = np.random.default_rng(0).random(2000).astype("float32")
    cands = highlights.candidates_from_transcript(tr, rms, 0.1, [], 15, 60)
    assert cands
    assert all(15 <= c.duration <= 60 for c in cands)


def test_select_returns_non_overlapping_3_to_5():
    tr = fake_transcript(300)
    rms = np.ones(3000, dtype="float32")
    cands = highlights.candidates_from_transcript(tr, rms, 0.1, [], 15, 60)
    chosen = highlights.select(cands, 3, 5)
    assert 3 <= len(chosen) <= 5
    for a, b in zip(chosen, chosen[1:]):
        assert a.end <= b.start


def test_hook_scoring_prefers_questions():
    q = highlights.score_text("Warum macht niemand das? Weil es geht.", "Warum macht niemand das?")
    weak = highlights.score_text("und dann ging ich so", "und dann ging ich so")
    assert q["hook"] > weak["hook"]
    assert q["completeness"] == 1.0 and weak["completeness"] < 1.0


def test_audio_fallback_prefers_loud_part():
    rms = np.full(1200, 0.05, dtype="float32")
    rms[600:900] = 0.8  # laute Stelle bei 60–90 s
    cands = highlights.candidates_from_audio(120, rms, 0.1, [], 15, 60)
    best = highlights.select(cands, 1, 1)[0]
    assert 55 <= best.start <= 65


def test_audio_fallback_gives_three_clips_for_short_video():
    rms = np.random.default_rng(1).random(600).astype("float32")
    cands = highlights.candidates_from_audio(60, rms, 0.1, [], 15, 60, n_wanted=3)
    assert len(highlights.select(cands, 3, 5)) == 3
