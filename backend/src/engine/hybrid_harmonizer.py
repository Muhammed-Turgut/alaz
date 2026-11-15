"""Hybrid MODIS↔VIIRS harmonization: active-cell grid + dynamic regional α.

NASA Space Apps style pipeline (count-level, not per-detection ML):

1. Hygiene — confidence policy A + valid coordinates / date / FRP
2. Spatial grid — H3-7 (~5 km) or 0.05° cells; daily unique active cells
3. Fit ``Y = α X`` (VIIRS vs MODIS monthly active-cell counts, no intercept)
   on the 2012– overlap window
4. Fallback α when n < 20 or α ∉ [0.8, 10]
5. Continuous series — post-VIIRS: VIIRS cells; pre-VIIRS: round(MODIS × α)

Does **not** invent coordinates. Raw detection counts stay available separately.
"""
from __future__ import annotations

from typing import Any, Literal

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression

from src.engine import geometry_h3 as geo
from src.engine.sensors import VIIRS_START

GridKind = Literal["h3", "deg005"]

DEFAULT_FALLBACK_ALPHA = 2.5
ALPHA_LO = 0.8
ALPHA_HI = 10.0
MIN_CALIB_MONTHS = 20
VSTART = pd.Timestamp(str(VIIRS_START))


def hygiene_mask(df: pd.DataFrame) -> np.ndarray:
    """Keep rows that pass confidence A + finite lat/lon/ts/frp."""
    n = len(df)
    if n == 0:
        return np.zeros(0, dtype=bool)
    conf_ok = (
        df["conf_ok"].to_numpy(dtype=bool)
        if "conf_ok" in df.columns
        else np.ones(n, dtype=bool)
    )
    static = (
        df["is_static"].to_numpy(dtype=bool)
        if "is_static" in df.columns
        else np.zeros(n, dtype=bool)
    )
    lat = pd.to_numeric(df["latitude"], errors="coerce")
    lon = pd.to_numeric(df["longitude"], errors="coerce")
    frp = pd.to_numeric(df["frp"], errors="coerce") if "frp" in df.columns else pd.Series(0.0, index=df.index)
    ts = pd.to_datetime(df["ts"], errors="coerce")
    geo_ok = lat.notna() & lon.notna() & (lat >= -90) & (lat <= 90) & (lon >= -180) & (lon <= 180)
    frp_ok = frp.notna() & (frp >= 0)
    ts_ok = ts.notna()
    return conf_ok & ~static & geo_ok.to_numpy() & frp_ok.to_numpy() & ts_ok.to_numpy()


def assign_grid(df: pd.DataFrame, grid: GridKind = "h3", h3_res: int = 7) -> pd.Series:
    """Return a string grid_id per row (H3 cell or 0.05° tile)."""
    lat = df["latitude"].to_numpy(dtype=float)
    lon = df["longitude"].to_numpy(dtype=float)
    if grid == "deg005":
        # Fixed 0.05° tiles (~5 km); id is "i_j" in tile indices
        i = np.floor(lat / 0.05).astype(np.int64)
        j = np.floor(lon / 0.05).astype(np.int64)
        return pd.Series([f"{a}_{b}" for a, b in zip(i, j)], index=df.index)
    # Default: H3 (reuse prepared column when present)
    col = "h3_7" if h3_res == 7 and "h3_7" in df.columns else None
    if col is not None:
        return df[col].map(lambda c: geo.h3_str(int(c)))
    cells = geo.h3_index(lat, lon, h3_res)
    return pd.Series([geo.h3_str(int(c)) for c in cells], index=df.index)


