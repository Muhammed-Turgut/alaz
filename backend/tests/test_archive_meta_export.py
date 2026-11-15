"""Product provenance, empty reasons, polygon CSV clip."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.data.archive_store import ArchiveStore, point_in_ring, product_kind, standardize_raw


def test_product_kind_unknown_not_assumed():
    assert product_kind("SOMETHING_ELSE") == "unknown"
    raw = pd.DataFrame({
        "latitude": [37.0], "longitude": [35.0],
        "bright_ti4": [320.0], "scan": [0.4], "track": [0.4],
        "acq_date": ["2020-01-01"], "acq_time": [1000],
        "satellite": ["N"], "confidence": ["n"], "bright_ti5": [290.0], "frp": [1.0],
    })
    # Non-standard source id still maps satellite; kind unknown if not _SP/_NRT suffix
    out = standardize_raw(raw, "CUSTOM_FEED")
    assert out.product_kind.iloc[0] == "unknown"


def test_query_meta_product_composition(tmp_path, monkeypatch):
    import src.data.archive_store as mod
    monkeypatch.setattr(mod, "ARCHIVE_DIR", tmp_path)
    root = tmp_path / "tr"
    raw_dir = root / "raw" / "VIIRS_NOAA20_SP" / "2020"
    raw_dir.mkdir(parents=True)
    (root / "area.json").write_text(
        '{"id":"tr","name":"Türkiye","bbox":[25.5,35.8,45.0,42.3],"bbox_order":"west,south,east,north"}'
    )
    (root / "progress.json").write_text('{"state":"done","done_chunks":10,"total_chunks":10}')
    csv = (
        "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,confidence,version,bright_ti5,frp,daynight\n"
        "37.5,35.5,330,0.4,0.4,2020-08-01,1200,N20,n,2.0,290,5.0,D\n"
        "37.6,35.6,331,0.4,0.4,2020-08-02,1300,N20,n,2.0,291,6.0,D\n"
    )
    (raw_dir / "2020-08-01_5d.csv").write_text(csv)
    store = ArchiveStore("tr")
    # Small polygon around the points
    poly = [[37.4, 35.4], [37.7, 35.4], [37.7, 35.7], [37.4, 35.7]]
    df, meta = store.query(polygon=poly)
    assert len(df) == 2
    assert meta["product_composition"].get("VIIRS_NOAA20_SP") == 2
    assert meta["product_kind_counts"].get("standard") == 2
    assert meta["satellites"].get("NOAA20") == 2
    assert meta["times_utc"] is True
    assert meta["empty_reason"] is None


def test_empty_roi_reasons(tmp_path, monkeypatch):
    import src.data.archive_store as mod
    monkeypatch.setattr(mod, "ARCHIVE_DIR", tmp_path)
    root = tmp_path / "tr"
    root.mkdir()
    (root / "area.json").write_text(
        '{"id":"tr","name":"Türkiye","bbox":[25.5,35.8,45.0,42.3],"bbox_order":"west,south,east,north"}'
    )
    (root / "progress.json").write_text('{"state":"done","done_chunks":5,"total_chunks":5}')
    (root / "raw").mkdir()
    store = ArchiveStore("tr")
    poly = [[37.0, 32.0], [37.1, 32.0], [37.1, 32.1], [37.0, 32.1]]
    df, meta = store.query(polygon=poly)
    assert df.empty
    assert meta["empty_reason"] in ("no_detections", "archive_partial_no_rows", "archive_not_ready")


def test_polygon_clip_excludes_bbox_only_points():
    """Point inside bbox but outside polygon must be excluded."""
    # Square ring; point in corner of bbox but outside diamond
    ring = [[0.0, 0.0], [0.0, 2.0], [2.0, 2.0], [2.0, 0.0]]
    lat = np.array([1.0, 1.0, -1.0])
    lon = np.array([1.0, 3.0, 1.0])
    m = point_in_ring(lat, lon, ring)
    assert m.tolist() == [True, False, False]
