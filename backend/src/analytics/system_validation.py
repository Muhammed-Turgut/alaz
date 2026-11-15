"""Leak-free system validation on a local FIRMS archive.

Measures **inter-sensor agreement** (MODIS vs fixed VIIRS), not independent
ground-truth accuracy. Compares raw MODIS, harmonized MODIS, and a simple
platform-scaled baseline on a chronological hold-out test set.

Usage (from ``backend/``)::

    python -m src.analytics.system_validation \\
        --archive-root data/archive/tr \\
        --standard-only

Exit codes: 0 = report written; 2 = archive/model missing; 3 = empty test set.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.analytics.confidence_sensitivity import (
    DEFAULT_BBOX,
    DEFAULT_POLYGON,
    ArchiveMissingError,
    evaluate_policies,
    load_roi_detections,
    prepare_for_modis_policy,
    require_archive,
)
from src.analytics.reference_comparison import (
    compare_to_viirs_reference,
    platform_scale_factor,
    product_coverage_table,
    select_evaluation_months,
    temporal_calib_test_split,
)
from src.data.firms_archive import ARCHIVE_DIR
from src.engine import pipeline as pl
from src.engine.confidence_policies import FIXED_VIIRS_POLICY
from src.engine.harmonizer_ml import MODEL_PATH, HarmonizerModel

# Secondary ROI inside TR archive (eastern Anatolia) — geographic hold-out check.
# Not used for threshold tuning; only scored with calib params from primary ROI.
GEO_HOLD_OUT_BBOX = (40.0, 38.5, 44.0, 41.0)  # west,south,east,north


def _verdict_block(metrics: dict) -> dict[str, Any]:
    """Compare raw / uncalibrated harm / post-cal blend / platform baseline."""
    if not metrics or metrics.get("n", 0) == 0:
        return {"ok": False, "note": "no test months"}
    mae_raw = metrics.get("mae_raw")
    mae_har = metrics.get("mae_harmonized")
    mae_damped = metrics.get("mae_harmonized_damped")
    mae_cal = metrics.get("mae_harmonized_calibrated") or metrics.get("mae_harmonized_blend")
    mae_base = metrics.get("mae_platform_scaled")
    # Primary claim uses calib-only post-cal blend (not raw model dump)
    primary = mae_cal if mae_cal is not None else mae_har
    beats_raw = (
        primary is not None and mae_raw is not None and primary < mae_raw
    )
    beats_base = (
        primary is not None and mae_base is not None and primary < mae_base
    )
    return {
        "ok": True,
        "harmonized_beats_raw_mae": bool(beats_raw),
        "harmonized_beats_platform_baseline_mae": bool(beats_base),
        "primary_series": "harmonized_blend" if mae_cal is not None else "harmonized_uncalibrated",
        "mae_raw": mae_raw,
        "mae_harmonized_uncalibrated": mae_har,
        "mae_harmonized_damped": mae_damped,
        "mae_harmonized_calibrated": mae_cal,
        "mae_harmonized": mae_cal if mae_cal is not None else mae_har,
        "mae_platform_scaled": mae_base,
        "rmse_raw": metrics.get("rmse_raw"),
        "rmse_harmonized": metrics.get("rmse_harmonized_calibrated") or metrics.get("rmse_harmonized"),
        "rmse_platform_scaled": metrics.get("rmse_platform_scaled"),
        "n_test_months": metrics.get("n_test_months") or metrics.get("n"),
        "platform_scale": metrics.get("platform_scale"),
        "harm_alpha": metrics.get("harm_alpha"),
        "harm_cap_k": metrics.get("harm_cap_k"),
        "blend_w": metrics.get("blend_w"),
        "platform_scale_fit": metrics.get("platform_scale_fit"),
        "postcal_fit": metrics.get("postcal_fit"),
        "note": (
            "Inter-sensor agreement only. Post-cal (alpha + cap + blend) is fit on "
            "calibration months only. Beating raw/baseline is necessary but not "
            "sufficient scientific validation."
        ),
    }


def evaluate_holdout_roi(
    raw: pd.DataFrame,
    *,
    products: dict,
    model: HarmonizerModel,
    standard_only: bool = True,
    calib_end: str = "2019-12-01",
    modis_policy: str = "A",
    fixed_postcal: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Temporal hold-out: calib ≤ calib_end → learn post-cal; score later months only.

    If ``fixed_postcal`` is set (geographic hold-out), do **not** re-fit
    coefficients on this ROI — apply the primary ROI's calib post-cal only.
    """
    df = prepare_for_modis_policy(
        raw, model, modis_policy=modis_policy, viirs_policy=FIXED_VIIRS_POLICY,
    )
    start = str(df["ts"].min().date())
    end = str(df["ts"].max().date())
    daily = pl.daily_series(df, start=start, end=end)
    monthly = daily.resample("MS").sum(min_count=1)
    coverage = product_coverage_table(products, monthly.index)
    month_info = select_evaluation_months(
        monthly, coverage,
        require_both_sensors=True,
        standard_only=standard_only,
        post_viirs_only=True,
    )
    mask = month_info["mask"]
    if not mask.any():
        return {
            "error": "No evaluation months after coverage filter",
            "month_selection": {k: v for k, v in month_info.items() if k != "mask"},
        }

    locked = monthly.index[mask]
    ref = monthly.loc[locked, "viirs_native"].to_numpy(dtype=float)
    raw_m = monthly.loc[locked, "modis_filtered"].to_numpy(dtype=float)
    har_m = monthly.loc[locked, "harm_modis_only"].to_numpy(dtype=float)
    calib_m, test_m = temporal_calib_test_split(locked, calib_end=calib_end)

    metrics = compare_to_viirs_reference(
        ref, raw_m, har_m, months=locked, calib_end=calib_end,
        fixed_postcal=fixed_postcal,
    )

    quiet = int(((ref == 0) & (raw_m == 0)).sum())
    learned = None
    if fixed_postcal is None and metrics.get("platform_scale") is not None:
        learned = {
            "platform_scale": float(metrics["platform_scale"]),
            "harm_alpha": float(metrics.get("harm_alpha", 1.0)),
            "harm_cap_k": float(metrics.get("harm_cap_k", 1e9)),
            "blend_w": float(metrics.get("blend_w", 1.0)),
        }
    return {
        "date_range_detections": [start, end],
        "month_selection": {k: v for k, v in month_info.items() if k != "mask"},
        "n_locked_months": int(len(locked)),
        "n_zero_zero_months_kept": quiet,
        "n_calib_months": int(calib_m.sum()),
        "n_test_months": int(test_m.sum()),
        "calib_end": calib_end,
        "modis_policy": modis_policy,
        "viirs_policy_fixed": FIXED_VIIRS_POLICY,
        "metrics": metrics,
        "verdict": _verdict_block(metrics),
        "metric_label": "inter_sensor_agreement",
        "reference_name": "VIIRS inter-sensor agreement reference (not ground truth)",
        "learned_postcal": learned,
        "learned_platform_scale": None if learned is None else learned["platform_scale"],
    }


