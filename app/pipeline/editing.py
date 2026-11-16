"""Schnittliste: entfernt lange Pausen, Füllwörter und direkte Wortwiederholungen."""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.pipeline.transcript import Word

FILLERS = {"äh", "ähm", "öh", "öhm", "hm", "hmm", "mhm", "uh", "um", "uhm", "erm", "eh", "ehm"}
_CLEAN = re.compile(r"[^\wäöüÄÖÜß]+")


def _norm(text: str) -> str:
    return _CLEAN.sub("", text.lower())


@dataclass
class Interval:
    start: float  # Zeit im Originalvideo
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


def drop_disfluencies(words: list[Word]) -> list[Word]:
    """Entfernt Füllwörter und Stotter-Wiederholungen („ich ich“ → „ich“)."""
    out: list[Word] = []
    for i, w in enumerate(words):
        n = _norm(w.text)
        if not n or n in FILLERS:
            continue
        nxt = _norm(words[i + 1].text) if i + 1 < len(words) else None
        if nxt == n and (words[i + 1].start - w.end) < 0.6 and len(n) <= 12:
            continue  # die erste Hälfte der Wiederholung weglassen
        out.append(w)
    return out


def keep_intervals_from_words(words: list[Word], clip_start: float, clip_end: float,
                              max_pause: float = 0.6, pad: float = 0.12) -> list[Interval]:
    """Behält gesprochene Bereiche; Pausen länger als max_pause werden gekürzt."""
    words = [w for w in words if w.end > clip_start and w.start < clip_end]
    if not words:
        return [Interval(clip_start, clip_end)]
    out: list[Interval] = []
    cur_s = max(clip_start, words[0].start - pad)
    cur_e = min(clip_end, words[0].end + pad)
    for w in words[1:]:
        ws, we = max(clip_start, w.start - pad), min(clip_end, w.end + pad)
        if ws - cur_e <= max_pause - 2 * pad:  # kurze Pause → zusammenhängend lassen
            cur_e = max(cur_e, we)
        else:
            out.append(Interval(cur_s, cur_e))
            cur_s, cur_e = ws, we
    out.append(Interval(cur_s, cur_e))
    return _merge_short(out)


def keep_intervals_from_silence(silences: list[tuple[float, float]], clip_start: float, clip_end: float,
                                keep_pause: float = 0.25) -> list[Interval]:
    """Fallback ohne Transkript: stille Bereiche im Clip auf keep_pause kürzen."""
    out: list[Interval] = []
    cursor = clip_start
    for s, e in silences:
        if e <= clip_start or s >= clip_end:
            continue
        s, e = max(s, clip_start), min(e, clip_end)
        cut_s, cut_e = s + keep_pause / 2, e - keep_pause / 2
        if cut_e - cut_s <= 0.2:
            continue
        if cut_s > cursor:
            out.append(Interval(cursor, cut_s))
        cursor = cut_e
    if clip_end > cursor:
        out.append(Interval(cursor, clip_end))
    return _merge_short(out) or [Interval(clip_start, clip_end)]


def _merge_short(intervals: list[Interval], min_len: float = 0.35) -> list[Interval]:
    """Sehr kurze Stücke an den Nachbarn hängen, damit es nicht ‚flackert‘."""
    out: list[Interval] = []
    for iv in intervals:
        if iv.duration <= 0:
            continue
        if out and (iv.duration < min_len or iv.start - out[-1].end < 0.05):
            out[-1] = Interval(out[-1].start, max(out[-1].end, iv.end))
        else:
            out.append(iv)
    return out


def map_time(t: float, intervals: list[Interval]) -> float | None:
    """Rechnet eine Originalzeit in die Zeit des geschnittenen Clips um (None = herausgeschnitten)."""
    offset = 0.0
    for iv in intervals:
        if iv.start - 1e-6 <= t <= iv.end + 1e-6:
            return offset + (min(max(t, iv.start), iv.end) - iv.start)
        offset += iv.duration
    return None


def remap_words(words: list[Word], intervals: list[Interval]) -> list[Word]:
    out: list[Word] = []
    for w in words:
        mid = (w.start + w.end) / 2
        if map_time(mid, intervals) is None:
            continue
        s = map_time(max(w.start, _containing(mid, intervals).start), intervals)
        e = map_time(min(w.end, _containing(mid, intervals).end), intervals)
        if s is None or e is None:
            continue
        out.append(Word(text=w.text, start=s, end=max(e, s + 0.05)))
    return out


def _containing(t: float, intervals: list[Interval]) -> Interval:
    for iv in intervals:
        if iv.start - 1e-6 <= t <= iv.end + 1e-6:
            return iv
    return intervals[-1]


def total_duration(intervals: list[Interval]) -> float:
    return sum(iv.duration for iv in intervals)
