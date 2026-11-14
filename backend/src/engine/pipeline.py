"""Orchestration: archives -> prepared frames -> static catalog -> training -> series."""
from __future__ import annotations

import logging
import time

import numpy as np
import pandas as pd

from src.data import benchmark_archives as arch
from src.data.regions import REGIONS
from src.engine.harmonizer_ml import HarmonizerModel, build_matchups
from src.engine.prepare import prepare
from src.engine.sensors import VIIRS_START
from src.engine.static_filter import build_persistence_catalog, tag_static

log = logging.getLogger("aether.pipeline")

TRAIN_YEARS = (2012, 2019)
CALIB_YEARS = (2020, 2021)
TEST_YEARS = (2022, 2026)


def prepare_region(data: dict):
    """Prepare detections + counterfactual frame and learn the persistence catalog."""
    df = prepare(data["df"])
    catalog = build_persistence_catalog(df)
    df = tag_static(df, catalog["cells"], use_seeds=False)
    cf = prepare(data["cf"]) if len(data["cf"]) else pd.DataFrame()
    if len(cf):
        cf = tag_static(cf, catalog["cells"], use_seeds=False)
    return df, cf, catalog


def _years(df, lo, hi):
    return df[(df["year"] >= lo) & (df["year"] <= hi)]


def train_harmonizer(regions: dict, progress=None) -> HarmonizerModel:
    """regions: {region_id: prepared df}.  Returns a fitted model (train/calib/test by year)."""
    mt_parts, snpp_counts = [], []
    for rid, df in regions.items():
        ok = df[df["conf_ok"] & ~df["is_static"]]
        modis = ok[(ok["instrument"] == "MODIS") & (ok["ts"] >= pd.Timestamp(str(VIIRS_START)))]
        snpp = ok[ok["satellite"] == "SNPP"]
        if progress:
            progress(f"matching footprints [{rid}]: {len(modis):,} MODIS x {len(snpp):,} SNPP")
        mm = build_matchups(modis, snpp)
        mm["region"] = rid
        mt_parts.append(mm)
        snpp_counts.append(snpp[snpp["year"] <= CALIB_YEARS[1]].groupby(["biome", "daynight"]).size()
                           .rename("n").reset_index())
    mt = pd.concat(mt_parts, ignore_index=True)
    train = _years(mt, *TRAIN_YEARS)
    calib = _years(mt, *CALIB_YEARS)
    test = _years(mt, *TEST_YEARS)
    if progress:
        progress(f"matchups train={len(train):,} calib={len(calib):,} test={len(test):,}")
    model = HarmonizerModel().fit(train, calib, test)

    cal = _years(mt, TRAIN_YEARS[0], CALIB_YEARS[1])
    cal_pred = model.predict(cal)
    cal = cal.assign(n_mean=cal_pred["n_mean"].to_numpy())
    vc = pd.concat(snpp_counts).groupby(["biome", "daynight"], as_index=False)["n"].sum()
    model.fit_calibration(cal, vc)
    model.matchup_summary = {
        "n_matchups": int(len(mt)), "n_train": int(len(train)), "n_calib": int(len(calib)), "n_test": int(len(test)),
        "train_years": TRAIN_YEARS, "calib_years": CALIB_YEARS, "test_years": TEST_YEARS,
        "frac_modis_with_viirs_counterpart": float((mt["n_eq"] > 0).mean()),
    }
    return model