def daily_active_cells(df: pd.DataFrame) -> pd.DataFrame:
    """Per day × instrument: n unique grid cells + FRP sum/mean."""
    if df.empty:
        return pd.DataFrame(columns=[
            "date", "instrument", "n_cells", "frp_sum", "frp_mean", "n_detections",
        ])
    g = df.copy()
    g["date"] = pd.to_datetime(g["ts"]).dt.floor("D")
    g["instrument"] = g["instrument"].astype(str).str.upper()
    rows = []
    for (day, inst), sub in g.groupby(["date", "instrument"], sort=True):
        cells = sub["grid_id"].nunique()
        frp = pd.to_numeric(sub["frp"], errors="coerce").fillna(0.0)
        rows.append({
            "date": pd.Timestamp(day),
            "instrument": inst,
            "n_cells": int(cells),
            "frp_sum": float(frp.sum()),
            "frp_mean": float(frp.mean()) if len(frp) else 0.0,
            "n_detections": int(len(sub)),
        })
    return pd.DataFrame(rows)


def monthly_active_from_daily(daily: pd.DataFrame) -> pd.DataFrame:
    """Sum daily active-cell counts into calendar months per instrument."""
    if daily.empty:
        return pd.DataFrame(columns=["month", "MODIS", "VIIRS", "raw_modis_det", "raw_viirs_det"])
    d = daily.copy()
    d["month"] = d["date"].dt.to_period("M").dt.to_timestamp()
    pivot_cells = d.pivot_table(
        index="month", columns="instrument", values="n_cells", aggfunc="sum", fill_value=0.0,
    )
    pivot_det = d.pivot_table(
        index="month", columns="instrument", values="n_detections", aggfunc="sum", fill_value=0.0,
    )
    out = pd.DataFrame(index=pivot_cells.index.sort_values())
    out["MODIS"] = pivot_cells["MODIS"] if "MODIS" in pivot_cells.columns else 0.0
    out["VIIRS"] = pivot_cells["VIIRS"] if "VIIRS" in pivot_cells.columns else 0.0
    out["raw_modis_det"] = pivot_det["MODIS"] if "MODIS" in pivot_det.columns else 0.0
    out["raw_viirs_det"] = pivot_det["VIIRS"] if "VIIRS" in pivot_det.columns else 0.0
    return out.reset_index().rename(columns={"index": "month"})


