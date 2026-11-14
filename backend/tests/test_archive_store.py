"""Arşiv okuyucu: alan limiti, kapsama, poligon süzgeci, koordinat korunumu."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.data.archive_store import (
    MAX_AREA_KM2,
    ArchiveStore,
    bbox_area_km2,
    point_in_ring,
    polygon_bbox,
    standardize_raw,
)


def test_bbox_area_and_limit_helpers():
    assert bbox_area_km2(28, 36, 29, 37) < MAX_AREA_KM2
    assert bbox_area_km2(25.5, 35.8, 45.0, 42.3) > MAX_AREA_KM2
    w, s, e, n = polygon_bbox([[36.0, 28.0], [37.0, 28.0], [37.0, 29.0], [36.0, 29.0]])
    assert (w, s, e, n) == (28.0, 36.0, 29.0, 37.0)


def test_point_in_ring_square():
    ring = [[36.0, 28.0], [37.0, 28.0], [37.0, 29.0], [36.0, 29.0]]
    lat = np.array([36.5, 35.0, 36.5])
    lon = np.array([28.5, 28.5, 30.0])
    m = point_in_ring(lat, lon, ring)
    assert m.tolist() == [True, False, False]


def test_standardize_preserves_coords():
    raw = pd.DataFrame({
        "latitude": [37.123456], "longitude": [38.654321],
        "bright_ti4": [330.12], "scan": [0.4], "track": [0.4],
        "acq_date": ["2024-08-01"], "acq_time": [1200],
        "satellite": ["N20"], "instrument": ["VIIRS"], "confidence": ["n"],
        "version": ["2.0NRT"], "bright_ti5": [290.0], "frp": [12.345], "daynight": ["D"],
    })
    out = standardize_raw(raw, "VIIRS_NOAA20_NRT")
    assert len(out) == 1
    assert out.latitude.iloc[0] == pytest.approx(37.123456)
    assert out.longitude.iloc[0] == pytest.approx(38.654321)
    assert out.frp.iloc[0] == pytest.approx(12.345)
    assert out.instrument.iloc[0] == "VIIRS"


def test_store_rejects_oversized_and_outside(tmp_path, monkeypatch):
    import src.data.archive_store as mod
    monkeypatch.setattr(mod, "ARCHIVE_DIR", tmp_path)
    root = tmp_path / "tr"
    root.mkdir()
    (root / "area.json").write_text(
        '{"id":"tr","name":"Türkiye","bbox":[25.5,35.8,45.0,42.3],"bbox_order":"west,south,east,north"}'
    )
    (root / "progress.json").write_text('{"state":"running","done_chunks":1,"total_chunks":10}')
    (root / "raw").mkdir()
    store = ArchiveStore("tr")
    with pytest.raises(ValueError, match="kapsama dışında"):
        store.assert_inside_coverage(20.0, 30.0, 21.0, 31.0)
    with pytest.raises(ValueError, match="çok büyük"):
        store.assert_area_limit(25.5, 35.8, 45.0, 42.3)
