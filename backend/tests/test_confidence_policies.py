"""Confidence filter policies A/B/C and VIIRS-closeness sensitivity."""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.engine.confidence_policies import (
    PRIMARY_POLICY,
    apply_policy,
    confidence_filter_sensitivity,
    policy_current,
    policy_loose,
    policy_strict,
)
from src.engine.prepare import confidence_ok, prepare


def _df():
    # Mixed MODIS numeric + VIIRS class-mapped scores (as after standardize)
    rows = [
        # MODIS: 20 low, 40 mid, 85 high
        dict(latitude=37.0, longitude=38.0, ts="2020-06-01", satellite="Terra",
             instrument="MODIS", scan=1.0, track=1.0, frp=5.0, confidence=20,
             bright_t21=330, bright_t31=290, daynight="D", is_static=False),
        dict(latitude=37.0, longitude=38.0, ts="2020-06-01", satellite="Aqua",
             instrument="MODIS", scan=1.0, track=1.0, frp=5.0, confidence=40,
             bright_t21=330, bright_t31=290, daynight="D", is_static=False),
        dict(latitude=37.0, longitude=38.0, ts="2020-06-01", satellite="Terra",
             instrument="MODIS", scan=1.0, track=1.0, frp=5.0, confidence=85,
             bright_t21=330, bright_t31=290, daynight="D", is_static=False),
        # VIIRS mapped: low=30, nominal=65, high=90
        dict(latitude=37.1, longitude=38.1, ts="2020-06-01", satellite="SNPP",
             instrument="VIIRS", scan=0.4, track=0.4, frp=4.0, confidence=30,
             bright_t21=330, bright_t31=290, daynight="D", is_static=False),
        dict(latitude=37.1, longitude=38.1, ts="2020-06-01", satellite="NOAA20",
             instrument="VIIRS", scan=0.4, track=0.4, frp=4.0, confidence=65,
             bright_t21=330, bright_t31=290, daynight="D", is_static=False),
        dict(latitude=37.1, longitude=38.1, ts="2020-06-01", satellite="NOAA20",
             instrument="VIIRS", scan=0.4, track=0.4, frp=4.0, confidence=90,
             bright_t21=330, bright_t31=290, daynight="D", is_static=False),
    ]
    return pd.DataFrame(rows)


def test_policy_a_matches_prepare_confidence_ok():
    df = _df()
    a = policy_current(df)
    assert np.array_equal(a, confidence_ok(df))
    # MODIS 20 out; 40 and 85 in. VIIRS 30 out; 65 and 90 in.
    assert a.tolist() == [False, True, True, False, True, True]


def test_policy_b_strict():
    df = _df()
    b = policy_strict(df)
    # MODIS only >=80; VIIRS only >=90
    assert b.tolist() == [False, False, True, False, False, True]


def test_policy_c_loose_keeps_all_valid():
    df = _df()
    c = policy_loose(df)
    assert c.all()


def test_primary_is_a_and_not_auto_selected():
    assert PRIMARY_POLICY == "A"
    # Build a few months so sensitivity has n_months
    parts = []
    for m in range(1, 8):
        d = _df().copy()
        d["ts"] = pd.Timestamp(f"2020-{m:02d}-15")
        parts.append(d)
    df = pd.concat(parts, ignore_index=True)
    sens = confidence_filter_sensitivity(df)
    assert sens["primary_policy"] == "A"
    assert sens["auto_select_best"] is False
    assert sens["primary_used_in_main_result"] is True
    assert sens.get("fair_comparison") is True
    assert sens.get("viirs_policy_fixed") == "A"
    assert len(sens["policies"]) == 3
    ids = [p["id"] for p in sens["policies"]]
    assert ids == ["A", "B", "C"]
    assert all(p.get("viirs_reference_unchanged") for p in sens["policies"])
    # Diagnostic closest may be any of A/B/C — must not change primary
    assert sens["closest_by_rmse_to_viirs"] in ("A", "B", "C", None)
    note = sens["selection_note"].lower()
    assert "diagnostic" in note or "fixed" in note or "cherry-pick" in note
    scale = sens["scale_note"].lower()
    assert "ordinal" in scale or "not the same" in scale or "0–100" in scale or "0-100" in scale


def test_apply_policy_aliases():
    df = _df()
    assert np.array_equal(apply_policy(df, "current"), apply_policy(df, "A"))
    assert np.array_equal(apply_policy(df, "strict"), apply_policy(df, "B"))
    assert np.array_equal(apply_policy(df, "loose"), apply_policy(df, "C"))


def test_prepare_tags_policy_a():
    raw = _df().drop(columns=["is_static"])
    out = prepare(raw)
    assert (out["conf_policy"] == "A").all()
    assert np.array_equal(out["conf_ok"].to_numpy(), confidence_ok(out))
