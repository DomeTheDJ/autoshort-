from app.pipeline import render
from app.pipeline.editing import Interval
from app.pipeline.reframe import Focus, crop_box


def test_crop_landscape_is_9_16_and_inside():
    cw, ch, x, y = crop_box(1920, 1080, Focus(0.9, 0.5))
    assert ch == 1080 and abs(cw / ch - 9 / 16) < 0.01
    assert 0 <= x <= 1920 - cw and x == 1920 - cw  # ganz rechts, aber im Bild


def test_crop_center_fallback_and_zoom():
    cw, ch, x, y = crop_box(1920, 1080, Focus())
    assert abs((x + cw / 2) - 960) <= 2
    zw, zh, _, _ = crop_box(1920, 1080, Focus(), zoom=1.12)
    assert zw < cw and zh < ch


def test_crop_portrait_source():
    cw, ch, x, y = crop_box(1080, 1920, Focus())
    assert (cw, ch, x, y) == (1080, 1920, 0, 0)


def test_pieces_alternate_zoom_and_sfx_are_sparse():
    pieces = render.plan_pieces([Interval(0, 12), Interval(14, 30)], [Focus(), Focus()])
    zooms = [p.zoom for p in pieces]
    assert 1.0 in zooms and render.ZOOM_IN in zooms
    assert all(a != b for a, b in zip(zooms, zooms[1:]))
    assert pieces[0].hard_cut is False and any(p.hard_cut for p in pieces)
    times = render.sfx_times(pieces)
    assert all(b - a >= render.SFX_MIN_GAP - 1e-6 for a, b in zip(times, times[1:]))
