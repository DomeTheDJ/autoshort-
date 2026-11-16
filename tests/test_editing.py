from app.pipeline import editing
from app.pipeline.transcript import Word


def W(t, s, e):
    return Word(t, s, e)


def test_fillers_and_repetitions_are_removed():
    words = [W("Ich", 0, .2), W("ich", .25, .45), W("äh", .5, .8), W("zeige", .9, 1.2), W("Ähm,", 1.3, 1.5), W("dir", 1.6, 1.8)]
    out = [w.text for w in editing.drop_disfluencies(words)]
    assert out == ["ich", "zeige", "dir"]


def test_long_pauses_are_cut_short_pauses_kept():
    words = [W("a", 0.0, 0.5), W("b", 0.7, 1.0), W("c", 3.0, 3.5)]  # 0,2 s Pause bleibt, 2 s Pause fliegt raus
    iv = editing.keep_intervals_from_words(words, 0.0, 4.0)
    assert len(iv) == 2
    assert iv[0].start == 0.0 and abs(iv[0].end - 1.12) < 1e-6
    assert abs(iv[1].start - 2.88) < 1e-6
    assert editing.total_duration(iv) < 2.0


def test_time_mapping_and_word_remap():
    iv = [editing.Interval(10, 12), editing.Interval(20, 21)]
    assert editing.map_time(11, iv) == 1
    assert editing.map_time(20.5, iv) == 2.5
    assert editing.map_time(15, iv) is None
    mapped = editing.remap_words([W("x", 20.1, 20.4), W("weg", 15, 15.3)], iv)
    assert len(mapped) == 1 and abs(mapped[0].start - 2.1) < 1e-6


def test_silence_fallback():
    iv = editing.keep_intervals_from_silence([(2.0, 4.0), (6.0, 6.2)], 0.0, 10.0)
    # die 2-s-Stille wird auf 0,25 s gekürzt, die kurze bleibt
    assert abs(editing.total_duration(iv) - 8.25) < 1e-6