def run_system_validation(
    archive_root: Path | str,
    *,
    bbox: tuple[float, float, float, float] = DEFAULT_BBOX,
    polygon=None,
    geo_bbox: tuple[float, float, float, float] | None = GEO_HOLD_OUT_BBOX,
    standard_only: bool = True,
    calib_end: str = "2019-12-01",
    model_path: Path | None = None,
) -> dict[str, Any]:
    """Full validation pack: fair A/B/C + temporal hold-out (+ optional geo ROI)."""
    root = Path(archive_root)
    require_archive(root)
    mp = Path(model_path) if model_path else MODEL_PATH
    if not mp.exists():
        raise ArchiveMissingError(f"Harmonizer model missing: {mp}")
    model = HarmonizerModel.load(mp)

    raw, meta = load_roi_detections(root, bbox=bbox, polygon=polygon)
    products = meta.get("products") or {}

    sensitivity = evaluate_policies(
        raw,
        products=products,
        model=model,
        standard_only=standard_only,
        calib_end=calib_end,
    )
    holdout = evaluate_holdout_roi(
        raw,
        products=products,
        model=model,
        standard_only=standard_only,
        calib_end=calib_end,
        modis_policy="A",
    )

    learned = holdout.get("learned_postcal")
    geo_holdout = None
    if geo_bbox is not None:
        try:
            raw_geo, meta_geo = load_roi_detections(root, bbox=geo_bbox, polygon=None)
            fixed = None
            if learned is not None:
                fixed = {
                    **learned,
                    "fit_label": "fixed_from_primary_roi_calib",
                }
            geo_holdout = evaluate_holdout_roi(
                raw_geo,
                products=meta_geo.get("products") or products,
                model=model,
                standard_only=standard_only,
                calib_end=calib_end,
                modis_policy="A",
                fixed_postcal=fixed,
            )
            geo_holdout["roi"] = meta_geo.get("bbox")
            geo_holdout["rows_roi"] = meta_geo.get("rows_roi")
            geo_holdout["role"] = (
                "geographic_holdout_same_archive — post-cal (scale/alpha/blend) from "
                "primary ROI calibration; this ROI is scored only (no re-tuning)."
            )
        except ArchiveMissingError as exc:
            geo_holdout = {"error": str(exc), "roi": {
                "west": geo_bbox[0], "south": geo_bbox[1],
                "east": geo_bbox[2], "north": geo_bbox[3],
            }}

    primary_verdict = holdout.get("verdict") or {}
    return {
        "title": "Alaz system validation (inter-sensor agreement)",
        "what_this_measures": (
            "Monthly MODIS hotspot counts vs a FIXED VIIRS filter (policy A) on "
            "the same ROI/months. Covered quiet (zero–zero) months are kept; "
            "months without product coverage are excluded. Platform scale, harm "
            "dampening (alpha), and blend weight are fit only on calibration "
            "months and scored on later test months."
        ),
        "what_this_does_not_measure": (
            "Independent fire/no-fire accuracy, burned-area skill, or event matching "
            "against perimeters / Sentinel-2 / Landsat / MCD64A1. VIIRS is another "
            "sensor, not ground truth. Harmonizer was trained on synthetic data. "
            "Monthly post-cal is a count-level correction, not proof of per-pixel skill."
        ),
        "archive_root": meta.get("archive_root"),
        "primary_roi": meta.get("bbox"),
        "rows_roi": meta.get("rows_roi"),
        "rows_archive": meta.get("rows_archive"),
        "standard_only": standard_only,
        "calib_end": calib_end,
        "fair_confidence_sensitivity": sensitivity,
        "temporal_holdout_primary": holdout,
        "geographic_holdout": geo_holdout,
        "summary": {
            "primary_temporal": primary_verdict,
            "progress_step": (
                "1) Fair fixed-VIIRS sensitivity + coverage/zero rules (this run). "
                "2) Beat raw + simple baseline on temporal hold-out with calib-only "
                "monthly post-cal (see verdict). "
                "3) Independent event/burn references — not in this pack."
            ),
        },
    }