# --------------------------------------------------------------------------
# harmonized weights + series
# --------------------------------------------------------------------------
def attach_harmonized(df: pd.DataFrame, model: HarmonizerModel, n_ops_modis=None, n_ops_viirs=None) -> pd.DataFrame:
    """Add h_w / h_sd / h_lo / h_hi (VIIRS-scale hotspot-equivalent contribution) and
    per-detection FRP harmonization columns.  Static / low-confidence rows get 0."""
    df = df.copy()
    for c in ("h_w", "h_sd", "h_lo", "h_hi", "frp_harm", "frp_harm_lo", "frp_harm_hi"):
        df[c] = 0.0
    ok = (df["conf_ok"] & ~df["is_static"]).to_numpy()
    sub = df[ok]
    if sub.empty:
        return df
    from src.engine.harmonizer_ml import add_cell_context
    sub = add_cell_context(sub)
    is_modis = (sub["instrument"] == "MODIS").to_numpy()
    df["log_cell_n"] = np.nan
    df["log_cell_frp"] = np.nan
    df.loc[sub.index, ["log_cell_n", "log_cell_frp"]] = sub[["log_cell_n", "log_cell_frp"]].to_numpy()
    pred = pd.DataFrame(index=sub.index)
    if is_modis.any():
        pred = model.predict(sub[is_modis])
    nm = None if n_ops_modis is None else n_ops_modis
    h = model.harmonize(sub, pred if len(pred) else None, nm, n_ops_viirs)
    df.loc[sub.index, ["h_w", "h_sd", "h_lo", "h_hi"]] = h.to_numpy()
    if len(pred):
        df.loc[pred.index, "frp_harm"] = pred["frp_p50"].to_numpy()
        df.loc[pred.index, "frp_harm_lo"] = pred["frp_lo90"].to_numpy()
        df.loc[pred.index, "frp_harm_hi"] = pred["frp_hi90"].to_numpy()
    # VIIRS rows are native: FRP is as measured
    v = sub[~is_modis]
    if len(v):
        df.loc[v.index, "frp_harm"] = v["frp"].to_numpy()
        df.loc[v.index, "frp_harm_lo"] = v["frp"].to_numpy()
        df.loc[v.index, "frp_harm_hi"] = v["frp"].to_numpy()
    return df


def daily_series(df: pd.DataFrame, cf: pd.DataFrame | None = None,
                 start="2001-01-01", end="2026-11-14") -> pd.DataFrame:
    """Daily series for one dataset (all columns are counts / VIIRS-scale hotspot equivalents)."""
    idx = pd.date_range(start, end, freq="D")
    day = df["ts"].dt.floor("D")
    ok = (df["conf_ok"] & ~df["is_static"]).to_numpy()
    modis = (df["instrument"] == "MODIS").to_numpy()

    def cnt(mask):
        return pd.Series(1.0, index=day[mask]).groupby(level=0).sum().reindex(idx, fill_value=0.0)

    def wsum(mask, col):
        return df.loc[mask, col].groupby(day[mask]).sum().reindex(idx, fill_value=0.0)

    out = pd.DataFrame(index=idx)
    out["raw_all"] = cnt(np.ones(len(df), dtype=bool))
    out["raw_modis"] = cnt(modis)
    out["raw_viirs"] = cnt(~modis)
    out["raw_filtered"] = cnt(ok)
    out["modis_filtered"] = cnt(ok & modis)
    out["static_removed"] = cnt(df["is_static"].to_numpy())
    out["harm_modis_only"] = wsum(ok & modis, "h_w")
    out["harm_modis_var"] = (df["h_sd"] ** 2).where(ok & modis, 0.0).groupby(day).sum().reindex(idx, fill_value=0.0)
    out["viirs_native"] = wsum(ok & ~modis, "h_w")
    viirs_start = pd.Timestamp(str(VIIRS_START))
    pre = idx < viirs_start
    out["harmonized"] = np.where(pre, out["harm_modis_only"], out["viirs_native"])
    out["harmonized_var"] = np.where(pre, out["harm_modis_var"], 0.0)
    if cf is not None and len(cf):
        cd = cf["ts"].dt.floor("D")
        cok = (cf["conf_ok"] & ~cf["is_static"]).to_numpy()
        out["counterfactual_snpp"] = pd.Series(1.0, index=cd[cok]).groupby(level=0).sum().reindex(idx, fill_value=0.0)
        out.loc[~pre, "counterfactual_snpp"] = np.nan
    else:
        out["counterfactual_snpp"] = np.nan
    return out


def monthly(daily: pd.DataFrame) -> pd.DataFrame:
    return daily.resample("MS").sum(min_count=1)
