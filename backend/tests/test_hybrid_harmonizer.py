"""Hybrid active-cell α engine unit tests."""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.engine.hybrid_harmonizer import (
    DEFAULT_FALLBACK_ALPHA,
    assign_grid,
    build_harmonized_monthly,
    fit_alpha,
    hygiene_mask,
    monthly_to_csv,
    run_hybrid,
)


def _row(ts, lat, lon, instrument, conf, frp=5.0, **kw):
    r = dict(
        latitude=lat, longitude=lon, ts=ts, satellite="Terra",
        instrument=instrument, frp=frp, confidence=conf,
        conf_ok=True, is_static=False, scan=1.0, track=1.0,
        bright_t21=330, bright_t31=290, daynight="D",
    )
    r.update(kw)
    return r


def test_hygiene_drops_low_confidence_and_bad_coords():
    df = pd.DataFrame([
        _row("2015-06-01", 37.0, 31.0, "MODIS", 40),
        _row("2015-06-01", 37.0, 31.0, "MODIS", 20, conf_ok=False),
        _row("2015-06-01", np.nan, 31.0, "VIIRS", 65),
        _row("2015-06-01", 37.1, 31.1, "VIIRS", 65),
    ])
    m = hygiene_mask(df)
    assert m.tolist() == [True, False, False, True]


def test_fit_alpha_through_origin_and_fallback():
    months = pd.date_range("2015-01-01", periods=30, freq="MS")
    # Y ≈ 2 X
    monthly = pd.DataFrame({
        "month": months,
        "MODIS": np.full(30, 10.0),
        "VIIRS": np.full(30, 20.0),
        "raw_modis_det": 10,
        "raw_viirs_det": 20,
    })
    info = fit_alpha(monthly)
    assert not info["fallback_used"]
    assert abs(info["value"] - 2.0) < 0.05
    assert info["n_months"] >= 20

    # Too few months → fallback
    small = monthly.iloc[:5]
    fb = fit_alpha(small, fallback_alpha=3.0)
    assert fb["fallback_used"]
    assert fb["value"] == 3.0


def test_fit_alpha_out_of_bounds_falls_back():
    months = pd.date_range("2015-01-01", periods=25, freq="MS")
    monthly = pd.DataFrame({
        "month": months,
        "MODIS": np.full(25, 1.0),
        "VIIRS": np.full(25, 50.0),  # α=50 → out of bounds
        "raw_modis_det": 1,
        "raw_viirs_det": 50,
    })
    info = fit_alpha(monthly, fallback_alpha=DEFAULT_FALLBACK_ALPHA)
    assert info["fallback_used"]
    assert info["value"] == DEFAULT_FALLBACK_ALPHA


def test_pre_viirs_scaled_post_viirs_native():
    monthly = pd.DataFrame({
        "month": pd.to_datetime(["2010-07-01", "2015-07-01"]),
        "MODIS": [10.0, 4.0],
        "VIIRS": [0.0, 12.0],
        "raw_modis_det": [10, 4],
        "raw_viirs_det": [0, 12],
    })
    h = build_harmonized_monthly(monthly, alpha=2.0)
    assert h.loc[0, "harmonized_activity"] == 20.0  # 10 * 2
    assert h.loc[1, "harmonized_activity"] == 12.0  # VIIRS native


def test_run_hybrid_end_to_end_and_csv_columns():
    rows = []
    # Build 24+ overlap months with MODIS+VIIRS in nearby cells
    for i in range(24):
        ts = pd.Timestamp("2015-01-15") + pd.DateOffset(months=i)
        rows.append(_row(ts, 36.8, 31.4, "MODIS", 50, frp=3.0))
        rows.append(_row(ts, 36.81, 31.41, "MODIS", 60, frp=4.0))
        rows.append(_row(ts, 36.8, 31.4, "VIIRS", 65, frp=2.0, satellite="SNPP"))
        rows.append(_row(ts, 36.805, 31.405, "VIIRS", 90, frp=2.5, satellite="NOAA20"))
        rows.append(_row(ts, 36.81, 31.41, "VIIRS", 65, frp=1.0, satellite="SNPP"))
    # Pre-VIIRS months
    for i in range(6):
        ts = pd.Timestamp("2010-01-15") + pd.DateOffset(months=i)
        rows.append(_row(ts, 36.8, 31.4, "MODIS", 55, frp=5.0))
    df = pd.DataFrame(rows)
    df["grid_id"] = assign_grid(df, grid="h3")
    pack = run_hybrid(df, grid="h3")
    assert not pack["empty"]
    assert "alpha" in pack
    assert len(pack["monthly"]) >= 20
    row0 = pack["monthly"][0]
    for k in (
        "year", "month", "raw_modis", "raw_viirs", "applied_alpha",
        "harmonized_activity", "historical_median", "anomaly_pct",
    ):
        assert k in row0
    csv = monthly_to_csv(pack["monthly"])
    assert csv.startswith("year,month,raw_modis")
    assert pack["map"]["harmonized_cells"] or pack["map"]["viirs_points"]
