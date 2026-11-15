"""Unit tests: confidence policy A/B/C and VIIRS label mapping.

These tests lock the *filter contract*. Passing them does not scientifically
validate MODIS↔VIIRS harmonization.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.data.archive_store import standardize_raw
from src.engine.confidence_policies import (
    MISSING_CONFIDENCE_DEFAULT,
    PRIMARY_POLICY,
    apply_policy,
    map_viirs_confidence_labels,
    policy_current,
    policy_loose,
    policy_strict,
)
from src.engine.prepare import confidence_ok


def _df(instrument, confidence):
    n = len(confidence)
    return pd.DataFrame({
        "instrument": list(instrument) if not isinstance(instrument, str) else [instrument] * n,
        "confidence": list(confidence),
    })


def test_policy_a_modis_ge_30_viirs_excludes_low():
    """MODIS keeps 30; VIIRS mapped-low (30) is dropped — thresholds are not equivalent."""
    df = _df(
        ["MODIS", "MODIS", "MODIS", "VIIRS", "VIIRS", "VIIRS"],
        [29, 30, 80, 30, 65, 90],
    )
    ok = policy_current(df)
    assert ok.tolist() == [False, True, True, False, True, True]


def test_prepare_confidence_ok_matches_policy_a():
    df = _df(["MODIS", "VIIRS", "VIIRS"], [30, 30, 65])
    assert confidence_ok(df).tolist() == policy_current(df).tolist()
    assert PRIMARY_POLICY == "A"


def test_same_numeric_30_not_equivalent_across_sensors():
    modis = _df("MODIS", [30])
    viirs = _df("VIIRS", [30])
    assert bool(policy_current(modis)[0]) is True
    assert bool(policy_current(viirs)[0]) is False


def test_viirs_label_mapping_low_nominal_high():
    mapped = map_viirs_confidence_labels(["l", "n", "h", "low", "nominal", "high"])
    assert mapped.tolist() == [30, 65, 90, 30, 65, 90]


def test_archive_standardize_maps_viirs_classes():
    raw = pd.DataFrame({
        "latitude": [37.0, 37.1, 37.2],
        "longitude": [38.0, 38.1, 38.2],
        "bright_ti4": [330.0, 331.0, 332.0],
        "scan": [0.4, 0.4, 0.4],
        "track": [0.4, 0.4, 0.4],
        "acq_date": ["2024-08-01"] * 3,
        "acq_time": [1200, 1201, 1202],
        "satellite": ["N20"] * 3,
        "instrument": ["VIIRS"] * 3,
        "confidence": ["l", "n", "h"],
        "version": ["2.0NRT"] * 3,
        "bright_ti5": [290.0, 291.0, 292.0],
        "frp": [1.0, 2.0, 3.0],
        "daynight": ["D"] * 3,
    })
    out = standardize_raw(raw, "VIIRS_NOAA20_NRT")
    assert out["confidence"].tolist() == [30, 65, 90]
    ok = policy_current(out)
    assert ok.tolist() == [False, True, True]


def test_missing_confidence_fills_nominal_default():
    df = _df(["MODIS", "VIIRS"], [np.nan, np.nan])
    # prepare / policies fill missing with 65 → passes A for both
    assert policy_current(df).tolist() == [True, True]
    assert MISSING_CONFIDENCE_DEFAULT == 65.0
    # Strict: MODIS 65 < 80 → drop; VIIRS 65 < 90 → drop
    assert policy_strict(df).tolist() == [False, False]


def test_policy_b_strict():
    df = _df(
        ["MODIS", "MODIS", "VIIRS", "VIIRS", "VIIRS"],
        [79, 80, 65, 89, 90],
    )
    assert policy_strict(df).tolist() == [False, True, False, False, True]


def test_policy_c_loose_keeps_viirs_low_and_valid_modis():
    df = _df(
        ["MODIS", "MODIS", "VIIRS", "VIIRS"],
        [0, 100, 30, 90],
    )
    assert policy_loose(df).tolist() == [True, True, True, True]
    # Invalid MODIS outside [0,100] dropped under loose
    bad = _df("MODIS", [-1, 101])
    assert policy_loose(bad).tolist() == [False, False]


def test_apply_policy_aliases():
    df = _df(["MODIS", "VIIRS"], [50, 65])
    assert apply_policy(df, "A").tolist() == apply_policy(df, "current").tolist()
    assert apply_policy(df, "B").tolist() == apply_policy(df, "strict").tolist()
    with pytest.raises(ValueError, match="unknown"):
        apply_policy(df, "Z")
