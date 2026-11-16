"""Findet und bewertet Highlight-Kandidaten (Hook, Emotion, Überraschung, Vollständigkeit)."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import numpy as np

from app.pipeline.transcript import Transcript

log = logging.getLogger(__name__)

HOOK_WORDS = {
    "warum", "wieso", "weshalb", "wie", "was", "niemand", "keiner", "nie", "niemals", "geheim",
    "geheimnis", "fehler", "wahrheit", "stell", "stellt", "achtung", "wichtig", "unglaublich",
    "größte", "beste", "schlimmste", "trick", "tipp", "tipps", "hack", "kennst", "wusstest",
    "why", "how", "what", "nobody", "never", "secret", "mistake", "truth", "best", "worst",
}
EMOTION_WORDS = {
    "liebe", "hasse", "angst", "wütend", "krass", "wahnsinn", "wow", "geil", "mega", "verrückt",
    "schockiert", "glücklich", "traurig", "peinlich", "lachen", "weinen", "unfassbar", "hammer",
    "love", "hate", "crazy", "insane", "amazing", "scared", "angry", "omg",
}
SURPRISE_WORDS = {
    "plötzlich", "überraschend", "überraschung", "eigentlich", "tatsächlich", "aber", "doch",
    "stattdessen", "trotzdem", "unerwartet", "dann", "auf einmal", "aufeinmal", "nicht", "falsch",
    "suddenly", "actually", "but", "turns", "instead", "surprise",
}
WEAK_STARTS = {"und", "also", "ähm", "äh", "ja", "so", "oder", "and", "um", "uh"}
WORD_RE = re.compile(r"[\wäöüÄÖÜß]+")


@dataclass
class Highlight:
    start: float
    end: float
    score: float
    text: str = ""
    title: str = ""
    reasons: dict = field(default_factory=dict)

    @property
    def duration(self) -> float:
        return self.end - self.start


def _tokens(text: str) -> list[str]:
    return [t.lower() for t in WORD_RE.findall(text)]


def _energy_stats(rms: np.ndarray, window: float, start: float, end: float) -> tuple[float, float]:
    a, b = int(start / window), max(int(start / window) + 1, int(end / window))
    part = rms[a:b] if rms.size else np.zeros(1)
    if part.size == 0:
        return 0.0, 0.0
    return float(part.mean()), float(part.max())


def score_text(text: str, first: str) -> dict:
    toks = _tokens(text)
    first_toks = _tokens(first)[:12]
    n = max(1, len(toks))
    hook = 0.0
    if "?" in first:
        hook += 0.45
    hook += min(0.45, 0.15 * sum(t in HOOK_WORDS for t in first_toks))
    if any(t.isdigit() for t in first_toks):
        hook += 0.15
    if first_toks and first_toks[0] in {"du", "ihr", "you", "stell", "stellt"}:
        hook += 0.15
    if first_toks and first_toks[0] in WEAK_STARTS:
        hook -= 0.2
    emotion = min(1.0, 0.25 * text.count("!") + 8.0 * sum(t in EMOTION_WORDS for t in toks) / n)
    surprise = min(1.0, 6.0 * sum(t in SURPRISE_WORDS for t in toks) / n)
    end_char = text.rstrip()[-1:] if text.strip() else ""
    complete = 1.0 if end_char in ".!?" else 0.35
    return {"hook": max(0.0, min(1.0, hook)), "emotion": emotion, "surprise": surprise,
            "completeness": complete}


def _combine(parts: dict, energy_z: float, peak_ratio: float, duration: float, scene_density: float) -> float:
    emotion = min(1.0, 0.6 * parts["emotion"] + 0.4 * max(0.0, min(1.0, 0.5 + energy_z / 2)))
    surprise = min(1.0, 0.7 * parts["surprise"] + 0.3 * max(0.0, min(1.0, (peak_ratio - 1.5) / 2)))
    length_bonus = 0.1 if 25 <= duration <= 45 else 0.0
    scene_bonus = 0.05 if 0.02 <= scene_density <= 0.3 else 0.0
    parts["emotion"], parts["surprise"] = emotion, surprise
    return (0.3 * parts["hook"] + 0.25 * emotion + 0.2 * surprise
            + 0.25 * parts["completeness"] + length_bonus + scene_bonus)


def candidates_from_transcript(tr: Transcript, rms: np.ndarray, window: float, scenes: list[float],
                               min_s: float, max_s: float) -> list[Highlight]:
    segs = tr.segments
    gmean = float(rms.mean()) if rms.size else 0.0
    gstd = (float(rms.std()) or 1e-6) if rms.size else 1e-6
    out: list[Highlight] = []
    for i in range(len(segs)):
        text_parts: list[str] = []
        for j in range(i, len(segs)):
            text_parts.append(segs[j].text)
            start, end = segs[i].start, segs[j].end
            dur = end - start
            if dur > max_s:
                break
            if dur < min_s:
                continue
            text = " ".join(text_parts)
            parts = score_text(text, segs[i].text)
            mean, peak = _energy_stats(rms, window, start, end)
            sc = sum(1 for t in scenes if start <= t <= end) / dur
            score = _combine(parts, (mean - gmean) / gstd, peak / (mean or 1e-6), dur, sc)
            out.append(Highlight(start=start, end=end, score=score, text=text, reasons=parts))
    return out


def candidates_from_audio(duration: float, rms: np.ndarray, window: float, scenes: list[float],
                          min_s: float, max_s: float, n_wanted: int = 3) -> list[Highlight]:
    """Fallback ohne Transkript: Fenster mit hoher Audio-Intensität, an Szenenwechseln ausgerichtet."""
    # Bei kurzen Videos kürzere Fenster, damit trotzdem mehrere Clips möglich sind
    length = max(min_s, min(max_s, 30.0, duration / max(1, n_wanted) - 1.5))
    if duration < min_s:
        return []
    length = min(length, duration)
    gmean = float(rms.mean()) if rms.size else 0.0
    gstd = (float(rms.std()) or 1e-6) if rms.size else 1e-6
    out = []
    t = 0.0
    while t + length <= duration + 1e-6:
        start = t
        near = [s for s in scenes if abs(s - t) < 3.0]
        if near:
            start = min(max(0.0, near[0]), max(0.0, duration - length))
        end = min(duration, start + length)
        mean, peak = _energy_stats(rms, window, start, end)
        z = (mean - gmean) / gstd
        score = 0.5 + 0.35 * max(-1.0, min(1.0, z / 2)) + 0.1 * min(1.0, peak / (mean or 1e-6) / 4)
        out.append(Highlight(start=start, end=end, score=score,
                             reasons={"hook": 0.0, "emotion": max(0.0, min(1.0, 0.5 + z / 2)),
                                      "surprise": 0.0, "completeness": 0.5}))
        t += 2.5
    return out


def select(cands: list[Highlight], n_min: int, n_max: int, gap: float = 1.0) -> list[Highlight]:
    """Wählt die besten, sich nicht überschneidenden Clips (chronologisch sortiert).
    Zuerst gierig nach Punktzahl; reicht das nicht für n_min Clips, wird optimal so geplant,
    dass möglichst n_min Clips mit maximaler Gesamtpunktzahl herauskommen."""
    chosen: list[Highlight] = []
    for c in sorted(cands, key=lambda h: h.score, reverse=True):
        if all(c.end + gap <= o.start or c.start >= o.end + gap for o in chosen):
            chosen.append(c)
        if len(chosen) >= n_max:
            break
    if len(chosen) < n_min:
        better = _best_k(cands, n_min, gap)
        if len(better) > len(chosen):
            chosen = better
        log.info("%d Clips ausgewählt (gewünscht mind. %d)", len(chosen), n_min)
    return sorted(chosen, key=lambda h: h.start)


def _best_k(cands: list[Highlight], k: int, gap: float) -> list[Highlight]:
    """Dynamische Programmierung: höchstens k überschneidungsfreie Clips, möglichst viele,
    bei gleicher Anzahl mit größter Gesamtpunktzahl."""
    import bisect
    items = sorted(cands, key=lambda h: h.end)
    ends = [h.end for h in items]
    n = len(items)
    neg = float("-inf")
    # dp[c][i]: beste Summe mit genau c Clips aus den ersten i Kandidaten
    dp = [[0.0] * (n + 1)] + [[neg] * (n + 1) for _ in range(k)]
    prev = [bisect.bisect_right(ends, items[i].start - gap) for i in range(n)]
    for c in range(1, k + 1):
        row, before = dp[c], dp[c - 1]
        for i in range(1, n + 1):
            take = before[prev[i - 1]] + items[i - 1].score if before[prev[i - 1]] > neg else neg
            row[i] = max(row[i - 1], take)
    best_c = max((c for c in range(k + 1) if dp[c][n] > neg), default=0)
    out, c, i = [], best_c, n
    while c > 0 and i > 0:
        if dp[c][i] == dp[c][i - 1]:
            i -= 1
        else:
            out.append(items[i - 1])
            i, c = prev[i - 1], c - 1
    return out


def shortlist(cands: list[Highlight], k: int = 30) -> list[Highlight]:
    """Beste k Kandidaten mit wenig Überschneidung – als Vorauswahl für die KI."""
    out: list[Highlight] = []
    for c in sorted(cands, key=lambda h: h.score, reverse=True):
        overlap_ok = all(min(c.end, o.end) - max(c.start, o.start) < 0.5 * c.duration for o in out)
        if overlap_ok:
            out.append(c)
        if len(out) >= k:
            break
    return sorted(out, key=lambda h: h.start)


def default_title(h: Highlight) -> str:
    words = h.text.split()
    return (" ".join(words[:7]) + ("…" if len(words) > 7 else "")) if words else "Highlight"
