from app.pipeline import subtitles
from app.pipeline.transcript import Word


def test_ass_time_format():
    assert subtitles.ass_time(0) == "0:00:00.00"
    assert subtitles.ass_time(61.257) == "0:01:01.26"
    assert subtitles.ass_time(3600) == "1:00:00.00"


def test_word_by_word_highlight():
    words = [Word("Hallo", 0.0, 0.4), Word("liebe", 0.5, 0.8), Word("Leute!", 0.9, 1.3), Word("Heute", 2.0, 2.3)]
    ass = subtitles.build_ass(words)
    events = [l for l in ass.splitlines() if l.startswith("Dialogue:")]
    assert len(events) == 4  # ein Event pro Wort
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass
    # im zweiten Event ist genau „LIEBE“ hervorgehoben
    assert subtitles.HIGHLIGHT + "LIEBE" + subtitles.RESET in events[1]
    assert subtitles.HIGHLIGHT + "HALLO" not in events[1]
    # nach Satzende beginnt eine neue Zeile
    assert "HALLO" not in events[3]


def test_escape_override_characters():
    ass = subtitles.build_ass([Word("{\\b1}böse", 0, 1)])
    line = [l for l in ass.splitlines() if l.startswith("Dialogue:")][0]
    text = line.split(",,", 1)[1]
    assert "{\\b1}" not in text.replace(subtitles.HIGHLIGHT, "").replace(subtitles.RESET, "")


def test_events_never_overlap():
    words = [Word("Eins.", 0.0, 1.0), Word("Zwei", 1.05, 1.3), Word("drei", 1.3, 1.6), Word("vier", 1.6, 1.9),
             Word("fünf", 1.9, 2.4)]
    ass = subtitles.build_ass(words)
    times = []
    for line in ass.splitlines():
        if line.startswith("Dialogue:"):
            _, start, end = line.split(",")[:3]
            times.append((start, end))
    def secs(t):
        h, m, s = t.split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)
    for (s1, e1), (s2, _) in zip(times, times[1:]):
        assert secs(e1) <= secs(s2)
