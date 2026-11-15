"""Month selection for real-archive VIIRS comparison reference evaluation."""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.analytics.reference_comparison import (
    compare_to_viirs_reference,
    platform_scale_factor,
    product_coverage_table,
    select_evaluation_months,
    temporal_calib_test_split,
)


PRODUCTS = {
    "MODIS_SP": ["2000-11-01", "2026-08-31"],
    "MODIS_NRT": ["2026-09-01", "2026-11-14"],
    "VIIRS_SNPP_SP": ["2012-01-20", "2026-07-31"],
    "VIIRS_NOAA20_SP": ["2018-04-01", "2026-07-31"],
    "VIIRS_SNPP_NRT": ["2026-08-01", "2026-11-02"],
    "VIIRS_NOAA20_NRT": ["2026-08-01", "2026-11-14"],
}


def _monthly_frame(months, viirs, modis, harm=None):
    idx = pd.DatetimeIndex(months)
    return pd.DataFrame({
        "viirs_native": viirs,
        "modis_filtered": modis,
        "harm_modis_only": harm if harm is not None else modis,
    }, index=idx)


def test_zero_detection_month_with_viirs_coverage_is_kept():
    months = ["2015-01-01", "2015-02-01", "2015-03-01"]
    monthly = _monthly_frame(months, [10.0, 0.0, 5.0], [3.0, 0.0, 2.0])
    cov = product_coverage_table(PRODUCTS, monthly.index)
    info = select_evaluation_months(monthly, cov, standard_only=True)
    assert info["n_selected"] == 3
    assert info["n_zero_detection_kept"] == 1
    assert "2015-02-01" in info["months"]


def test_month_without_product_coverage_excluded_not_zeroed():
    # Pre-VIIRS month has MODIS but no VIIRS product → excluded when both required
    months = ["2010-06-01", "2015-06-01"]
    monthly = _monthly_frame(months, [0.0, 4.0], [8.0, 2.0])
    cov = product_coverage_table(PRODUCTS, monthly.index)
    assert cov.loc[pd.Timestamp("2010-06-01"), "viirs_covered"] is False or \
        cov.loc[pd.Timestamp("2010-06-01"), "viirs_covered"] == False
    info = select_evaluation_months(monthly, cov, standard_only=True, post_viirs_only=False)
    assert "2010-06-01" not in info["months"]
    assert info["n_excluded_no_coverage"] >= 1


def test_known_none_month_not_treated_as_zero_fire():
    months = pd.date_range("2015-01-01", periods=3, freq="MS")
    monthly = _monthly_frame(months, [1.0, 0.0, 2.0], [1.0, 0.0, 1.0])
    # Fake coverage: middle month has no product
    cov = product_coverage_table(PRODUCTS, months)
    cov.loc[months[1], :] = {
        "modis_covered": False, "viirs_covered": False,
        "modis_standard": False, "viirs_standard": False,
        "product_kind": "none", "sources": "",
    }
    info = select_evaluation_months(monthly, cov, standard_only=False)
    assert months[1].strftime("%Y-%m-%d") not in info["months"]
    assert info["n_selected"] == 2
    assert info["n_excluded_no_coverage"] >= 1


def test_standard_only_drops_nrt_tail_months():
    months = ["2026-06-01", "2026-09-01"]  # Jun = SP; Sep = NRT tail
    monthly = _monthly_frame(months, [3.0, 4.0], [1.0, 2.0])
    cov = product_coverage_table(PRODUCTS, pd.DatetimeIndex(months))
    assert cov.loc[pd.Timestamp("2026-06-01"), "product_kind"] == "standard"
    assert cov.loc[pd.Timestamp("2026-09-01"), "product_kind"] in ("nrt", "mixed")
    std = select_evaluation_months(monthly, cov, standard_only=True)
    all_ = select_evaluation_months(monthly, cov, standard_only=False)
    assert "2026-06-01" in std["months"]
    assert "2026-09-01" not in std["months"]
    assert "2026-09-01" in all_["months"]


def test_platform_scale_learned_on_calib_not_test():
    months = pd.date_range("2015-01-01", periods=12, freq="MS")
    # Calib (2015 H1): MODIS=1, VIIRS=2 → scale 2
    # Test (2015 H2): MODIS=10, VIIRS=10 → if scale leaked from test would be 1
    ref = np.array([2.0] * 6 + [10.0] * 6)
    raw = np.array([1.0] * 6 + [10.0] * 6)
    har = raw.copy()
    m = compare_to_viirs_reference(ref, raw, har, months=months, calib_end="2015-06-01")
    assert m["n_calib_months"] == 6
    assert m["n_test_months"] == 6
    assert m["platform_scale_fit"].startswith("calib_through_")
    # Scale must be ~2 (calib only), not ~1.1 (full sample)
    assert abs(m["platform_scale"] - 2.0) < 1e-9


def test_temporal_split_is_chronological():
    idx = pd.date_range("2012-01-01", periods=48, freq="MS")  # through 2015-12
    calib, test = temporal_calib_test_split(idx, calib_end="2013-12-01")
    assert calib.sum() + test.sum() == len(idx)
    assert idx[calib].max() <= pd.Timestamp("2013-12-01")
    assert idx[test].min() > pd.Timestamp("2013-12-01")


def test_platform_scale_factor_unit():
    assert platform_scale_factor(np.array([4.0, 6.0]), np.array([2.0, 3.0])) == 2.0