def _parse_bbox(s: str) -> tuple[float, float, float, float]:
    parts = [float(x.strip()) for x in s.split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("bbox must be west,south,east,north")
    return parts[0], parts[1], parts[2], parts[3]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--archive-root", type=Path, default=ARCHIVE_DIR / "tr")
    p.add_argument("--bbox", type=_parse_bbox, default=DEFAULT_BBOX)
    p.add_argument("--use-demo-polygon", action="store_true")
    p.add_argument("--geo-bbox", type=_parse_bbox, default=GEO_HOLD_OUT_BBOX)
    p.add_argument("--no-geo", action="store_true", help="Skip geographic hold-out ROI")
    p.add_argument(
        "--standard-only",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    p.add_argument("--calib-end", default="2019-12-01")
    p.add_argument("--model", type=Path, default=None)
    p.add_argument(
        "-o", "--output",
        type=Path,
        default=Path("data/reports/system_validation.json"),
    )
    args = p.parse_args(argv)

    try:
        report = run_system_validation(
            args.archive_root,
            bbox=args.bbox,
            polygon=DEFAULT_POLYGON if args.use_demo_polygon else None,
            geo_bbox=None if args.no_geo else args.geo_bbox,
            standard_only=args.standard_only,
            calib_end=args.calib_end,
            model_path=args.model,
        )
    except ArchiveMissingError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    hold = report.get("temporal_holdout_primary") or {}
    if hold.get("error") or (hold.get("n_test_months") or 0) == 0:
        text = json.dumps(report, indent=2, default=str)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
        print(f"Wrote {args.output} (empty / failed test set)", file=sys.stderr)
        print(text)
        return 3

    text = json.dumps(report, indent=2, default=str)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text)
    print(f"Wrote {args.output}")

    v = (hold.get("verdict") or {})

    def _f(x, nd=3):
        return f"{x:.{nd}f}" if isinstance(x, (int, float)) and x is not None else "n/a"

    print(
        "\n=== Temporal hold-out (primary ROI) — inter-sensor agreement ===\n"
        f"  test months: {v.get('n_test_months')}\n"
        f"  MAE raw={_f(v.get('mae_raw'))}  "
        f"baseline={_f(v.get('mae_platform_scaled'))}\n"
        f"  MAE harm uncalibrated={_f(v.get('mae_harmonized_uncalibrated'))}  "
        f"damped={_f(v.get('mae_harmonized_damped'))}  "
        f"blend(calibrated)={_f(v.get('mae_harmonized_calibrated'))}\n"
        f"  primary={v.get('primary_series')}  "
        f"beats raw: {v.get('harmonized_beats_raw_mae')}  "
        f"beats baseline: {v.get('harmonized_beats_platform_baseline_mae')}\n"
        f"  postcal fit: {v.get('postcal_fit')}  "
        f"scale={_f(v.get('platform_scale'), 4)}  "
        f"alpha={_f(v.get('harm_alpha'), 4)}  "
        f"cap_k={_f(v.get('harm_cap_k'), 4)}  "
        f"blend_w={_f(v.get('blend_w'), 4)}\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
