"""Externe Dienste werden hier NUR simuliert (Mocks). Echte API-Aufrufe sind nicht getestet."""
import json
from types import SimpleNamespace

import httpx
import pytest

from app.errors import UserError
from app.pipeline import ai_rank
from app.pipeline.highlights import Highlight
from app.pipeline.transcript import build_from_openai, transcribe_openai

OPENAI_SAMPLE = {
    "text": "Hallo zusammen. Heute zeige ich euch was.",
    "segments": [{"start": 0.0, "end": 1.4, "text": " Hallo zusammen."},
                 {"start": 1.6, "end": 3.9, "text": " Heute zeige ich euch was."}],
    "words": [{"word": "Hallo", "start": 0.0, "end": 0.5}, {"word": "zusammen.", "start": 0.6, "end": 1.4},
              {"word": "Heute", "start": 1.6, "end": 2.0}, {"word": "zeige", "start": 2.1, "end": 2.5},
              {"word": "ich", "start": 2.6, "end": 2.8}, {"word": "euch", "start": 2.9, "end": 3.3},
              {"word": "was.", "start": 3.4, "end": 3.9}],
}


def test_openai_payload_is_mapped_with_offset():
    tr = build_from_openai(OPENAI_SAMPLE, offset=600)
    assert len(tr.segments) == 2
    assert [w.text for w in tr.segments[0].words] == ["Hallo", "zusammen."]
    assert tr.segments[1].start == 601.6 and len(tr.words) == 7


def test_transcribe_openai_with_mock_transport(tmp_path):
    seen = {}

    def handler(request: httpx.Request):
        seen["auth"] = request.headers["authorization"]
        seen["body"] = request.content
        return httpx.Response(200, json=OPENAI_SAMPLE)

    f = tmp_path / "chunk_000.mp3"
    f.write_bytes(b"ID3")
    client = httpx.Client(transport=httpx.MockTransport(handler))
    tr = transcribe_openai([(f, 0.0), (f, 600.0)], "sk-test", "de", client=client)
    assert seen["auth"] == "Bearer sk-test"
    assert b"whisper-1" in seen["body"] and b"word" in seen["body"]
    assert len(tr.segments) == 4 and tr.segments[-1].start == 601.6


def test_transcribe_openai_bad_key_message(tmp_path):
    f = tmp_path / "c.mp3"
    f.write_bytes(b"x")
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401, json={})))
    with pytest.raises(UserError, match="Schlüssel"):
        transcribe_openai([(f, 0.0)], "bad", "de", client=client)


class FakeClaude:
    def __init__(self, payload, stop="end_turn"):
        self.payload, self.stop, self.kwargs = payload, stop, None
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(stop_reason=self.stop,
                               usage=SimpleNamespace(input_tokens=10_000, output_tokens=1_000),
                               content=[SimpleNamespace(type="text", text=json.dumps(self.payload))])


def cands():
    return [Highlight(0, 20, 0.5, "Warum?"), Highlight(10, 30, 0.5, "Überlappt"), Highlight(40, 70, 0.4, "Später.")]


def test_claude_ranking_parses_and_removes_overlap():
    fake = FakeClaude({"clips": [
        {"id": 0, "hook": 9, "emotion": 7, "surprise": 5, "completeness": 8, "title": "Der Trick", "reason": "r"},
        {"id": 1, "hook": 9, "emotion": 9, "surprise": 9, "completeness": 9, "title": "X", "reason": "r"},
        {"id": 2, "hook": 6, "emotion": 6, "surprise": 6, "completeness": 6, "title": "Y", "reason": "r"},
        {"id": 99, "hook": 1, "emotion": 1, "surprise": 1, "completeness": 1, "title": "Z", "reason": "r"},
    ]})
    res = ai_rank.rank_with_claude(cands(), 1, 5, "key", "claude-opus-5-5", client=fake)
    assert [h.title for h in res.highlights] == ["Der Trick", "Y"]
    assert abs(res.cost_usd - (10_000 * 4 + 1_000 * 20) / 1e6) < 1e-9
    assert fake.kwargs["model"] == "claude-opus-5-5"
    assert fake.kwargs["output_config"]["format"]["type"] == "json_schema"


def test_claude_refusal_raises_so_pipeline_falls_back():
    with pytest.raises(RuntimeError):
        ai_rank.rank_with_claude(cands(), 1, 5, "key", "claude-opus-5-5", client=FakeClaude({}, stop="refusal"))
