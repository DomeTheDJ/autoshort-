"""Optionale KI-Bewertung der Highlight-Kandidaten mit Claude (Anthropic API)."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from app.pipeline.highlights import Highlight

log = logging.getLogger(__name__)

# USD pro 1 Mio. Token (Eingabe, Ausgabe) – nur für die Kostenschätzung
PRICES = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
}

SYSTEM = (
    "Du bist ein erfahrener Social-Media-Editor für TikTok, Instagram Reels und YouTube Shorts. "
    "Du bekommst nummerierte Ausschnitte aus dem Transkript eines langen Videos. "
    "Bewerte jeden Ausschnitt von 0 bis 10 nach: hook (packt der erste Satz sofort?), "
    "emotion, surprise (Wendung/Überraschung) und completeness (versteht man den Ausschnitt "
    "ohne Kontext, endet er sauber?). Wähle die besten Ausschnitte aus, die sich zeitlich nicht "
    "überschneiden, und gib jedem einen kurzen, neugierig machenden Titel (max. 8 Wörter) in der "
    "Sprache des Videos. Antworte nur mit den ausgewählten Ausschnitten."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "clips": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "hook": {"type": "integer"},
                    "emotion": {"type": "integer"},
                    "surprise": {"type": "integer"},
                    "completeness": {"type": "integer"},
                    "title": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["id", "hook", "emotion", "surprise", "completeness", "title", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["clips"],
    "additionalProperties": False,
}


@dataclass
class RankResult:
    highlights: list[Highlight]
    cost_usd: float


def estimate_cost(model: str, n_candidates: int) -> float:
    pin, pout = PRICES.get(model, PRICES["claude-opus-5-5"])
    # grob: ~250 Token pro Kandidat + 600 System/Overhead, ~3000 Ausgabe inkl. Denken
    return ((n_candidates * 250 + 600) * pin + 3000 * pout) / 1_000_000


def build_prompt(cands: list[Highlight], n_min: int, n_max: int) -> str:
    lines = [f"Wähle {n_min} bis {n_max} Ausschnitte.", ""]
    for i, c in enumerate(cands):
        text = c.text if len(c.text) <= 1200 else c.text[:1200] + " …"
        lines.append(f"[{i}] {c.start:.1f}s–{c.end:.1f}s ({c.duration:.0f}s): {text}")
    return "\n".join(lines)


def rank_with_claude(cands: list[Highlight], n_min: int, n_max: int, api_key: str, model: str,
                     client=None) -> RankResult:
    """Gibt die von Claude gewählten Clips zurück. Wirft Exception bei Fehlern (Aufrufer fällt
    dann auf die Heuristik zurück)."""
    import anthropic

    client = client or anthropic.Anthropic(api_key=api_key, timeout=180.0, max_retries=2)
    response = client.beta.messages.create(
        model=model,
        max_tokens=16000,
        system=SYSTEM,
        messages=[{"role": "user", "content": build_prompt(cands, n_min, n_max)}],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    pin, pout = PRICES.get(model, PRICES["claude-opus-5-5"])
    usage = getattr(response, "usage", None)
    cost = 0.0
    if usage is not None:
        cost = (getattr(usage, "input_tokens", 0) * pin + getattr(usage, "output_tokens", 0) * pout) / 1e6
    if response.stop_reason != "end_turn":
        raise RuntimeError(f"Claude-Antwort unvollständig (stop_reason={response.stop_reason})")
    text = next(b.text for b in response.content if b.type == "text")
    data = json.loads(text)

    chosen: list[Highlight] = []
    for item in data.get("clips", []):
        idx = item.get("id")
        if not isinstance(idx, int) or not 0 <= idx < len(cands):
            continue
        c = cands[idx]
        if any(min(c.end, o.end) > max(c.start, o.start) for o in chosen):
            continue
        parts = {k: max(0, min(10, int(item.get(k, 0)))) / 10 for k in
                 ("hook", "emotion", "surprise", "completeness")}
        score = 0.3 * parts["hook"] + 0.25 * parts["emotion"] + 0.2 * parts["surprise"] + 0.25 * parts["completeness"]
        chosen.append(Highlight(start=c.start, end=c.end, score=score, text=c.text,
                                title=str(item.get("title", ""))[:80],
                                reasons={**parts, "ai": str(item.get("reason", ""))[:300]}))
        if len(chosen) >= n_max:
            break
    if not chosen:
        raise RuntimeError("Claude hat keine gültigen Clips geliefert")
    return RankResult(highlights=sorted(chosen, key=lambda h: h.start), cost_usd=cost)
