"""Satellite constellation calendar: which platforms were observing when.

Counts are used to normalize multi-platform detections into a per-instrument
equivalent series so adding a new platform does not create a step artifact.
Residual sensor/product transitions are still visible to the user — this is
not a claim that every break is removed.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# Operational windows (UTC calendar dates). SNPP NRT production ended 2026-11-02;
# historical Standard archive through that era remains valid for archive queries.
SENSORS = {
    "Terra": dict(instrument="MODIS", start="2000-12-01", end=None),
    "Aqua": dict(instrument="MODIS", start="2002-07-04", end=None),
    "SNPP": dict(instrument="VIIRS", start="2012-01-20", end="2026-11-02"),
    "NOAA20": dict(instrument="VIIRS", start="2018-04-01", end=None),
    # FIRMS NOAA-21 VIIRS active-fire availability (user/demo calendar marker: 2024).
    "NOAA21": dict(instrument="VIIRS", start="2024-01-01", end=None),
}

VIIRS_START = np.datetime64(SENSORS["SNPP"]["start"])
NOAA20_START = np.datetime64(SENSORS["NOAA20"]["start"])
NOAA21_START = np.datetime64(SENSORS["NOAA21"]["start"])
SNPP_END = np.datetime64(SENSORS["SNPP"]["end"])
MODIS_RECORD_START = np.datetime64("2001-01-01")

# Product / constellation markers for charts (UTC).
TIMELINE_MARKERS = [
    {"id": "viirs_snpp_start", "date": "2012-01-20", "label": "VIIRS SNPP start"},
    {"id": "noaa20_start", "date": "2018-04-01", "label": "NOAA-20 VIIRS start"},
    {"id": "noaa21_start", "date": "2024-01-01", "label": "NOAA-21 VIIRS start"},
    {"id": "snpp_nrt_end", "date": "2026-11-02", "label": "SNPP NRT production end"},
]


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


def platform_contribution(ts, instrument: str) -> np.ndarray:
    """Per-detection weight 1/n_operating so multi-platform sums stay continuous."""
    return 1.0 / n_operating(ts, instrument)
