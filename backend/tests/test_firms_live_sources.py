"""P0: live sources (NOAA-21 in, SNPP out), standardization, sensors calendar."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.data.firms_archive import PRODUCTS, plan_chunks
from src.data.firms_client import (
    LIVE_SOURCES,
    RETIRED_LIVE_SOURCES,
    FirmsClient,
    product_kind,
    standardize,
)
from src.data.archive_store import standardize_raw
from src.engine.pipeline import (
    CALIB_YEARS,
    TEST_YEARS,
    TRAIN_YEARS,
    assert_year_blocks_disjoint,
)
from src.engine.sensors import NOAA21_START, SNPP_END, SENSORS, n_operating


SAMPLE = Path(__file__).resolve().parents[1] / "data" / "samples" / "VIIRS_NOAA21_NRT_sample.csv"


def test_live_sources_exclude_snpp_include_noaa21():
    assert "VIIRS_SNPP_NRT" not in LIVE_SOURCES
    assert "VIIRS_NOAA21_NRT" in LIVE_SOURCES
    assert "VIIRS_NOAA20_NRT" in LIVE_SOURCES
    assert "MODIS_NRT" in LIVE_SOURCES
    assert "VIIRS_SNPP_NRT" in RETIRED_LIVE_SOURCES


def test_noaa21_csv_standardize_satellite():
    assert SAMPLE.exists(), "bundled NOAA-21 sample missing"
    raw = pd.read_csv(SAMPLE)
    out = standardize(raw, "VIIRS_NOAA21_NRT")
    assert len(out) >= 1
    assert set(out["satellite"].unique()) <= {"NOAA21"}
    assert (out["instrument"] == "VIIRS").all()
    assert (out["firms_source"] == "VIIRS_NOAA21_NRT").all()
    assert (out["product_kind"] == "nrt").all()
    assert out["latitude"].iloc[0] == pytest.approx(float(raw["latitude"].iloc[0]))


def test_archive_standardize_noaa21_and_snpp_historical():
    raw21 = pd.DataFrame({
        "latitude": [37.1], "longitude": [38.2],
        "bright_ti4": [330.0], "scan": [0.4], "track": [0.4],
        "acq_date": ["2024-08-01"], "acq_time": [1200],
        "satellite": ["N21"], "instrument": ["VIIRS"], "confidence": ["n"],
        "version": ["2.0NRT"], "bright_ti5": [290.0], "frp": [5.0], "daynight": ["D"],
    })
    out21 = standardize_raw(raw21, "VIIRS_NOAA21_NRT")
    assert out21.satellite.iloc[0] == "NOAA21"
    assert out21.product_kind.iloc[0] == "nrt"

    raw_sp = pd.DataFrame({
        "latitude": [37.1], "longitude": [38.2],
        "bright_ti4": [330.0], "scan": [0.4], "track": [0.4],
        "acq_date": ["2015-08-01"], "acq_time": [1200],
        "satellite": ["N"], "instrument": ["VIIRS"], "confidence": ["n"],
        "version": ["2.0"], "bright_ti5": [290.0], "frp": [5.0], "daynight": ["D"],
    })
    out_sp = standardize_raw(raw_sp, "VIIRS_SNPP_SP")
    assert out_sp.satellite.iloc[0] == "SNPP"
    assert out_sp.product_kind.iloc[0] == "standard"
    assert product_kind("MODIS_SP") == "standard"
    assert product_kind("VIIRS_NOAA20_NRT") == "nrt"
    assert product_kind("") == "unknown"


def test_retired_snpp_not_served_as_live(tmp_path, monkeypatch):
    client = FirmsClient(map_key="")
    # Point cache/sample at empty temp so no accidental success
    monkeypatch.setattr("src.data.firms_client.CACHE_DIR", tmp_path)
    monkeypatch.setattr("src.data.firms_client.SAMPLE_DIR", tmp_path)
    df, meta = client.fetch_source("VIIRS_SNPP_NRT", days=1)
    assert len(df) == 0
    assert meta.tier == "retired"
    assert meta.live is False


def test_fresh_cache_is_not_live(tmp_path, monkeypatch):
    """TTL-fresh disk cache must be tier=cache, live=False with source_fetched_at."""
    import os
    import time
    from src.data.firms_client import _write_cache_sidecar

    monkeypatch.setattr("src.data.firms_client.CACHE_DIR", tmp_path)
    monkeypatch.setattr("src.data.firms_client.SAMPLE_DIR", tmp_path)
    # Block any network fallback
    monkeypatch.setattr(
        "src.data.firms_client.requests.Session.get",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("network should not be used")),
    )
    client = FirmsClient(map_key="", cache_ttl_s=3600)
    cache = client._cache_file("MODIS_NRT", 1, None)
    header = (
        "latitude,longitude,brightness,scan,track,acq_date,acq_time,satellite,instrument,"
        "confidence,version,bright_t31,frp,daynight\n"
    )
    # Cache path requires size > 300 bytes to count as usable
    rows = "".join(
        f"1{i % 10}.0,2{i % 10}.0,330,1,1,2026-11-14,1200,T,MODIS,80,6.1NRT,290,5,D\n"
        for i in range(20)
    )
    cache.write_text(header + rows, encoding="utf-8")
    assert cache.stat().st_size > 300
    _write_cache_sidecar(cache, source_fetched_at="2026-11-14T10:00:00+00:00", tier="open-feed")
    now = time.time()
    os.utime(cache, (now, now))

    df, meta = client.fetch_source("MODIS_NRT", days=1)
    assert len(df) >= 1
    assert meta.tier == "cache"
    assert meta.live is False
    assert meta.source_fetched_at == "2026-11-14T10:00:00+00:00"
    assert meta.retrieved_at
    d = meta.to_dict()
    assert d["live"] is False
    assert d["source_fetched_at"] == "2026-11-14T10:00:00+00:00"


def test_sensors_calendar_snpp_end_and_noaa21():
    assert SENSORS["SNPP"]["end"] == "2026-11-02"
    assert SENSORS["NOAA21"]["start"] == "2024-01-01"
    ts = pd.to_datetime(["2015-06-01", "2019-06-01", "2024-06-01", "2026-11-03"])
    n = n_operating(ts, "VIIRS")
    # 2015: SNPP only → 1; 2019: SNPP+NOAA20 → 2; 2024: all three → 3; after SNPP end: NOAA20+21 → 2
    assert n.tolist() == [1.0, 2.0, 3.0, 2.0]
    assert NOAA21_START == np.datetime64("2024-01-01")
    assert SNPP_END == np.datetime64("2026-11-02")


def test_n_operating_continuity_no_jump_formula():
    """Per-platform weight 1/n keeps sum continuous when a platform joins."""
    ts_before = pd.to_datetime(["2023-12-15"] * 10)
    ts_after = pd.to_datetime(["2024-01-15"] * 15)  # NOAA-21 on
    w_b = 1.0 / n_operating(ts_before, "VIIRS")
    w_a = 1.0 / n_operating(ts_after, "VIIRS")
    # Same number of detections per platform would keep total ≈ constant;
    # here we only check weights are 1/2 then 1/3
    assert np.allclose(w_b, 0.5)
    assert np.allclose(w_a, 1.0 / 3.0)


def test_archive_plan_includes_noaa21():
    today = date(2026, 11, 14)
    avail = {
        "MODIS_SP": (date(2000, 11, 1), date(2026, 8, 31)),
        "MODIS_NRT": (date(2026, 9, 1), date(2026, 11, 14)),
        "VIIRS_SNPP_SP": (date(2012, 1, 20), date(2026, 7, 31)),
        "VIIRS_SNPP_NRT": (date(2026, 8, 1), date(2026, 11, 2)),
        "VIIRS_NOAA20_SP": (date(2018, 4, 1), date(2026, 7, 31)),
        "VIIRS_NOAA20_NRT": (date(2026, 8, 1), date(2026, 11, 14)),
        "VIIRS_NOAA21_SP": (date(2024, 1, 1), date(2026, 7, 31)),
        "VIIRS_NOAA21_NRT": (date(2026, 8, 1), date(2026, 11, 14)),
    }
    assert "VIIRS_NOAA21" in PRODUCTS
    chunks = plan_chunks(avail, today)
    sources = {c.source for c in chunks}
    assert "VIIRS_NOAA21_SP" in sources
    assert "VIIRS_NOAA21_NRT" in sources
    assert "VIIRS_SNPP_SP" in sources  # historical SNPP preserved


def test_synthetic_year_blocks_disjoint():
    assert assert_year_blocks_disjoint()
    with pytest.raises(ValueError, match="overlap"):
        assert_year_blocks_disjoint(train_years=(2012, 2022), calib_years=CALIB_YEARS, test_years=TEST_YEARS)
    # Document that these are synthetic blocks
    assert TRAIN_YEARS[1] < CALIB_YEARS[0]
    assert CALIB_YEARS[1] < TEST_YEARS[0]
