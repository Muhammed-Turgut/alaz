"""Offline confidence-policy sensitivity on a local Turkey FIRMS archive.

No network / NASA MAP_KEY.  Fails loudly if the archive path is missing.

Usage (from ``backend/``)::

    python -m src.analytics.confidence_sensitivity \\
        --archive-root data/archive/tr \\
        --bbox 31.20,36.55,31.75,37.05 \\
        --standard-only

Default ROI is the jury demo box (Manavgat / Antalya coast) inside the TR archive.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from src.analytics.reference_comparison import (
    compare_to_viirs_reference,
    product_coverage_table,
    select_evaluation_months,
    summarize_policy_result,
)
from src.data.archive_store import ArchiveStore, point_in_ring, polygon_bbox
from src.data.firms_archive import ARCHIVE_DIR
from src.engine import pipeline as pl
from src.engine.confidence_policies import (
    FIXED_VIIRS_POLICY,
    POLICY_META,
    detection_filter_counts,
    fair_policy_mask,
)
from src.engine.harmonizer_ml import MODEL_PATH, HarmonizerModel
from src.engine.prepare import prepare
from src.engine.sensors import VIIRS_START, n_operating
from src.engine.static_filter import tag_static

# Jury demo ROI (same as frontend DemoGuide) — lat,lon rings → bbox W,S,E,N
DEFAULT_BBOX = (31.20, 36.55, 31.75, 37.05)
DEFAULT_POLYGON = [
    [36.55, 31.20],
    [36.55, 31.75],
    [37.05, 31.75],
    [37.05, 31.20],
    [36.55, 31.20],
]


class ArchiveMissingError(FileNotFoundError):
    """Raised when required local archive files are absent."""


def require_archive(root: Path) -> dict:
    """Validate local archive layout; raise with explicit missing paths."""
    root = Path(root)
    missing = []
    for rel in ("area.json", "progress.json", "raw"):
        if not (root / rel).exists():
            missing.append(str(root / rel))
    if missing:
        raise ArchiveMissingError(
            "Local FIRMS archive incomplete — refusing empty success report. Missing:\n  - "
            + "\n  - ".join(missing)
        )
    area = json.loads((root / "area.json").read_text())
    raw = root / "raw"
    csvs = list(raw.rglob("*.csv"))
    if not csvs:
        raise ArchiveMissingError(
            f"No CSV chunks under {raw} — download the Turkey archive before running sensitivity."
        )
    return area


def _filter_roi(df: pd.DataFrame, bbox, polygon) -> pd.DataFrame:
    if df.empty:
        return df
    if polygon is not None:
        west, south, east, north = polygon_bbox(polygon)
    else:
        west, south, east, north = bbox
    m = (
        (df["longitude"] >= west) & (df["longitude"] <= east)
        & (df["latitude"] >= south) & (df["latitude"] <= north)
    )
    sub = df.loc[m]
    if polygon is not None and len(sub):
        inside = point_in_ring(
            sub["latitude"].to_numpy(dtype=float),
            sub["longitude"].to_numpy(dtype=float),
            polygon,
        )
        sub = sub.loc[inside]
    return sub.reset_index(drop=True)


def load_roi_detections(
    archive_root: Path,
    *,
    bbox=DEFAULT_BBOX,
    polygon=None,
    area_id: str | None = None,
) -> tuple[pd.DataFrame, dict]:
    """Load standardized detections from disk only (no Area API)."""
    area = require_archive(archive_root)
    # Point ArchiveStore at the parent of area_id folder
    root = Path(archive_root)
    parent = root.parent
    aid = area_id or root.name
    # Monkeypatch-free: construct store then override root
    store = ArchiveStore(aid)
    store.root = root
    # Ensure ARCHIVE_DIR parent layout is not required beyond root override
    _ = parent
    raw = store.load(force=False)
    if raw.empty:
        raise ArchiveMissingError(f"Archive at {root} loaded 0 rows from CSV chunks.")
    sub = _filter_roi(raw, bbox, polygon)
    meta = {
        "archive_root": str(root),
        "area": area,
        "bbox": {"west": bbox[0], "south": bbox[1], "east": bbox[2], "north": bbox[3]},
        "rows_archive": int(len(raw)),
        "rows_roi": int(len(sub)),
        "products": area.get("products") or {},
    }
    if sub.empty:
        raise ArchiveMissingError(
            f"ROI filter returned 0 rows inside {meta['bbox']} "
            f"(archive has {len(raw)} rows). Check bbox/polygon."
        )
    return sub, meta


def prepare_for_modis_policy(
    raw: pd.DataFrame,
    model: HarmonizerModel,
    *,
    modis_policy: str = "A",
    viirs_policy: str = FIXED_VIIRS_POLICY,
) -> pd.DataFrame:
    """Prepare detections with fair confidence mask (MODIS varies; VIIRS fixed)."""
    meta_cols = [c for c in ("firms_source", "product_kind", "product_version", "daynight_raw", "nasa_type")
                 if c in raw.columns]
    base = raw.drop(columns=[c for c in meta_cols if c in raw.columns], errors="ignore")
    df = prepare(base.copy())
    for c in meta_cols:
        df[c] = raw[c].to_numpy()
    df = tag_static(df, catalog_cells=None, use_seeds=True, provisional=True)
    if "nasa_type" in df.columns:
        m = (df["nasa_type"] == 2) & ~df["is_static"]
        df.loc[m, "is_static"] = True
        df.loc[m, "static_reason"] = "nasa-type-2"
    df["conf_ok"] = fair_policy_mask(df, modis_policy=modis_policy, viirs_policy=viirs_policy)
    nm = n_operating(df["ts"], "MODIS")
    nv = n_operating(df["ts"], "VIIRS")
    return pl.attach_harmonized(df, model, n_ops_modis=nm, n_ops_viirs=nv)


# Back-compat alias
def prepare_for_policy(raw: pd.DataFrame, model: HarmonizerModel, policy: str) -> pd.DataFrame:
    return prepare_for_modis_policy(raw, model, modis_policy=policy, viirs_policy=FIXED_VIIRS_POLICY)


def evaluate_policies(
    raw: pd.DataFrame,
    *,
    products: dict,
    model: HarmonizerModel,
    policies: tuple[str, ...] = ("A", "B", "C"),
    standard_only: bool = True,
    calib_end: str = "2019-12-01",
) -> dict:
    """Fair A/B/C: same ROI, same months, **fixed VIIRS policy A**, MODIS threshold varies.

    Month set locked from production policy A + product coverage (zeros kept when
    covered; no-coverage months excluded). Platform scale fit on calib months only.
    """
    df_a = prepare_for_modis_policy(raw, model, modis_policy="A", viirs_policy=FIXED_VIIRS_POLICY)
    start = str(df_a["ts"].min().date())
    end = str(df_a["ts"].max().date())
    daily_a = pl.daily_series(df_a, start=start, end=end)
    monthly_a = daily_a.resample("MS").sum(min_count=1)
    coverage = product_coverage_table(products, monthly_a.index)
    month_info = select_evaluation_months(
        monthly_a, coverage,
        require_both_sensors=True,
        standard_only=standard_only,
        post_viirs_only=True,
    )
    mask = month_info["mask"]
    if not mask.any():
        return {
            "error": "No evaluation months after coverage filter",
            "month_selection": month_info,
            "date_range_detections": [start, end],
            "caveat": (
                "Empty month set — not a successful validation. "
                "Check Standard product overlap / ROI."
            ),
        }

    locked_months = monthly_a.index[mask]
    # Fixed VIIRS reference from policy A (never re-filtered under B/C)
    viirs_ref_locked = monthly_a.loc[locked_months, "viirs_native"].to_numpy(dtype=float)
    results = {}
    for pid in policies:
        df = (
            prepare_for_modis_policy(raw, model, modis_policy=pid, viirs_policy=FIXED_VIIRS_POLICY)
            if pid != "A" else df_a
        )
        counts = detection_filter_counts(df, df["conf_ok"].to_numpy())
        daily = pl.daily_series(df, start=start, end=end)
        monthly = daily.resample("MS").sum(min_count=1).reindex(monthly_a.index).fillna(0.0)
        viirs_check = monthly.loc[locked_months, "viirs_native"].to_numpy(dtype=float)
        if not np.allclose(viirs_check, viirs_ref_locked):
            raise RuntimeError(
                f"Fair comparison broken for policy {pid}: VIIRS monthly series changed "
                f"(must stay fixed at policy {FIXED_VIIRS_POLICY})."
            )
        raw_m = monthly.loc[locked_months, "modis_filtered"].to_numpy(dtype=float)
        har_m = monthly.loc[locked_months, "harm_modis_only"].to_numpy(dtype=float)
        metrics = compare_to_viirs_reference(
            viirs_ref_locked, raw_m, har_m, months=locked_months, calib_end=calib_end,
        )
        metrics["n_months_locked"] = int(len(locked_months))
        metrics["locked_month_range"] = [
            str(locked_months.min().date()),
            str(locked_months.max().date()),
        ]
        metrics["viirs_reference_fixed"] = True
        metrics["metric_label"] = "inter_sensor_agreement"
        meta = {
            **POLICY_META.get(pid, {"id": pid}),
            "modis_policy": pid,
            "viirs_policy_fixed": FIXED_VIIRS_POLICY,
        }
        results[pid] = summarize_policy_result(
            policy_id=pid,
            policy_meta=meta,
            filter_counts=counts,
            month_info={**month_info, "months": [str(ts.date()) for ts in locked_months]},
            metrics=metrics,
        )

    kind_counts = {}
    if "product_kind" in raw.columns:
        kind_counts = {str(k): int(v) for k, v in raw["product_kind"].value_counts().items()}
    src_counts = {}
    if "firms_source" in raw.columns:
        src_counts = {str(k): int(v) for k, v in raw["firms_source"].value_counts().items()}
    sat_counts = {}
    if "satellite" in raw.columns:
        sat_counts = {str(k): int(v) for k, v in raw["satellite"].value_counts().items()}

    month_selection_public = {
        k: v for k, v in month_info.items()
        if k != "mask"
    }
    # Quiet months among locked set (both sensors zero)
    n_quiet = int(
        ((monthly_a.loc[locked_months, "viirs_native"] == 0)
         & (monthly_a.loc[locked_months, "modis_filtered"] == 0)).sum()
    )
    return {
        "reference_name": "VIIRS inter-sensor agreement reference (not ground truth)",
        "metric_label": "inter_sensor_agreement",
        "fair_comparison": True,
        "viirs_policy_fixed": FIXED_VIIRS_POLICY,
        "evaluation_kind": "real_firms_archive_sensitivity",
        "training_kind": "synthetic_benchmark_harmonizer",
        "viirs_start": str(VIIRS_START),
        "date_range_detections": [start, end],
        "standard_only": standard_only,
        "month_selection": month_selection_public,
        "locked_months_n": int(len(locked_months)),
        "n_zero_zero_months_kept": n_quiet,
        "calib_end": calib_end,
        "product_kind_counts": kind_counts,
        "product_composition": src_counts,
        "satellites": sat_counts,
        "nrt_mix_note": (
            "ROI mixes Standard and NRT rows; prefer --standard-only for completed "
            "Standard months when claiming long-term comparison."
            if kind_counts.get("nrt") and kind_counts.get("standard")
            else None
        ),
        "policies": results,
        "selection_rule": (
            "Month set locked from policy A + product coverage. MODIS threshold varies "
            "A/B/C while VIIRS filter stays at A. Covered zero–zero months kept; "
            "no-coverage months excluded (not treated as zero fire)."
        ),
        "scientific_claim": (
            "Lower RMSE here measures inter-sensor agreement with VIIRS, not independent "
            "detection accuracy. Harmonizer was trained on synthetic data. "
            "Do not present this as ground-truth validation."
        ),
    }


def run(
    archive_root: Path | str,
    *,
    bbox: tuple[float, float, float, float] = DEFAULT_BBOX,
    polygon=None,
    standard_only: bool = True,
    calib_end: str = "2019-12-01",
    model_path: Path | None = None,
) -> dict:
    root = Path(archive_root)
    raw, meta = load_roi_detections(root, bbox=bbox, polygon=polygon)
    mp = Path(model_path) if model_path else MODEL_PATH
    if not mp.exists():
        raise ArchiveMissingError(f"Harmonizer model missing: {mp}")
    model = HarmonizerModel.load(mp)
    report = evaluate_policies(
        raw,
        products=meta.get("products") or {},
        model=model,
        standard_only=standard_only,
        calib_end=calib_end,
    )
    report["roi"] = meta.get("bbox")
    report["archive_root"] = meta.get("archive_root")
    report["rows_roi"] = meta.get("rows_roi")
    report["rows_archive"] = meta.get("rows_archive")
    return report


def _parse_bbox(s: str) -> tuple[float, float, float, float]:
    parts = [float(x.strip()) for x in s.split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("bbox must be west,south,east,north")
    return parts[0], parts[1], parts[2], parts[3]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--archive-root",
        type=Path,
        default=ARCHIVE_DIR / "tr",
        help="Path to local area archive (must contain area.json, progress.json, raw/)",
    )
    p.add_argument(
        "--bbox",
        type=_parse_bbox,
        default=DEFAULT_BBOX,
        help="west,south,east,north (default: Manavgat demo ROI)",
    )
    p.add_argument(
        "--use-demo-polygon",
        action="store_true",
        help="Filter with the jury demo polygon instead of bbox only",
    )
    p.add_argument(
        "--standard-only",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Restrict evaluation months to overlapping Standard products (default: true)",
    )
    p.add_argument("--calib-end", default="2019-12-01", help="Last month used to learn platform scale")
    p.add_argument("--model", type=Path, default=None, help="Path to harmonizer_quantile.joblib")
    p.add_argument("-o", "--output", type=Path, default=None, help="Write JSON report to this path")
    args = p.parse_args(argv)

    try:
        report = run(
            args.archive_root,
            bbox=args.bbox,
            polygon=DEFAULT_POLYGON if args.use_demo_polygon else None,
            standard_only=args.standard_only,
            calib_end=args.calib_end,
            model_path=args.model,
        )
    except ArchiveMissingError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    text = json.dumps(report, indent=2, default=str)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
        print(f"Wrote {args.output}")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
