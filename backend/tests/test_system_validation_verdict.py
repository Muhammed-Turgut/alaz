"""Unit checks for system_validation helpers (no archive required)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.analytics.reference_comparison import (
    compare_to_viirs_reference,
    fit_monthly_postcal,
    harm_dampen_factor,
)
from src.analytics.system_validation import _verdict_block


def test_verdict_uses_calibrated_series_for_beats_flags():
    metrics = {
        "n": 12,
        "n_test_months": 12,
        "mae_raw": 10.0,
        "mae_harmonized": 40.0,  # uncalibrated loses
        "mae_harmonized_damped": 8.0,
        "mae_harmonized_blend": 5.0,
        "mae_harmonized_calibrated": 5.0,
        "mae_platform_scaled": 6.0,
        "rmse_raw": 12.0,
        "rmse_harmonized": 50.0,
        "rmse_harmonized_calibrated": 6.0,
        "rmse_platform_scaled": 7.0,
        "platform_scale": 2.0,
        "harm_alpha": 0.3,
        "blend_w": 0.4,
        "platform_scale_fit": "calib_through_2019-12-01",
        "postcal_fit": "calib_through_2019-12-01",
    }
    v = _verdict_block(metrics)
    assert v["ok"] is True
    assert v["primary_series"] == "harmonized_blend"
    assert v["harmonized_beats_raw_mae"] is True
    assert v["harmonized_beats_platform_baseline_mae"] is True
    assert v["mae_harmonized_uncalibrated"] == 40.0
    assert v["mae_harmonized_calibrated"] == 5.0


def test_holdout_metrics_do_not_fit_scale_on_test():
    months = pd.date_range("2018-01-01", periods=24, freq="MS")
    # Calib: MODIS half of VIIRS → scale 2; test: different ratio
    ref = np.array([4.0] * 12 + [10.0] * 12)
    raw = np.array([2.0] * 12 + [10.0] * 12)
    har = raw * 1.5  # dummy
    m = compare_to_viirs_reference(ref, raw, har, months=months, calib_end="2018-12-01")
    assert abs(m["platform_scale"] - 2.0) < 1e-9
    assert m["n_test_months"] == 12
    assert m["platform_scale_fit"].startswith("calib_through_")
    assert "mae_harmonized_blend" in m
    assert "harm_alpha" in m


def test_postcal_alpha_and_blend_fit_only_on_calib_slice():
    # Calib: har overshoots 4x → MAE-optimal alpha ~0.25
    ref_c = np.array([4.0, 8.0, 2.0, 6.0])
    har_c = np.array([16.0, 32.0, 8.0, 24.0])
    raw_c = np.array([2.0, 4.0, 1.0, 3.0])
    post = fit_monthly_postcal(ref_c, raw_c, har_c)
    assert abs(post["platform_scale"] - 2.0) < 1e-9
    assert abs(post["harm_alpha"] - 0.25) < 0.05
    assert "harm_cap_k" in post
    assert 0.0 <= post["blend_w"] <= 1.0


def test_postcal_can_beat_platform_when_harm_shape_helps():
    """On calib, harm tracks ref shape better than raw×scale after dampening."""
    ref = np.array([10.0, 2.0, 8.0, 1.0, 12.0, 0.0])
    raw = np.array([5.0, 5.0, 5.0, 5.0, 5.0, 0.0])  # flat → weak platform baseline
    har = np.array([40.0, 8.0, 32.0, 4.0, 48.0, 0.0])  # 4× ref shape
    months = pd.date_range("2018-01-01", periods=12, freq="MS")
    # pad test half with same pattern so split works
    ref2 = np.concatenate([ref, ref])
    raw2 = np.concatenate([raw, raw])
    har2 = np.concatenate([har, har])
    m = compare_to_viirs_reference(ref2, raw2, har2, months=months, calib_end="2018-06-01")
    assert m["mae_harmonized_calibrated"] <= m["mae_platform_scaled"] + 1e-9


def test_dampen_fixes_mean_overshoot():
    ref = np.array([10.0, 20.0])
    har = np.array([40.0, 80.0])
    a = harm_dampen_factor(ref, har)
    assert abs(a - 0.25) < 1e-9


def test_fixed_postcal_does_not_refit():
    months = pd.date_range("2018-01-01", periods=24, freq="MS")
    ref = np.array([4.0] * 12 + [10.0] * 12)
    raw = np.array([2.0] * 12 + [8.0] * 12)
    har = raw * 5.0
    fixed = {
        "platform_scale": 2.0,
        "harm_alpha": 0.2,
        "harm_cap_k": 1.5,
        "blend_w": 0.5,
        "fit_label": "fixed_from_primary_roi_calib",
    }
    m = compare_to_viirs_reference(
        ref, raw, har, months=months, calib_end="2018-12-01", fixed_postcal=fixed,
    )
    assert m["platform_scale"] == 2.0
    assert m["harm_alpha"] == 0.2
    assert m["harm_cap_k"] == 1.5
    assert m["blend_w"] == 0.5
    assert m["postcal_fit"] == "fixed_from_primary_roi_calib"
