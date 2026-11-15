"""Named confidence filter policies for MODIS / VIIRS hotspots.

Policy A matches production ``prepare.confidence_ok``.  Numeric thresholds are
**not** interchangeable across sensors: VIIRS class labels are mapped to
low=30 / nominal=65 / high=90, and production keeps MODIS at ``>= 30`` while
excluding VIIRS ``low`` with ``> 30``.
"""
from __future__ import annotations

from typing import Callable

import numpy as np
import pandas as pd

# Same mapping as archive_store.standardize_raw (keep in sync).
VIIRS_CONF_MAP = {
    "l": 30, "low": 30,
    "n": 65, "nominal": 65,
    "h": 90, "high": 90,
}

# Missing confidence in prepare() is filled with 65 (VIIRS "nominal").
MISSING_CONFIDENCE_DEFAULT = 65.0

PolicyFn = Callable[[pd.DataFrame], np.ndarray]


def map_viirs_confidence_labels(values) -> pd.Series:
    """Map VIIRS class labels (l/n/h or low/nominal/high) to numeric scores.

    Unmapped tokens fall back to ``to_numeric`` (NaN if neither works).
    """
    s = pd.Series(values)
    mapped = s.astype(str).str.strip().str.lower().map(VIIRS_CONF_MAP)
    return mapped.fillna(pd.to_numeric(s, errors="coerce"))


def _numeric_confidence(df: pd.DataFrame, *, fill_missing: bool = True) -> np.ndarray:
    conf = pd.to_numeric(df["confidence"], errors="coerce")
    if fill_missing:
        conf = conf.fillna(MISSING_CONFIDENCE_DEFAULT)
    return conf.to_numpy(dtype=float)


def _is_modis(df: pd.DataFrame) -> np.ndarray:
    return (df["instrument"].astype(str) == "MODIS").to_numpy()


def policy_current(df: pd.DataFrame) -> np.ndarray:
    """A — production: MODIS >= 30; VIIRS nominal/high (mapped conf > 30)."""
    conf = _numeric_confidence(df, fill_missing=True)
    is_modis = _is_modis(df)
    return np.where(is_modis, conf >= 30, conf > 30)


def policy_strict(df: pd.DataFrame) -> np.ndarray:
    """B — strict: MODIS >= 80; VIIRS high only (mapped conf >= 90)."""
    conf = _numeric_confidence(df, fill_missing=True)
    is_modis = _is_modis(df)
    return np.where(is_modis, conf >= 80, conf >= 90)


def policy_loose(df: pd.DataFrame) -> np.ndarray:
    """C — loose: valid MODIS numeric confidence; all VIIRS mapped classes.

    Valid MODIS: finite value in [0, 100].  VIIRS: finite mapped score
    (low/nominal/high all kept).  Missing still filled like production (65).
    """
    conf = _numeric_confidence(df, fill_missing=True)
    is_modis = _is_modis(df)
    modis_ok = np.isfinite(conf) & (conf >= 0) & (conf <= 100)
    viirs_ok = np.isfinite(conf)
    return np.where(is_modis, modis_ok, viirs_ok)


POLICIES: dict[str, PolicyFn] = {
    "A": policy_current,
    "current": policy_current,
    "B": policy_strict,
    "strict": policy_strict,
    "C": policy_loose,
    "loose": policy_loose,
}

POLICY_META = {
    "A": {
        "id": "A",
        "name": "current",
        "modis": "confidence >= 30",
        "viirs": "nominal/high (mapped score > 30; low excluded)",
    },
    "B": {
        "id": "B",
        "name": "strict",
        "modis": "confidence >= 80",
        "viirs": "high only (mapped score >= 90)",
    },
    "C": {
        "id": "C",
        "name": "loose",
        "modis": "finite confidence in [0, 100]",
        "viirs": "all mapped classes (low/nominal/high)",
    },
}


def apply_policy(df: pd.DataFrame, policy: str = "A") -> np.ndarray:
    key = str(policy).strip()
    if key not in POLICIES:
        raise ValueError(f"unknown confidence policy {policy!r}; expected one of {sorted(POLICIES)}")
    return POLICIES[key](df)


def modis_mask(df: pd.DataFrame, policy: str = "A") -> np.ndarray:
    """MODIS-only keep-mask for policy A/B/C (ignores VIIRS rows → False)."""
    conf = _numeric_confidence(df, fill_missing=True)
    is_modis = _is_modis(df)
    key = str(policy).strip().upper()
    if key in ("A", "CURRENT"):
        ok = conf >= 30
    elif key in ("B", "STRICT"):
        ok = conf >= 80
    elif key in ("C", "LOOSE"):
        ok = np.isfinite(conf) & (conf >= 0) & (conf <= 100)
    else:
        raise ValueError(f"unknown MODIS policy {policy!r}")
    return is_modis & ok


