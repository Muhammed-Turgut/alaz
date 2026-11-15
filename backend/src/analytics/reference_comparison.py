"""Monthly MODIS → VIIRS **comparison reference** evaluation helpers.

VIIRS is never treated as ground truth.  Month selection prefers product
coverage metadata so months without coverage are excluded (not zero-filled),
while valid covered months with zero detections stay in the sample.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd

from src.engine import stats_diagnostics as sd
from src.engine.sensors import VIIRS_START

VSTART = pd.Timestamp(str(VIIRS_START))


def _as_date(v) -> date | None:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    return pd.Timestamp(v).date()


def product_coverage_table(
    products: dict[str, list | tuple],
    month_starts: pd.DatetimeIndex,
) -> pd.DataFrame:
    """Build per-month coverage flags from FIRMS ``area.json`` product ranges.

    ``products`` maps source id → [start, end] inclusive ISO dates
    (e.g. ``MODIS_SP``, ``VIIRS_SNPP_SP``).
    """
    idx = pd.DatetimeIndex(month_starts).to_period("M").to_timestamp()
    rows = []
    for ms in idx:
        me = (ms + pd.offsets.MonthEnd(0)).normalize()
        m_start, m_end = ms.date(), me.date()
        modis_std = modis_nrt = viirs_std = viirs_nrt = False
        sources_hit: list[str] = []
        for src, span in (products or {}).items():
            if not span or len(span) < 2:
                continue
            a, b = _as_date(span[0]), _as_date(span[1])
            if a is None or b is None:
                continue
            if b < m_start or a > m_end:
                continue
            sources_hit.append(str(src))
            u = str(src).upper()
            if u.startswith("MODIS"):
                if u.endswith("_SP"):
                    modis_std = True
                elif u.endswith("_NRT"):
                    modis_nrt = True
            elif "VIIRS" in u:
                if u.endswith("_SP"):
                    viirs_std = True
                elif u.endswith("_NRT"):
                    viirs_nrt = True
        modis_covered = modis_std or modis_nrt
        viirs_covered = viirs_std or viirs_nrt
        if modis_std and viirs_std and not (modis_nrt or viirs_nrt):
            kind = "standard"
        elif (modis_nrt or viirs_nrt) and not (modis_std or viirs_std):
            kind = "nrt"
        elif modis_covered or viirs_covered:
            kind = "mixed"
        else:
            kind = "none"
        rows.append({
            "modis_covered": modis_covered,
            "viirs_covered": viirs_covered,
            "modis_standard": modis_std,
            "viirs_standard": viirs_std,
            "product_kind": kind,
            "sources": ",".join(sorted(sources_hit)),
        })
    return pd.DataFrame(rows, index=idx)


def select_evaluation_months(
    monthly: pd.DataFrame,
    coverage: pd.DataFrame | None = None,
    *,
    require_both_sensors: bool = True,
    standard_only: bool = False,
    post_viirs_only: bool = True,
) -> dict[str, Any]:
    """Choose months for MODIS↔VIIRS reference comparison.

    Rules
    -----
    * Months **without** product/coverage info are excluded (not counted as zero).
    * Months with valid VIIRS coverage and **zero** detections stay in the set.
    * Optionally restrict to completed Standard-product months (both sensors).
    """
    if monthly is None or monthly.empty:
        return {
            "mask": np.array([], dtype=bool),
            "n_selected": 0,
            "n_excluded_no_coverage": 0,
            "n_zero_detection_kept": 0,
            "months": [],
            "note": "empty monthly frame",
        }

    m = monthly.copy()
    if not isinstance(m.index, pd.DatetimeIndex):
        m.index = pd.to_datetime(m.index)
    m = m.sort_index()
    post = m.index >= VSTART if post_viirs_only else np.ones(len(m), dtype=bool)

    if coverage is None:
        # Without coverage metadata we cannot tell "no product" from "zero fires".
        # Keep all post-VIIRS months (including zeros); report unknown exclusions.
        base = np.asarray(post, dtype=bool)
        if require_both_sensors:
            # Still require columns exist; zeros allowed.
            need = {"viirs_native", "modis_filtered", "harm_modis_only"}
            if not need.issubset(m.columns):
                base[:] = False
        zero_kept = int((base & (m["viirs_native"].to_numpy(dtype=float) == 0)).sum()) if "viirs_native" in m.columns else 0
        return {
            "mask": base,
            "n_selected": int(base.sum()),
            "n_excluded_no_coverage": 0,
            "n_zero_detection_kept": zero_kept,
            "months": [str(ts.date()) for ts in m.index[base]],
            "coverage_available": False,
            "note": (
                "No product coverage table — months without FIRMS product cannot be "
                "distinguished from true zero-detection months."
            ),
        }

    cov = coverage.reindex(m.index)
    has_info = cov["viirs_covered"].notna() if "viirs_covered" in cov.columns else pd.Series(False, index=m.index)
    # Rows that failed reindex (no coverage row) → no info
    if "product_kind" in cov.columns:
        has_info = cov["product_kind"].notna() & (cov["product_kind"].astype(str) != "none")
        # "none" means we *know* there is no product — exclude separately
        known_none = cov["product_kind"].astype(str).eq("none")
    else:
        known_none = pd.Series(False, index=m.index)

    viirs_ok = cov.get("viirs_covered", pd.Series(False, index=m.index)).fillna(False).astype(bool)
    modis_ok = cov.get("modis_covered", pd.Series(False, index=m.index)).fillna(False).astype(bool)
    both_std = (
        cov.get("modis_standard", pd.Series(False, index=m.index)).fillna(False).astype(bool)
        & cov.get("viirs_standard", pd.Series(False, index=m.index)).fillna(False).astype(bool)
    )

    covered = viirs_ok & (modis_ok if require_both_sensors else True)
    if standard_only:
        covered = covered & both_std

    # Exclude: post-VIIRS months that are known-none or lack coverage when we have a table
    eligible = np.asarray(post, dtype=bool) & covered.to_numpy()
    no_cov = np.asarray(post, dtype=bool) & ~covered.to_numpy()
    # Count only those we deliberately drop for missing/none coverage
    n_excl = int(no_cov.sum())

    ref = m["viirs_native"].to_numpy(dtype=float) if "viirs_native" in m.columns else np.zeros(len(m))
    zero_kept = int((eligible & (ref == 0)).sum())

    return {
        "mask": eligible,
        "n_selected": int(eligible.sum()),
        "n_excluded_no_coverage": n_excl,
        "n_excluded_known_none": int((np.asarray(post, dtype=bool) & known_none.to_numpy()).sum()),
        "n_zero_detection_kept": zero_kept,
        "months": [str(ts.date()) for ts in m.index[eligible]],
        "coverage_available": True,
        "standard_only": bool(standard_only),
        "product_kinds": (
            cov.loc[eligible, "product_kind"].value_counts().to_dict()
            if "product_kind" in cov.columns and eligible.any() else {}
        ),
        "note": (
            "Standard-only months" if standard_only
            else "All months with product coverage (Standard and/or NRT)"
        ),
    }


def platform_scale_factor(reference: np.ndarray, raw: np.ndarray) -> float:
    """Learn multiplicative platform-count baseline on a **calibration** set only."""
    ref = np.asarray(reference, dtype=float)
    raw = np.asarray(raw, dtype=float)
    if len(ref) == 0:
        return 1.0
    return float(np.mean(ref) / max(np.mean(raw), 1e-9))


def harm_dampen_factor(reference: np.ndarray, harmonized: np.ndarray) -> float:
    """Mean-matching dampening (diagnostic). Prefer ``fit_harm_alpha_mae`` for scoring."""
    ref = np.asarray(reference, dtype=float)
    har = np.asarray(harmonized, dtype=float)
    if len(ref) == 0:
        return 1.0
    return float(np.mean(ref) / max(np.mean(har), 1e-9))


def fit_harm_alpha_mae(
    reference: np.ndarray,
    harmonized: np.ndarray,
    *,
    grid: int = 81,
) -> float:
    """``alpha`` minimizing MAE(alpha * har, ref) on calib (includes alpha=0)."""
    ref = np.asarray(reference, dtype=float)
    har = np.asarray(harmonized, dtype=float)
    if len(ref) == 0:
        return 1.0
    # Search from 0 up to a generous multiple of mean-match
    mean_a = harm_dampen_factor(ref, har)
    hi = max(2.0 * mean_a, 2.0)
    best_a, best_mae = 0.0, float("inf")
    for a in np.linspace(0.0, hi, grid):
        mae = float(np.mean(np.abs(a * har - ref)))
        if mae < best_mae:
            best_mae, best_a = mae, float(a)
    return best_a


def fit_harm_cap_k(
    reference: np.ndarray,
    platform_scaled: np.ndarray,
    harm_damped: np.ndarray,
    *,
    candidates: tuple[float, ...] = (0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 1e9),
) -> float:
    """Cap ``min(harm_damped, k * scaled)``; pick k by calib MAE (1e9 ≈ no cap)."""
    ref = np.asarray(reference, dtype=float)
    scaled = np.asarray(platform_scaled, dtype=float)
    har = np.asarray(harm_damped, dtype=float)
    if len(ref) == 0:
        return 1e9
    best_k, best_mae = 1e9, float("inf")
    for k in candidates:
        capped = np.minimum(har, float(k) * scaled)
        # When scaled is 0, keep harm (or 0) — don't invent from empty MODIS months
        capped = np.where(scaled > 0, capped, np.minimum(har, 0.0))
        mae = float(np.mean(np.abs(capped - ref)))
        if mae < best_mae:
            best_mae, best_k = mae, float(k)
    return best_k


def fit_blend_weight(
    reference: np.ndarray,
    platform_scaled: np.ndarray,
    harm_damped: np.ndarray,
    *,
    grid: int = 41,
) -> float:
    """``w`` in [0,1] minimizing MAE of ``w * scaled + (1-w) * harm_damped`` (calib only)."""
    ref = np.asarray(reference, dtype=float)
    scaled = np.asarray(platform_scaled, dtype=float)
    har = np.asarray(harm_damped, dtype=float)
    if len(ref) == 0:
        return 1.0
    best_w, best_mae = 1.0, float("inf")
    for w in np.linspace(0.0, 1.0, grid):
        pred = w * scaled + (1.0 - w) * har
        mae = float(np.mean(np.abs(pred - ref)))
        if mae < best_mae:
            best_mae, best_w = mae, float(w)
    return best_w


def fit_monthly_postcal(
    reference: np.ndarray,
    raw: np.ndarray,
    harmonized: np.ndarray,
) -> dict[str, float]:
    """Learn platform scale, MAE-optimal alpha, megafire cap, and blend on calib."""
    scale = platform_scale_factor(reference, raw)
    alpha = fit_harm_alpha_mae(reference, harmonized)
    scaled = np.asarray(raw, dtype=float) * scale
    har_d = np.asarray(harmonized, dtype=float) * alpha
    cap_k = fit_harm_cap_k(reference, scaled, har_d)
    har_c = np.where(scaled > 0, np.minimum(har_d, cap_k * scaled), 0.0)
    w = fit_blend_weight(reference, scaled, har_c)
    return {
        "platform_scale": scale,
        "harm_alpha": alpha,
        "harm_cap_k": cap_k,
        "blend_w": w,
    }


def apply_monthly_postcal(
    raw: np.ndarray,
    harmonized: np.ndarray,
    *,
    platform_scale: float,
    harm_alpha: float,
    blend_w: float,
    harm_cap_k: float = 1e9,
) -> dict[str, np.ndarray]:
    """Apply calib-fitted monthly post-cal coefficients (no re-fitting)."""
    raw = np.asarray(raw, dtype=float)
    har = np.asarray(harmonized, dtype=float)
    scaled = raw * float(platform_scale)
    har_d = har * float(harm_alpha)
    har_c = np.where(scaled > 0, np.minimum(har_d, float(harm_cap_k) * scaled), 0.0)
    w = float(blend_w)
    blend = w * scaled + (1.0 - w) * har_c
    return {
        "platform_scaled": scaled,
        "harmonized_damped": har_c,
        "harmonized_blend": blend,
    }



def temporal_calib_test_split(
    months: pd.DatetimeIndex,
    *,
    calib_end: str | pd.Timestamp = "2019-12-01",
) -> tuple[np.ndarray, np.ndarray]:
    """Earlier months = calibration; later months = test. No shuffle."""
    idx = pd.DatetimeIndex(months)
    cut = pd.Timestamp(calib_end)
    calib = np.asarray(idx <= cut, dtype=bool)
    test = np.asarray(idx > cut, dtype=bool)
    return calib, test


def compare_to_viirs_reference(
    reference: np.ndarray,
    raw_modis: np.ndarray,
    harmonized_modis: np.ndarray,
    *,
    months: pd.DatetimeIndex | None = None,
    calib_end: str | pd.Timestamp = "2019-12-01",
    fixed_postcal: dict[str, float] | None = None,
) -> dict[str, Any]:
    """RMSE / MAE / relative bias / correlation vs VIIRS comparison reference.

    Coefficients learned on calibration only (or taken from ``fixed_postcal`` for
    geographic hold-out): platform scale, harm dampening alpha, blend weight.
    Never fit on scored test months.
    """
    ref = np.asarray(reference, dtype=float)
    raw = np.asarray(raw_modis, dtype=float)
    har = np.asarray(harmonized_modis, dtype=float)
    n = len(ref)
    if n == 0:
        return {"n": 0, "note": "no months selected"}

    if months is None:
        months = pd.DatetimeIndex([pd.Timestamp("2012-01-01") + pd.offsets.MonthBegin(i) for i in range(n)])
    else:
        months = pd.DatetimeIndex(months)

    calib_m, test_m = temporal_calib_test_split(months, calib_end=calib_end)
    if fixed_postcal is not None:
        post = {
            "platform_scale": float(fixed_postcal["platform_scale"]),
            "harm_alpha": float(fixed_postcal["harm_alpha"]),
            "harm_cap_k": float(fixed_postcal.get("harm_cap_k", 1e9)),
            "blend_w": float(fixed_postcal["blend_w"]),
        }
        fit_label = str(fixed_postcal.get("fit_label") or "fixed_from_primary_roi_calib")
        if not test_m.any():
            return {"n": 0, "note": "no test months", "platform_scale_fit": fit_label}
        ref_t, raw_t, har_t = ref[test_m], raw[test_m], har[test_m]
        applied = apply_monthly_postcal(
            raw_t, har_t,
            platform_scale=post["platform_scale"],
            harm_alpha=post["harm_alpha"],
            blend_w=post["blend_w"],
            harm_cap_k=post["harm_cap_k"],
        )
        metrics = sd.harmonization_metrics(ref_t, raw_t, har_t)
        metrics.update(_mae_block(
            ref_t, raw_t, har_t, applied["platform_scaled"],
            har_damped=applied["harmonized_damped"],
            har_blend=applied["harmonized_blend"],
        ))
        metrics.update(post)
        metrics["platform_scale_fit"] = fit_label
        metrics["postcal_fit"] = fit_label
        metrics["n_calib_months"] = int(calib_m.sum())
        metrics["n_test_months"] = int(test_m.sum())
        metrics["test_months"] = [str(ts.date()) for ts in months[test_m]]
        metrics["months"] = [str(ts.date()) for ts in months[test_m]]
        metrics["reference_name"] = "VIIRS comparison reference (not ground truth)"
        metrics["date_range_test"] = [
            str(months[test_m].min().date()),
            str(months[test_m].max().date()),
        ]
        return metrics

    if not calib_m.any() or not test_m.any():
        # Fall back: report full-sample metrics but mark scale as in-sample (discouraged).
        post = fit_monthly_postcal(ref, raw, har)
        applied = apply_monthly_postcal(
            raw, har,
            platform_scale=post["platform_scale"],
            harm_alpha=post["harm_alpha"],
            blend_w=post["blend_w"],
            harm_cap_k=post["harm_cap_k"],
        )
        metrics = sd.harmonization_metrics(ref, raw, har)
        metrics.update(_mae_block(
            ref, raw, har, applied["platform_scaled"],
            har_damped=applied["harmonized_damped"],
            har_blend=applied["harmonized_blend"],
        ))
        metrics.update(post)
        metrics["platform_scale_fit"] = "full_sample_fallback"
        metrics["postcal_fit"] = "full_sample_fallback"
        metrics["n_calib_months"] = int(calib_m.sum())
        metrics["n_test_months"] = int(test_m.sum())
        metrics["months"] = [str(ts.date()) for ts in months]
        metrics["reference_name"] = "VIIRS comparison reference (not ground truth)"
        return metrics

    post = fit_monthly_postcal(ref[calib_m], raw[calib_m], har[calib_m])
    ref_t, raw_t, har_t = ref[test_m], raw[test_m], har[test_m]
    applied = apply_monthly_postcal(
        raw_t, har_t,
        platform_scale=post["platform_scale"],
        harm_alpha=post["harm_alpha"],
        blend_w=post["blend_w"],
        harm_cap_k=post["harm_cap_k"],
    )
    metrics = sd.harmonization_metrics(ref_t, raw_t, har_t)
    metrics.update(_mae_block(
        ref_t, raw_t, har_t, applied["platform_scaled"],
        har_damped=applied["harmonized_damped"],
        har_blend=applied["harmonized_blend"],
    ))
    metrics.update(post)
    metrics["platform_scale_fit"] = f"calib_through_{pd.Timestamp(calib_end).date()}"
    metrics["postcal_fit"] = f"calib_through_{pd.Timestamp(calib_end).date()}"
    metrics["n_calib_months"] = int(calib_m.sum())
    metrics["n_test_months"] = int(test_m.sum())
    metrics["calib_months"] = [str(ts.date()) for ts in months[calib_m]]
    metrics["test_months"] = [str(ts.date()) for ts in months[test_m]]
    metrics["months"] = [str(ts.date()) for ts in months[test_m]]
    metrics["reference_name"] = "VIIRS comparison reference (not ground truth)"
    metrics["date_range_test"] = [
        str(months[test_m].min().date()),
        str(months[test_m].max().date()),
    ]
    return metrics


def _mae_block(ref, raw, har, scaled, *, har_damped=None, har_blend=None) -> dict:
    out = {
        "mae_raw": float(np.mean(np.abs(raw - ref))),
        "mae_harmonized": float(np.mean(np.abs(har - ref))),
        "mae_platform_scaled": float(np.mean(np.abs(scaled - ref))),
        "rmse_platform_scaled": float(np.sqrt(np.mean((scaled - ref) ** 2))),
        "relative_bias_platform_scaled_pct": float(
            100.0 * (scaled.sum() - ref.sum()) / max(ref.sum(), 1e-12)
        ),
    }
    if har_damped is not None:
        hd = np.asarray(har_damped, dtype=float)
        out["mae_harmonized_damped"] = float(np.mean(np.abs(hd - ref)))
        out["rmse_harmonized_damped"] = float(np.sqrt(np.mean((hd - ref) ** 2)))
        out["relative_bias_harmonized_damped_pct"] = float(
            100.0 * (hd.sum() - ref.sum()) / max(ref.sum(), 1e-12)
        )
    if har_blend is not None:
        hb = np.asarray(har_blend, dtype=float)
        out["mae_harmonized_blend"] = float(np.mean(np.abs(hb - ref)))
        out["rmse_harmonized_blend"] = float(np.sqrt(np.mean((hb - ref) ** 2)))
        out["relative_bias_harmonized_blend_pct"] = float(
            100.0 * (hb.sum() - ref.sum()) / max(ref.sum(), 1e-12)
        )
        # Primary post-cal series used for "beats baseline" claims
        out["mae_harmonized_calibrated"] = out["mae_harmonized_blend"]
        out["rmse_harmonized_calibrated"] = out["rmse_harmonized_blend"]
    return out



def summarize_policy_result(
    *,
    policy_id: str,
    policy_meta: dict,
    filter_counts: dict,
    month_info: dict,
    metrics: dict,
) -> dict[str, Any]:
    """Bundle one policy's filter loss + reference-comparison metrics."""
    return {
        "policy": policy_meta,
        "detections": filter_counts,
        "months": {
            "n_selected": month_info.get("n_selected"),
            "n_excluded_no_coverage": month_info.get("n_excluded_no_coverage"),
            "n_zero_detection_kept": month_info.get("n_zero_detection_kept"),
            "product_kinds": month_info.get("product_kinds"),
            "standard_only": month_info.get("standard_only"),
            "note": month_info.get("note"),
        },
        "metrics": metrics,
        "caveat": (
            "Metrics compare monthly MODIS series to a VIIRS comparison reference "
            "on the same ROI/months. Passing thresholds or lower RMSE does not "
            "scientifically validate harmonization; VIIRS is not ground truth."
        ),
    }
