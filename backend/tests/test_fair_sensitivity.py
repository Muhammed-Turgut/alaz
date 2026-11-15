"""Fair confidence sensitivity: fixed VIIRS + keep covered zero months."""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.engine.confidence_policies import (
    FIXED_VIIRS_POLICY,
    _series_closeness,
    confidence_filter_sensitivity,
    fair_policy_mask,
    modis_mask,
    viirs_mask,
)


def _row(ts, instrument, conf, **kw):
    base = dict(
        latitude=37.0, longitude=38.0, ts=ts, satellite="Terra",
        instrument=instrument, scan=1.0, track=1.0, frp=5.0, confidence=conf,
        bright_t21=330, bright_t31=290, daynight="D", is_static=False,
    )
    base.update(kw)
    return base


def test_fair_mask_keeps_viirs_fixed_when_modis_policy_changes():
    df = pd.DataFrame([
        _row("2020-06-01", "MODIS", 40),
        _row("2020-06-01", "MODIS", 85),
        _row("2020-06-01", "VIIRS", 30, satellite="SNPP"),   # low — out under A
        _row("2020-06-01", "VIIRS", 65, satellite="NOAA20"),
        _row("2020-06-01", "VIIRS", 90, satellite="NOAA20"),
    ])
    a = fair_policy_mask(df, modis_policy="A", viirs_policy="A")
    b = fair_policy_mask(df, modis_policy="B", viirs_policy="A")
    c = fair_policy_mask(df, modis_policy="C", viirs_policy="A")
    # VIIRS rows identical across A/B/C
    is_v = (df["instrument"] == "VIIRS").to_numpy()
    assert np.array_equal(a[is_v], b[is_v])
    assert np.array_equal(a[is_v], c[is_v])
    # MODIS: B drops mid-confidence 40
    is_m = ~is_v
    assert a[is_m].tolist() == [True, True]
    assert b[is_m].tolist() == [False, True]
    assert c[is_m].tolist() == [True, True]


def test_sensitivity_viirs_reference_unchanged_across_policies():
    parts = []
    for m in range(1, 10):
        parts.append(pd.DataFrame([
            _row(f"2020-{m:02d}-10", "MODIS", 25),
            _row(f"2020-{m:02d}-10", "MODIS", 50),
            _row(f"2020-{m:02d}-10", "MODIS", 90),
            _row(f"2020-{m:02d}-12", "VIIRS", 30, satellite="SNPP"),
            _row(f"2020-{m:02d}-12", "VIIRS", 65, satellite="NOAA20"),
            _row(f"2020-{m:02d}-12", "VIIRS", 90, satellite="NOAA20"),
        ]))
    # Insert a quiet month (Aug) — no rows → continuous calendar fills zeros
    df = pd.concat([p for i, p in enumerate(parts) if i != 7], ignore_index=True)
    sens = confidence_filter_sensitivity(df)
    assert sens["fair_comparison"] is True
    assert sens["viirs_policy_fixed"] == FIXED_VIIRS_POLICY
    assert sens["metric_label"] == "inter_sensor_agreement"
    for p in sens["policies"]:
        assert p["viirs_reference_unchanged"] is True
        assert p["viirs_policy_fixed"] == "A"
        assert p["modis_policy"] == p["id"]
        close = p["vs_fixed_viirs_reference"]
        assert close["n_months"] >= 8
        # Quiet Aug kept as both-zero
        assert close.get("n_both_zero_kept", 0) >= 1


def test_series_closeness_keeps_both_zero_by_default():
    idx = pd.date_range("2020-01-01", periods=3, freq="MS")
    ref = pd.Series([2.0, 0.0, 1.0], index=idx)
    cand = pd.Series([1.0, 0.0, 1.0], index=idx)
    kept = _series_closeness(ref, cand, drop_both_zero=False)
    dropped = _series_closeness(ref, cand, drop_both_zero=True)
    assert kept["n_months"] == 3
    assert kept["n_both_zero_kept"] == 1
    assert dropped["n_months"] == 2
    assert dropped["n_both_zero_kept"] == 0


def test_modis_and_viirs_masks_are_disjoint():
    df = pd.DataFrame([
        _row("2020-06-01", "MODIS", 50),
        _row("2020-06-01", "VIIRS", 65, satellite="NOAA20"),
    ])
    m = modis_mask(df, "A")
    v = viirs_mask(df, "A")
    assert not (m & v).any()
    assert (m | v).all()