def fit_alpha(
    monthly: pd.DataFrame,
    *,
    calib_start: str | pd.Timestamp = "2012-01-01",
    calib_end: str | pd.Timestamp | None = None,
    min_months: int = MIN_CALIB_MONTHS,
    alpha_lo: float = ALPHA_LO,
    alpha_hi: float = ALPHA_HI,
    fallback_alpha: float = DEFAULT_FALLBACK_ALPHA,
) -> dict[str, Any]:
    """OLS through origin: VIIRS_cells = α · MODIS_cells on overlap months."""
    if monthly.empty:
        return {
            "value": float(fallback_alpha),
            "r2": None,
            "n_months": 0,
            "fallback_used": True,
            "fallback_reason": "no_monthly_data",
            "bounds": [alpha_lo, alpha_hi],
            "fallback_alpha": float(fallback_alpha),
        }
    m = monthly.copy()
    m["month"] = pd.to_datetime(m["month"])
    lo = pd.Timestamp(calib_start)
    hi = pd.Timestamp(calib_end) if calib_end else m["month"].max()
    # Prefer post-VIIRS overlap
    lo = max(lo, VSTART.to_period("M").to_timestamp())
    win = m[(m["month"] >= lo) & (m["month"] <= hi)].copy()
    # Months where both sensors have activity (or at least MODIS for undercount months —
    # require both > 0 for a meaningful α pair)
    both = win[(win["MODIS"] > 0) & (win["VIIRS"] > 0)]
    n = int(len(both))
    if n < min_months:
        return {
            "value": float(fallback_alpha),
            "r2": None,
            "n_months": n,
            "fallback_used": True,
            "fallback_reason": f"n_months<{min_months}",
            "bounds": [alpha_lo, alpha_hi],
            "fallback_alpha": float(fallback_alpha),
            "calib_window": [str(lo.date()), str(hi.date())],
        }
    X = both["MODIS"].to_numpy(dtype=float).reshape(-1, 1)
    y = both["VIIRS"].to_numpy(dtype=float)
    model = LinearRegression(fit_intercept=False)
    model.fit(X, y)
    alpha = float(model.coef_[0])
    y_hat = model.predict(X)
    ss_res = float(np.sum((y - y_hat) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r2 = float(1.0 - ss_res / ss_tot) if ss_tot > 0 else None
    if not np.isfinite(alpha) or alpha < alpha_lo or alpha > alpha_hi:
        return {
            "value": float(fallback_alpha),
            "r2": r2,
            "n_months": n,
            "fallback_used": True,
            "fallback_reason": f"alpha_out_of_bounds ({alpha:.4f})",
            "fitted_alpha": alpha,
            "bounds": [alpha_lo, alpha_hi],
            "fallback_alpha": float(fallback_alpha),
            "calib_window": [str(lo.date()), str(hi.date())],
        }
    return {
        "value": alpha,
        "r2": r2,
        "n_months": n,
        "fallback_used": False,
        "fallback_reason": None,
        "bounds": [alpha_lo, alpha_hi],
        "fallback_alpha": float(fallback_alpha),
        "calib_window": [str(lo.date()), str(hi.date())],
    }


def build_harmonized_monthly(monthly: pd.DataFrame, alpha: float) -> pd.DataFrame:
    """Pre-VIIRS: round(MODIS×α); post-VIIRS: VIIRS active cells."""
    if monthly.empty:
        return monthly
    out = monthly.copy()
    out["month"] = pd.to_datetime(out["month"])
    out["applied_alpha"] = float(alpha)
    pre = out["month"] < VSTART.to_period("M").to_timestamp()
    out["harmonized_activity"] = np.where(
        pre,
        np.round(out["MODIS"].to_numpy(dtype=float) * float(alpha)),
        out["VIIRS"].to_numpy(dtype=float),
    )
    return out


def attach_historical_median_anomaly(monthly: pd.DataFrame) -> pd.DataFrame:
    """Per-month historical median (other years, same calendar month) + anomaly_pct."""
    if monthly.empty:
        return monthly
    out = monthly.copy()
    out["year"] = out["month"].dt.year
    out["month_num"] = out["month"].dt.month
    medians = []
    anomalies = []
    for _, row in out.iterrows():
        same = out[
            (out["month_num"] == row["month_num"]) & (out["year"] != row["year"])
        ]["harmonized_activity"]
        if len(same) == 0:
            medians.append(None)
            anomalies.append(None)
            continue
        med = float(np.median(same.to_numpy(dtype=float)))
        medians.append(med)
        cur = float(row["harmonized_activity"])
        if med <= 1e-12:
            anomalies.append(None if cur == 0 else 100.0)
        else:
            anomalies.append(float(100.0 * (cur / med - 1.0)))
    out["historical_median"] = medians
    out["anomaly_pct"] = anomalies
    return out


def daily_harmonized_series(daily_cells: pd.DataFrame, alpha: float) -> pd.Series:
    """Daily harmonized active-cell activity for calendar heatmaps."""
    if daily_cells.empty:
        return pd.Series(dtype=float)
    d = daily_cells.copy()
    d["date"] = pd.to_datetime(d["date"])
    idx = pd.date_range(d["date"].min(), d["date"].max(), freq="D")
    mod = (
        d.loc[d["instrument"] == "MODIS"]
        .groupby("date")["n_cells"].sum()
        .reindex(idx, fill_value=0.0)
    )
    vir = (
        d.loc[d["instrument"] == "VIIRS"]
        .groupby("date")["n_cells"].sum()
        .reindex(idx, fill_value=0.0)
    )
    pre = idx < VSTART
    vals = np.where(pre, np.round(mod.to_numpy(dtype=float) * float(alpha)), vir.to_numpy(dtype=float))
    return pd.Series(vals, index=idx, name="harmonized_activity")


def grid_cell_boundary(grid_id: str, grid: GridKind) -> list[list[float]] | None:
    """[[lat, lon], ...] for map polygons."""
    if grid == "h3":
        try:
            return geo.cell_boundary(grid_id)
        except Exception:
            return None
    # 0.05° rectangle
    try:
        i_s, j_s = grid_id.split("_", 1)
        i, j = int(i_s), int(j_s)
    except Exception:
        return None
    lat0, lon0 = i * 0.05, j * 0.05
    lat1, lon1 = lat0 + 0.05, lon0 + 0.05
    return [
        [lat0, lon0], [lat0, lon1], [lat1, lon1], [lat1, lon0], [lat0, lon0],
    ]


def grid_cell_center(grid_id: str, grid: GridKind) -> tuple[float, float] | None:
    if grid == "h3":
        try:
            return geo.cell_center(grid_id)
        except Exception:
            return None
    try:
        i_s, j_s = grid_id.split("_", 1)
        i, j = int(i_s), int(j_s)
    except Exception:
        return None
    return (i * 0.05 + 0.025, j * 0.05 + 0.025)


def map_layers(
    clean: pd.DataFrame,
    alpha: float,
    *,
    grid: GridKind = "h3",
    recent_days: int | None = 365,
    start: str | pd.Timestamp | None = None,
    end: str | pd.Timestamp | None = None,
) -> dict[str, Any]:
    """Harmonized grid density + real VIIRS point coordinates (no invented coords)."""
    if clean.empty:
        return {"harmonized_cells": [], "viirs_points": []}
    ts = pd.to_datetime(clean["ts"])
    data_end = ts.max()
    data_start = ts.min()
    if start is not None or end is not None:
        win_end = pd.Timestamp(end) if end is not None else data_end
        win_start = pd.Timestamp(start) if start is not None else data_start
    else:
        win_end = data_end
        win_start = win_end - pd.Timedelta(days=max(1, int(recent_days or 365)))
    win = clean.loc[(ts >= win_start) & (ts <= win_end)].copy()
    # VIIRS points — real lat/lon only
    v = win[win["instrument"].astype(str).str.upper() == "VIIRS"]
    viirs_points = []
    for _, r in v.iterrows():
        viirs_points.append({
            "lat": float(r["latitude"]),
            "lon": float(r["longitude"]),
            "ts": str(pd.Timestamp(r["ts"])),
            "frp": float(r["frp"]) if pd.notna(r.get("frp")) else 0.0,
            "confidence": float(r["confidence"]) if pd.notna(r.get("confidence")) else None,
            "satellite": str(r.get("satellite") or ""),
        })
    # Grid: MODIS contribution scaled by α + VIIRS cells (unique cells, weight = activity)
    cells: dict[str, float] = {}
    frp_sum: dict[str, float] = {}
    for _, r in win.iterrows():
        gid = str(r["grid_id"])
        inst = str(r["instrument"]).upper()
        w = float(alpha) if inst == "MODIS" else 1.0
        cells[gid] = cells.get(gid, 0.0) + w
        frp_sum[gid] = frp_sum.get(gid, 0.0) + float(r["frp"] if pd.notna(r.get("frp")) else 0.0)
    harmonized_cells = []
    for gid, val in cells.items():
        center = grid_cell_center(gid, grid)
        if center is None:
            continue
        lat, lon = center
        harmonized_cells.append({
            "grid_id": gid,
            "h3": gid if grid == "h3" else None,
            "lat": lat,
            "lon": lon,
            "boundary": grid_cell_boundary(gid, grid),
            "n": float(val),
            "frp_sum": frp_sum.get(gid, 0.0),
            "w": float(val),
        })
    harmonized_cells.sort(key=lambda c: -c["n"])
    return {
        "harmonized_cells": harmonized_cells[:5000],
        "viirs_points": viirs_points[:8000],
        "window": [str(pd.Timestamp(win_start).date()), str(pd.Timestamp(win_end).date())],
    }


def monthly_csv_rows(monthly: pd.DataFrame) -> list[dict[str, Any]]:
    rows = []
    for _, r in monthly.iterrows():
        rows.append({
            "year": int(r["year"]),
            "month": int(r["month_num"]),
            "raw_modis": float(r["MODIS"]),
            "raw_viirs": float(r["VIIRS"]),
            "applied_alpha": float(r["applied_alpha"]),
            "harmonized_activity": float(r["harmonized_activity"]),
            "historical_median": (
                None if r["historical_median"] is None or (isinstance(r["historical_median"], float) and np.isnan(r["historical_median"]))
                else float(r["historical_median"])
            ),
            "anomaly_pct": (
                None if r["anomaly_pct"] is None or (isinstance(r["anomaly_pct"], float) and np.isnan(r["anomaly_pct"]))
                else float(r["anomaly_pct"])
            ),
        })
    return rows


def run_hybrid(
    df: pd.DataFrame,
    *,
    grid: GridKind = "h3",
    h3_res: int = 7,
    fallback_alpha: float = DEFAULT_FALLBACK_ALPHA,
    calib_start: str = "2012-01-01",
    calib_end: str | None = None,
    map_recent_days: int | None = 365,
    map_start: str | None = None,
    map_end: str | None = None,
) -> dict[str, Any]:
    """Full hybrid pack from a prepared FIRMS detection frame."""
    raw_n = int(len(df))
    mask = hygiene_mask(df)
    clean = df.loc[mask].copy()
    dropped = raw_n - int(len(clean))
    if clean.empty:
        return {
            "empty": True,
            "message": "No detections after confidence / validity filters.",
            "hygiene": {"raw_count": raw_n, "kept": 0, "dropped": dropped},
            "alpha": fit_alpha(pd.DataFrame(), fallback_alpha=fallback_alpha),
            "monthly": [],
            "map": {"harmonized_cells": [], "viirs_points": []},
        }

    clean["grid_id"] = assign_grid(clean, grid=grid, h3_res=h3_res)
    daily = daily_active_cells(clean)
    monthly = monthly_active_from_daily(daily)
    alpha_info = fit_alpha(
        monthly,
        calib_start=calib_start,
        calib_end=calib_end,
        fallback_alpha=fallback_alpha,
    )
    alpha = float(alpha_info["value"])
    harm = build_harmonized_monthly(monthly, alpha)
    harm = attach_historical_median_anomaly(harm)
    daily_h = daily_harmonized_series(daily, alpha)
    layers = map_layers(
        clean, alpha, grid=grid,
        recent_days=map_recent_days,
        start=map_start, end=map_end,
    )

    # Raw vs harmonized for transition chart (monthly)
    chart = []
    for _, r in harm.iterrows():
        chart.append({
            "label": r["month"].strftime("%Y-%m"),
            "raw_modis": float(r["MODIS"]),
            "raw_viirs": float(r["VIIRS"]),
            "harmonized": float(r["harmonized_activity"]),
        })

    return {
        "empty": False,
        "hygiene": {
            "raw_count": raw_n,
            "kept": int(len(clean)),
            "dropped": dropped,
            "filter": "policy_A + valid lat/lon/ts/frp + non-static",
        },
        "grid": grid,
        "h3_res": h3_res if grid == "h3" else None,
        "alpha": alpha_info,
        "monthly": monthly_csv_rows(harm),
        "daily_harmonized": {
            "labels": [str(ts.date()) for ts in daily_h.index],
            "values": [float(v) for v in daily_h.to_numpy()],
        },
        "transition_chart": chart,
        "map": layers,
        "viirs_start": str(VSTART.date()),
        "note": (
            "harmonized_activity = VIIRS active cells (post-2012-01-20) or "
            "round(MODIS_active_cells × α) (pre-VIIRS). Active cell ≠ fire event."
        ),
    }


def monthly_to_csv(monthly_rows: list[dict]) -> str:
    cols = [
        "year", "month", "raw_modis", "raw_viirs", "applied_alpha",
        "harmonized_activity", "historical_median", "anomaly_pct",
    ]
    lines = [",".join(cols)]
    for r in monthly_rows:
        vals = []
        for c in cols:
            v = r.get(c)
            if v is None:
                vals.append("")
            else:
                vals.append(str(v))
        lines.append(",".join(vals))
    return "\n".join(lines) + "\n"
