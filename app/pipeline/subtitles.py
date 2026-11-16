"""Erzeugt ASS-Untertitel mit Wort-für-Wort-Hervorhebung (für libass/FFmpeg)."""
from __future__ import annotations

from app.pipeline.transcript import Word

HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},86,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,6,2,2,70,70,520,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

HIGHLIGHT = r"{\c&H0000E5FF&\fscx112\fscy112}"  # gelb-orange (BGR), leicht größer
RESET = r"{\r}"


def ass_time(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def escape(text: str) -> str:
    return (text.replace("\\", "/").replace("{", "(").replace("}", ")")
            .replace("\n", " ").strip())


def group_words(words: list[Word], max_words: int = 3, max_chars: int = 18, max_gap: float = 0.5) -> list[list[Word]]:
    groups: list[list[Word]] = []
    cur: list[Word] = []
    for w in words:
        chars = sum(len(x.text) + 1 for x in cur) + len(w.text)
        if cur and (len(cur) >= max_words or chars > max_chars or w.start - cur[-1].end > max_gap
                    or cur[-1].text.rstrip()[-1:] in ".!?,"):
            groups.append(cur)
            cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    return groups


def build_ass(words: list[Word], font: str = "DejaVu Sans", uppercase: bool = True) -> str:
    events: list[tuple[float, float, str]] = []
    for group in group_words(words):
        texts = [escape(w.text.upper() if uppercase else w.text) for w in group]
        for i, w in enumerate(group):
            end = group[i + 1].start if i + 1 < len(group) else w.end + 0.15
            parts = [f"{HIGHLIGHT}{t}{RESET}" if j == i else t for j, t in enumerate(texts)]
            events.append((w.start, end, " ".join(parts)))
    lines = [HEADER.format(font=font)]
    for k, (start, end, text) in enumerate(events):
        if k + 1 < len(events):
            end = min(end, events[k + 1][0])  # nie zwei Zeilen gleichzeitig
        end = max(end, start + 0.05)
        lines.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Default,,0,0,0,,{text}")
    return "\n".join(lines) + "\n"
