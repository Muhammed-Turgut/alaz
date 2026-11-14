"""Persistent thermal-anomaly filter (gas flares, steel plants, volcanoes).

A wildfire is episodic and seasonal.  An industrial flare or volcano is
detected on a large fraction of days of *every* year with little seasonality.
The filter works on **H3 resolution-7 cells**:

  1. for each (cell, year) count distinct detection days and the monthly
     distribution of those days;
  2. a cell-year is "persistent" when distinct days > ``min_days`` (default 90)
     and the monthly coefficient of variation is low (<= ``cv_max``), with at
     least ``min_active_months`` active months;
  3. a cell is a static source when it is persistent in >= ``min_years`` years
     (a 1-ring halo with relaxed thresholds absorbs pixel-position jitter).

Static sources are further typed by FRP variability (steady -> industrial,
episodic -> volcanic).  For short live windows (24 h / 7 d) persistence cannot
be measured directly, so live data are screened against (a) catalogues learnt
from the benchmark archives, (b) a small seed list of well-known sources, and
(c) a *provisional* physical test: on >= 85% of window days, no day/night FRP
contrast (flares have no diurnal cycle) and low FRP variability.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from h3.api import numpy_int as hn

# (name, kind, lat, lon, radius_km) -- approximate, for screening only
KNOWN_SOURCES = [
    ("Kilauea", "volcano", 19.41, -155.28, 6.0),
    ("Mount Etna", "volcano", 37.75, 14.99, 6.0),
    ("Stromboli", "volcano", 38.79, 15.21, 3.0),
    ("Erta Ale", "volcano", 13.60, 40.67, 4.0),
    ("Nyiragongo", "volcano", -1.52, 29.25, 4.0),
    ("Popocatepetl", "volcano", 19.02, -98.62, 4.0),
    ("Sakurajima", "volcano", 31.58, 130.66, 4.0),
    ("Merapi", "volcano", -7.54, 110.44, 4.0),
    ("Rumaila oil field", "gas_flare", 30.45, 47.35, 30.0),
    ("Hassi Messaoud", "gas_flare", 31.70, 6.10, 25.0),
    ("Samotlor oil field", "gas_flare", 61.10, 76.70, 40.0),
]


def _distinct_days(df: pd.DataFrame, cell_col: str) -> pd.DataFrame:
    day = df["ts"].dt.floor("D")
    t = pd.DataFrame({"cell": df[cell_col].to_numpy(), "day": day.to_numpy()}).drop_duplicates()
    t["year"] = t["day"].dt.year
    t["month"] = t["day"].dt.month
    return t


def persistence_table(
    df: pd.DataFrame,
    cell_col: str = "h3_7",
    min_days: int = 90,
    cv_max: float = 0.6,
    min_active_months: int = 9,
    min_years: int = 2,
) -> pd.DataFrame:
    """Per-cell persistence statistics and static-source flag."""
    if df.empty:
        return pd.DataFrame(columns=["cell", "years_flagged", "median_days", "monthly_cv", "static"])
    t = _distinct_days(df, cell_col)
    by_cy = t.groupby(["cell", "year"]).size().rename("days").reset_index()
    cand = by_cy[by_cy["days"] > min_days // 2]  # prune early
    if cand.empty:
        return pd.DataFrame(columns=["cell", "years_flagged", "median_days", "monthly_cv", "static"])
    tt = t.merge(cand[["cell", "year"]], on=["cell", "year"])
    m = tt.groupby(["cell", "year", "month"]).size().unstack("month", fill_value=0)
    m = m.reindex(columns=range(1, 13), fill_value=0)
    days = m.sum(axis=1)
    mean = m.mean(axis=1)
    cv = m.std(axis=1, ddof=0) / mean.replace(0, np.nan)
    active = (m >= 3).sum(axis=1)
    cy = pd.DataFrame({"days": days, "cv": cv, "active": active}).reset_index()
    cy["strict"] = (cy["days"] > min_days) & (cy["cv"] <= cv_max) & (cy["active"] >= min_active_months)
    cy["relaxed"] = (cy["days"] > min_days // 2) & (cy["cv"] <= cv_max * 1.35) & (cy["active"] >= min_active_months - 2)
    n_years_data = t["year"].nunique()
    need = min(min_years, max(1, n_years_data))
    per_cell = cy.groupby("cell").agg(
        years_flagged=("strict", "sum"),
        years_relaxed=("relaxed", "sum"),
        median_days=("days", "median"),
        monthly_cv=("cv", "median"),
    ).reset_index()
    per_cell["static"] = per_cell["years_flagged"] >= need
    # 1-ring halo: neighbours of a static cell that are relaxed-persistent
    static_set = set(per_cell.loc[per_cell["static"], "cell"].astype(np.int64))
    relaxed_set = set(per_cell.loc[(~per_cell["static"]) & (per_cell["years_relaxed"] >= need), "cell"].astype(np.int64))
    halo = set()
    for c in static_set:
        for n in hn.grid_ring(int(c), 1):
            if int(n) in relaxed_set:
                halo.add(int(n))
    per_cell["halo"] = per_cell["cell"].astype(np.int64).isin(halo)
    per_cell["static"] = per_cell["static"] | per_cell["halo"]
    return per_cell


def classify_static_kind(df: pd.DataFrame, static_cells: set, cell_col: str = "h3_7") -> dict:
    """Industrial (steady FRP) vs volcanic (episodic FRP) for each static cell."""
    sub = df[df[cell_col].isin(static_cells)]
    out = {}
    if sub.empty:
        return out
    day = sub["ts"].dt.floor("D")
    daily = sub.groupby([cell_col, day.to_numpy()])["frp"].sum().reset_index()
    daily.columns = ["cell", "day", "frp"]
    for cell, g in daily.groupby("cell"):
        cv = g["frp"].std(ddof=0) / max(g["frp"].mean(), 1e-9)
        out[int(cell)] = "volcano" if cv > 1.1 else "industrial_flare_or_plant"
    return out


def build_persistence_catalog(df: pd.DataFrame, **kw) -> dict:
    table = persistence_table(df, **kw)
    cells = set(table.loc[table["static"], "cell"].astype(np.int64)) if len(table) else set()
    return {"cells": cells, "table": table, "kinds": classify_static_kind(df, cells)}


def _seed_mask(df: pd.DataFrame, seeds=KNOWN_SOURCES) -> np.ndarray:
    if df.empty:
        return np.zeros(0, dtype=bool)
    lat = df["latitude"].to_numpy()
    lon = df["longitude"].to_numpy()
    mask = np.zeros(len(df), dtype=bool)
    for _name, _kind, slat, slon, rad in seeds:
        near = (np.abs(lat - slat) < 1.5) & (np.abs(lon - slon) < 2.5)
        if not near.any():
            continue
        dy = (lat[near] - slat) * 110.574
        dx = (lon[near] - slon) * 111.32 * np.cos(np.radians(slat))
        inside = np.hypot(dx, dy) <= rad
        idx = np.where(near)[0][inside]
        mask[idx] = True
    return mask


def provisional_persistence(df: pd.DataFrame, min_window_days: int = 5, presence: float = 0.85) -> np.ndarray:
    """Physical persistence screen for short live windows -> boolean mask per row."""
    mask = np.zeros(len(df), dtype=bool)
    if df.empty:
        return mask
    day = df["ts"].dt.floor("D")
    n_days = day.nunique()
    if n_days < min_window_days:
        return mask
    g = pd.DataFrame({"cell": df["h3_7"].to_numpy(), "day": day.to_numpy(),
                      "frp": df["frp"].to_numpy(), "night": (df["daynight"].astype(str) == "N").to_numpy()})
    days = g.groupby("cell")["day"].nunique()
    cand = days[days >= presence * n_days].index
    if len(cand) == 0:
        return mask
    gg = g[g["cell"].isin(cand)]
    stats = gg.groupby("cell").agg(frp_mean=("frp", "mean"), frp_std=("frp", "std"))
    stats["cv"] = stats["frp_std"] / stats["frp_mean"].clip(lower=1e-9)
    dn = gg.groupby(["cell", "night"])["frp"].median().unstack("night")
    ratio = (dn.get(False, np.nan) / dn.get(True, np.nan)).rename("dn_ratio")
    stats = stats.join(ratio)
    ok = stats[(stats["cv"] < 0.6) & (stats["dn_ratio"].between(0.6, 1.7))].index
    return df["h3_7"].isin(ok).to_numpy()


def _dilate(cells: set, k: int) -> set:
    out = {int(c) for c in cells}
    for c in list(out):
        out.update(int(x) for x in hn.grid_disk(int(c), k))
    return out


def footprint_aware_static_mask(df: pd.DataFrame, cells: set) -> np.ndarray:
    """Rows whose *footprint* can touch a static source.

    A MODIS pixel is up to ~4.8 km wide, so its centre may fall in a cell next to
    the flagged one (res-7 cell spacing ~2.1 km).  MODIS rows are therefore tested
    against the static set dilated by 1 ring (scan <= 2.2 km) or 2 rings (wider,
    swath-edge pixels).  VIIRS rows use the plain set.
    """
    base = df["h3_7"].isin(cells).to_numpy()
    if "instrument" not in df.columns or "scan" not in df.columns or not cells:
        return base
    modis = (df["instrument"].astype(str) == "MODIS").to_numpy()
    wide = modis & (df["scan"].to_numpy() > 2.2)
    narrow = modis & ~wide
    d1, d2 = _dilate(cells, 1), _dilate(cells, 2)
    return base | (narrow & df["h3_7"].isin(d1).to_numpy()) | (wide & df["h3_7"].isin(d2).to_numpy())


def tag_static(df: pd.DataFrame, catalog_cells: set | None = None, use_seeds: bool = True,
               provisional: bool = False) -> pd.DataFrame:
    """Add ``is_static`` (bool) and ``static_reason`` (str) columns."""
    df = df.copy()
    n = len(df)
    reason = np.full(n, "", dtype=object)
    static = np.zeros(n, dtype=bool)
    if catalog_cells:
        m = footprint_aware_static_mask(df, catalog_cells)
        static |= m
        reason[m] = "persistence-catalog"
    if use_seeds:
        m = _seed_mask(df) & ~static
        static |= m
        reason[m] = "known-source"
    if provisional:
        m = provisional_persistence(df) & ~static
        static |= m
        reason[m] = "provisional-persistence"
    df["is_static"] = static
    df["static_reason"] = reason
    return df


def evaluate_against_truth(df: pd.DataFrame, flagged: np.ndarray) -> dict:
    """Precision / recall of the filter versus simulator ground truth (benchmark only)."""
    truth = df["truth_static"].to_numpy(dtype=bool)
    flagged = np.asarray(flagged, dtype=bool)
    tp = int((truth & flagged).sum())
    fp = int((~truth & flagged).sum())
    fn = int((truth & ~flagged).sum())
    return {
        "precision": tp / max(tp + fp, 1),
        "recall": tp / max(tp + fn, 1),
        "true_static_rows": int(truth.sum()),
        "flagged_rows": int(flagged.sum()),
        "false_positive_rows": fp,
        "false_negative_rows": fn,
    }
