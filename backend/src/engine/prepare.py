"""Common detection-schema preparation shared by training, archives and live data.

Input schema (any source): latitude, longitude, ts (UTC), satellite, instrument,
scan, track, frp  [+ bright_t21, bright_t31, confidence (0-100), daynight].
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.engine import geometry_h3 as geo
from src.engine.biomes import biome_codes_from_h3_7
from src.engine.confidence_policies import PRIMARY_POLICY, policy_current
from src.engine.diurnal_normalizer import (
    diurnal_factor,
    normalize_frp,
    reference_lst,
    utc_to_lst,
)

REQUIRED = ["latitude", "longitude", "ts", "satellite", "instrument", "scan", "track", "frp"]


def confidence_ok(df: pd.DataFrame) -> np.ndarray:
    """Production confidence filter (policy A) — do not retune for prettier plots.

    MODIS: confidence >= 30.  VIIRS: exclude mapped ``low`` (keep nominal/high).
    VIIRS class labels are **not** the same scale as MODIS 0–100; mapped
    scores 30/65/90 are a filter convenience only.  Alternate policies B/C are
    compared in ``confidence_policies.confidence_filter_sensitivity`` without
    replacing this main-result mask.
    """
    return policy_current(df)


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise ValueError(f"missing columns: {missing}")
    df = df.copy()
    for c in ("satellite", "instrument"):
        df[c] = df[c].astype(str)
    df["ts"] = pd.to_datetime(df["ts"])
    for c, default in (("bright_t21", 330.0), ("bright_t31", 290.0), ("confidence", 65)):
        if c not in df.columns:
            df[c] = default
    df = geo.add_h3_columns(df)
    df["lst"] = utc_to_lst(df["ts"], df["longitude"].to_numpy())
    # LST-consistent day/night (local solar hour). NRT vs Standard product daynight
    # fields can differ in meaning — keep the product value as daynight_raw and do
    # not silently treat them as interchangeable for science claims.
    df["daynight_lst"] = np.where((df["lst"] >= 6) & (df["lst"] < 18), "D", "N")
    if "daynight_raw" not in df.columns:
        if "daynight" in df.columns:
            df["daynight_raw"] = df["daynight"].astype(str)
        else:
            df["daynight_raw"] = "unknown"
    if "daynight" not in df.columns:
        df["daynight"] = df["daynight_lst"]
    df["daynight"] = df["daynight"].astype(str)
    # Unknown product daynight → use LST for model features only
    unk = df["daynight"].isin(["", "nan", "None", "unknown", "UNKNOWN"])
    if unk.any():
        df.loc[unk, "daynight"] = df.loc[unk, "daynight_lst"]
    local = df["ts"] + pd.to_timedelta(df["longitude"].to_numpy() / 15.0, unit="h")
    df["ldate"] = local.dt.floor("D")
    df["year"] = df["ts"].dt.year
    df["month"] = df["ts"].dt.month
    df["doy"] = df["ts"].dt.dayofyear
    df["area_km2"] = geo.footprint_area_km2(df["scan"].to_numpy(), df["track"].to_numpy())
    df["lst_ref"] = reference_lst(df["lst"].to_numpy())
    df["d_factor"] = diurnal_factor(df["lst"].to_numpy())
    df["diurnal_ratio"] = np.log(diurnal_factor(df["lst_ref"].to_numpy()) / df["d_factor"].to_numpy())
    df["frp_norm"] = normalize_frp(df["frp"].to_numpy(), df["lst"].to_numpy(), df["lst_ref"].to_numpy())
    df["biome"] = biome_codes_from_h3_7(df["h3_7"].to_numpy())
    df["conf_ok"] = confidence_ok(df)
    df["conf_policy"] = PRIMARY_POLICY
    return df
