"""Datenmodell für Transkripte und Spracherkennung (OpenAI Whisper API)."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import httpx

from app.errors import UserError

OPENAI_URL = "https://api.openai.com/v1/audio/transcriptions"


@dataclass
class Word:
    text: str
    start: float
    end: float


@dataclass
class Segment:
    text: str
    start: float
    end: float
    words: list[Word] = field(default_factory=list)


@dataclass
class Transcript:
    segments: list[Segment] = field(default_factory=list)

    @property
    def words(self) -> list[Word]:
        return [w for s in self.segments for w in s.words]

    @property
    def empty(self) -> bool:
        return not self.segments

    def to_json(self) -> str:
        return json.dumps({"segments": [asdict(s) for s in self.segments]}, ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict) -> "Transcript":
        segs = []
        for s in data.get("segments", []):
            words = [Word(**w) for w in s.get("words", [])]
            segs.append(Segment(text=s["text"], start=float(s["start"]), end=float(s["end"]), words=words))
        return cls(segments=segs)


def build_from_openai(payload: dict, offset: float = 0.0) -> Transcript:
    """Ordnet die Wörter aus der Whisper-Antwort den Satz-Segmenten zu."""
    words = [Word(text=str(w.get("word", "")).strip(), start=float(w["start"]) + offset,
                  end=float(w["end"]) + offset) for w in payload.get("words", []) if str(w.get("word", "")).strip()]
    segments: list[Segment] = []
    for s in payload.get("segments", []):
        segments.append(Segment(text=str(s.get("text", "")).strip(),
                                start=float(s["start"]) + offset, end=float(s["end"]) + offset))
    if not segments and words:
        segments = [Segment(text=" ".join(w.text for w in words), start=words[0].start, end=words[-1].end)]
    wi = 0
    for idx, seg in enumerate(segments):
        nxt = segments[idx + 1].start if idx + 1 < len(segments) else float("inf")
        while wi < len(words) and words[wi].start < nxt - 0.01:
            seg.words.append(words[wi])
            wi += 1
    segments = [s for s in segments if s.words]
    for s in segments:  # Segmentgrenzen an echte Wortzeiten anpassen
        s.start, s.end = s.words[0].start, s.words[-1].end
    return Transcript(segments=segments)


def transcribe_openai(chunks: list[tuple[Path, float]], api_key: str, language: str,
                      client: httpx.Client | None = None) -> Transcript:
    own = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(300.0, connect=20.0))
    result = Transcript()
    try:
        for path, offset in chunks:
            with open(path, "rb") as fh:
                resp = client.post(
                    OPENAI_URL,
                    headers={"Authorization": f"Bearer {api_key}"},
                    data={"model": "whisper-1", "response_format": "verbose_json",
                          "timestamp_granularities[]": ["word", "segment"], "language": language},
                    files={"file": (path.name, fh, "audio/mpeg")},
                )
            if resp.status_code == 401:
                raise UserError("Der OpenAI-API-Schlüssel ist ungültig. Bitte in Render prüfen.")
            if resp.status_code == 429:
                raise UserError("OpenAI meldet ein Limit (zu viele Anfragen oder kein Guthaben).")
            if resp.status_code >= 400:
                raise UserError(f"Die Spracherkennung ist fehlgeschlagen (Fehler {resp.status_code}).")
            part = build_from_openai(resp.json(), offset)
            result.segments.extend(part.segments)
    except httpx.HTTPError as exc:
        raise UserError("Keine Verbindung zur Spracherkennung (OpenAI). Bitte später erneut versuchen.") from exc
    finally:
        if own:
            client.close()
    return result
