"""9:16-Bildausschnitt: Gesicht im Bild halten, sonst mittiger Ausschnitt."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.pipeline.media import sample_frames

log = logging.getLogger(__name__)

TARGET_W, TARGET_H = 1080, 1920


@dataclass
class Focus:
    x: float = 0.5  # 0..1 relativ zur Bildbreite
    y: float = 0.4
    found: bool = False


def _even(v: float) -> int:
    return max(2, int(v) // 2 * 2)


def crop_box(src_w: int, src_h: int, focus: Focus, zoom: float = 1.0) -> tuple[int, int, int, int]:
    """Berechnet (Breite, Höhe, x, y) eines 9:16-Ausschnitts, der den Fokuspunkt enthält."""
    aspect = TARGET_W / TARGET_H
    if src_w / src_h > aspect:
        ch, cw = src_h, src_h * aspect
    else:
        cw, ch = src_w, src_w / aspect
    cw, ch = _even(cw / zoom), _even(ch / zoom)
    cw, ch = min(cw, _even(src_w)), min(ch, _even(src_h))
    x = focus.x * src_w - cw / 2
    y = focus.y * src_h - ch * 0.4  # Gesicht im oberen Drittel
    x = int(max(0, min(src_w - cw, x)))
    y = int(max(0, min(src_h - ch, y)))
    return cw, ch, x, y


_cascade = None


def _detector():
    global _cascade
    if _cascade is None:
        try:
            import cv2
            path = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
            c = cv2.CascadeClassifier(str(path))
            _cascade = c if not c.empty() else False
        except Exception as exc:  # OpenCV fehlt → Fallback Mitte
            log.warning("Gesichtserkennung nicht verfügbar: %s", exc)
            _cascade = False
    return _cascade or None


def detect_faces(src: Path, start: float, end: float, src_w: int, src_h: int,
                 fps: float = 2.0) -> list[tuple[float, float, float]]:
    """Liste von (Zeit, x, y) – normierte Mittelpunkte des größten Gesichts je Bild."""
    det = _detector()
    if det is None:
        return []
    out = []
    for t, frame in sample_frames(src, start, end, fps, src_w, src_h):
        faces = det.detectMultiScale(frame, scaleFactor=1.15, minNeighbors=5, minSize=(24, 24))
        if len(faces) == 0:
            continue
        x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
        fh, fw = frame.shape[:2]
        out.append((t, (x + w / 2) / fw, (y + h / 2) / fh))
    return out


def focus_for(faces: list[tuple[float, float, float]], start: float, end: float, fallback: Focus) -> Focus:
    pts = [(x, y) for t, x, y in faces if start - 0.5 <= t <= end + 0.5]
    if not pts:
        return fallback
    arr = np.array(pts)
    return Focus(x=float(np.median(arr[:, 0])), y=float(np.median(arr[:, 1])), found=True)


def overall_focus(faces: list[tuple[float, float, float]]) -> Focus:
    if not faces:
        return Focus()
    arr = np.array([(x, y) for _, x, y in faces])
    return Focus(x=float(np.median(arr[:, 0])), y=float(np.median(arr[:, 1])), found=True)
