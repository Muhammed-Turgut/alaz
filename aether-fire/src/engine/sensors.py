"""Satellite constellation calendar: which platforms were observing when."""
from __future__ import annotations

import numpy as np
import pandas as pd

SENSORS = {
    "Terra": dict(instrument="MODIS", start="2000-12-01", end=None),
    "Aqua": dict(instrument="MODIS", start="2002-07-04", end=None),
    "SNPP": dict(instrument="VIIRS", start="2012-01-20", end=None),
    "NOAA20": dict(instrument="VIIRS", start="2018-04-01", end=None),
}

VIIRS_START = np.datetime64(SENSORS["SNPP"]["start"])
MODIS_RECORD_START = np.datetime64("2001-01-01")


def n_operating(ts, instrument: str) -> np.ndarray:
    """Number of platforms of ``instrument`` operating at each timestamp."""
    t = pd.to_datetime(pd.Series(ts)).to_numpy().astype("datetime64[D]")
    n = np.zeros(len(t), dtype=float)
    for spec in SENSORS.values():
        if spec["instrument"] != instrument:
            continue
        on = t >= np.datetime64(spec["start"])
        if spec["end"]:
            on &= t <= np.datetime64(spec["end"])
        n += on
    return np.maximum(n, 1.0)
