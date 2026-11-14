"""Common detection-schema preparation shared by training, archives and live data.

Input schema (any source): latitude, longitude, ts (UTC), satellite, instrument,
scan, track, frp  [+ bright_t21, bright_t31, confidence (0-100), daynight].
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.engine import geometry_h3 as geo
from src.engine.biomes import biome_codes_from_h3_7
from src.engine.diurnal_normalizer import (
    diurnal_factor,
    normalize_frp,
    reference_lst,
    utc_to_lst,
)

REQUIRED = ["latitude", "longitude", "ts", "satellite", "instrument", "scan", "track", "frp"]


def confidence_ok(df: pd.DataFrame) -> np.ndarray:
    """Drop low-confidence detections consistently for both sensors.

    MODIS: confidence >= 30 (i.e. not 'low').  VIIRS classes are mapped
    low=30 / nominal=65 / high=90, so 'low' is excluded with > 30.
    """
    conf = pd.to_numeric(df["confidence"], errors="coerce").fillna(65).to_numpy()
    is_modis = (df["instrument"].astype(str) == "MODIS").to_numpy()
    return np.where(is_modis, conf >= 30, conf > 30)


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
    if "daynight" not in df.columns:
        df["daynight"] = np.where((df["lst"] >= 6) & (df["lst"] < 18), "D", "N")
    df["daynight"] = df["daynight"].astype(str)
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
    return df