def viirs_mask(df: pd.DataFrame, policy: str = "A") -> np.ndarray:
    """VIIRS-only keep-mask. Sensitivity comparisons should keep this fixed at A."""
    conf = _numeric_confidence(df, fill_missing=True)
    is_viirs = ~_is_modis(df)
    key = str(policy).strip().upper()
    if key in ("A", "CURRENT"):
        ok = conf > 30  # nominal/high
    elif key in ("B", "STRICT"):
        ok = conf >= 90  # high only
    elif key in ("C", "LOOSE"):
        ok = np.isfinite(conf)
    else:
        raise ValueError(f"unknown VIIRS policy {policy!r}")
    return is_viirs & ok


def fair_policy_mask(
    df: pd.DataFrame,
    *,
    modis_policy: str = "A",
    viirs_policy: str = "A",
) -> np.ndarray:
    """Combine MODIS + VIIRS masks. For fair A/B/C sensitivity keep ``viirs_policy='A'``."""
    return modis_mask(df, modis_policy) | viirs_mask(df, viirs_policy)


def detection_filter_counts(df: pd.DataFrame, mask: np.ndarray) -> dict:
    """Per-sensor kept / dropped counts for a boolean keep-mask."""
    inst = df["instrument"].astype(str).to_numpy()
    mask = np.asarray(mask, dtype=bool)
    out = {}
    for label in ("MODIS", "VIIRS"):
        m = inst == label
        n = int(m.sum())
        kept = int((m & mask).sum())
        out[label] = {
            "total": n,
            "kept": kept,
            "dropped": n - kept,
            "drop_rate": float((n - kept) / n) if n else 0.0,
        }
    out["ALL"] = {
        "total": int(len(df)),
        "kept": int(mask.sum()),
        "dropped": int((~mask).sum()),
    }
    return out


def _monthly_instrument_counts(
    df: pd.DataFrame,
    keep: np.ndarray,
    *,
    start: pd.Timestamp | None = None,
) -> tuple[pd.Series, pd.Series]:
    """Monthly MODIS / VIIRS detection counts under a keep-mask (UTC month start)."""
    keep = np.asarray(keep, dtype=bool)
    if not keep.any():
        empty = pd.Series(dtype=float)
        return empty, empty
    sub = df.loc[keep]
    month = pd.to_datetime(sub["ts"]).dt.to_period("M").dt.to_timestamp()
    inst = sub["instrument"].astype(str).str.upper()
    modis = pd.Series(1.0, index=month[inst == "MODIS"]).groupby(level=0).sum()
    viirs = pd.Series(1.0, index=month[inst == "VIIRS"]).groupby(level=0).sum()
    if start is not None:
        modis = modis.loc[modis.index >= start]
        viirs = viirs.loc[viirs.index >= start]
    idx = modis.index.union(viirs.index).sort_values()
    return modis.reindex(idx, fill_value=0.0), viirs.reindex(idx, fill_value=0.0)


def _series_closeness(
    reference: pd.Series,
    candidate: pd.Series,
    *,
    drop_both_zero: bool = False,
) -> dict:
    """Inter-sensor agreement metrics (VIIRS is comparison reference, not truth).

    By default **keeps** months where both series are zero (covered quiet months).
    Set ``drop_both_zero=True`` only for legacy diagnostics — not for fair claims.
    """
    idx = reference.index.union(candidate.index).sort_values()
    if len(idx) == 0:
        return {"n_months": 0, "n_both_zero_kept": 0}
    ref = reference.reindex(idx, fill_value=0.0).to_numpy(dtype=float)
    cand = candidate.reindex(idx, fill_value=0.0).to_numpy(dtype=float)
    both_zero = (ref == 0) & (cand == 0)
    n_both_zero = int(both_zero.sum())
    if drop_both_zero:
        keep = ~both_zero
        ref, cand = ref[keep], cand[keep]
    if len(ref) == 0:
        return {"n_months": 0, "n_both_zero_kept": 0 if drop_both_zero else n_both_zero}
    rmse = float(np.sqrt(np.mean((cand - ref) ** 2)))
    mae = float(np.mean(np.abs(cand - ref)))
    bias = float(np.mean(cand - ref))
    rel_bias = float(100.0 * (cand.sum() - ref.sum()) / max(ref.sum(), 1e-12))
    corr = (
        float(np.corrcoef(cand, ref)[0, 1])
        if len(ref) > 1 and np.std(cand) > 0 and np.std(ref) > 0
        else float("nan")
    )
    return {
        "n_months": int(len(ref)),
        "n_both_zero_kept": 0 if drop_both_zero else n_both_zero,
        "rmse": rmse,
        "mae": mae,
        "bias": bias,
        "relative_bias_pct": rel_bias,
        "correlation": corr,
        "label": "inter_sensor_agreement",
    }


PRIMARY_POLICY = "A"
FIXED_VIIRS_POLICY = "A"


def confidence_filter_sensitivity(
    df: pd.DataFrame,
    *,
    viirs_start: str = "2012-01-20",
) -> dict:
    """Fair A/B/C sensitivity: vary **MODIS** threshold; keep VIIRS filter fixed at A.

    Production stays on policy **A**. Does **not** auto-select a “best” threshold.
    Zero–zero months are kept (quiet months ≠ missing coverage).
    Metrics are **inter-sensor agreement**, not independent accuracy.
    """
    if df is None or getattr(df, "empty", True) or "confidence" not in df.columns:
        return {
            "primary_policy": PRIMARY_POLICY,
            "n": 0,
            "note": "No detections available for confidence sensitivity.",
        }
    if "instrument" not in df.columns or "ts" not in df.columns:
        return {
            "primary_policy": PRIMARY_POLICY,
            "n": 0,
            "note": "Missing instrument/ts for confidence sensitivity.",
        }

    static = (
        ~df["is_static"].to_numpy(dtype=bool)
        if "is_static" in df.columns
        else np.ones(len(df), dtype=bool)
    )
    start = pd.Timestamp(viirs_start)
    ts = pd.to_datetime(df["ts"])
    t_min = max(start, ts.min().to_period("M").to_timestamp())
    t_max = ts.max().to_period("M").to_timestamp()
    # Continuous calendar so covered quiet months stay in the sample (not dropped).
    # Product no-coverage exclusion requires archive metadata — see system_validation.
    month_index = pd.date_range(t_min, t_max, freq="MS")

    # Fixed VIIRS reference (policy A) — never re-filtered with B/C.
    viirs_fixed = fair_policy_mask(df, modis_policy="A", viirs_policy=FIXED_VIIRS_POLICY) & static
    _, viirs_ref = _monthly_instrument_counts(df, viirs_fixed, start=start)
    viirs_ref = viirs_ref.reindex(month_index, fill_value=0.0)

    policies = []
    for pid in ("A", "B", "C"):
        keep = fair_policy_mask(df, modis_policy=pid, viirs_policy=FIXED_VIIRS_POLICY) & static
        counts = detection_filter_counts(df, keep)
        modis_m, viirs_m = _monthly_instrument_counts(df, keep, start=start)
        viirs_aligned = viirs_m.reindex(month_index, fill_value=0.0)
        modis_aligned = modis_m.reindex(month_index, fill_value=0.0)
        viirs_match = bool(np.allclose(viirs_aligned.to_numpy(), viirs_ref.to_numpy()))
        closeness = _series_closeness(viirs_ref, modis_aligned, drop_both_zero=False)
        policies.append({
            **POLICY_META[pid],
            "modis_policy": pid,
            "viirs_policy_fixed": FIXED_VIIRS_POLICY,
            "viirs_reference_unchanged": viirs_match,
            "filter_counts": counts,
            "vs_fixed_viirs_reference": closeness,
            # Back-compat key for older UI readers
            "vs_viirs_same_policy": closeness,
            "is_primary": pid == PRIMARY_POLICY,
        })

    ranked = sorted(
        (p for p in policies if p.get("vs_fixed_viirs_reference", {}).get("n_months", 0) > 0),
        key=lambda p: p["vs_fixed_viirs_reference"].get("rmse", float("inf")),
    )
    closest = ranked[0]["id"] if ranked else None

    return {
        "primary_policy": PRIMARY_POLICY,
        "primary_used_in_main_result": True,
        "auto_select_best": False,
        "fair_comparison": True,
        "viirs_policy_fixed": FIXED_VIIRS_POLICY,
        "metric_label": "inter_sensor_agreement",
        "closest_by_rmse_to_viirs": closest,
        "selection_note": (
            "Closest-by-RMSE is diagnostic only. Main path keeps policy A. "
            "VIIRS filter is fixed at A while MODIS threshold varies (fair comparison). "
            "Zero–zero months are kept; this is NOT independent ground-truth accuracy."
        ),
        "scale_note": (
            "VIIRS low/nominal/high are ordinal product classes, not MODIS 0–100. "
            "Mapped scores low=30 / nominal=65 / high=90 enable a shared code path "
            "but do not mean a common physical confidence threshold."
        ),
        "tradeoff_note": (
            "Stricter (B): fewer low-confidence MODIS detections, higher risk of missing "
            "small/weak fires. Looser (C): more MODIS detections retained, higher false-positive risk. "
            "Judged by inter-sensor agreement vs fixed VIIRS reference — not visual preference."
        ),
        "policies": policies,
        "n_detections": int(len(df)),
    }
