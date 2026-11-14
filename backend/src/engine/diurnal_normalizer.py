"""Local Solar Time (LST) conversion and diurnal fire-intensity normalisation.

Fire activity follows a strong diurnal cycle (afternoon peak, pre-dawn trough).
Terra (10:30) and Aqua (13:30, drifting later since 2022) therefore see the
same fire at different intensity, and the Terra/Aqua *orbital drift* changes
that sampling over time.  We model the cycle with a circular Gaussian

    D(t) = floor + (1 - floor) * exp(-0.5 * (circ(t - 14.5h) / sigma)^2)

(peak ~14:30 LST, trough ~04:00 LST) and rescale FRP to the SNPP reference
overpass: FRP_norm = FRP * D(t_ref) / D(t_obs), with a bounded correction so
night-time noise is not amplified.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class DiurnalParams:
    peak_hour: float = 14.5
    sigma_hours: float = 3.3
    floor: float = 0.10
    day_reference_hour: float = 13.5    # SNPP / NOAA-20 / Aqua(nominal) day overpass
    night_reference_hour: float = 1.5   # SNPP night overpass
    clip: tuple = (0.35, 3.5)


PARAMS = DiurnalParams()


# --------------------------------------------------------------------------
# Time conversions
# --------------------------------------------------------------------------
def equation_of_time_minutes(doy):
    """Equation of time (minutes): apparent minus mean solar time."""
    b = 2.0 * np.pi * (np.asarray(doy, dtype=float) - 81.0) / 364.0
    return 9.87 * np.sin(2 * b) - 7.53 * np.cos(b) - 1.5 * np.sin(b)


def _ts_parts(ts):
    ts = pd.to_datetime(pd.Series(ts)) if not isinstance(ts, (pd.Series, pd.DatetimeIndex)) else pd.Series(ts)
    ts = ts.reset_index(drop=True)
    hours = (ts.dt.hour + ts.dt.minute / 60.0 + ts.dt.second / 3600.0).to_numpy(dtype=float)
    doy = ts.dt.dayofyear.to_numpy(dtype=float)
    return hours, doy


def utc_to_lst(ts, lon, apparent: bool = True) -> np.ndarray:
    """UTC timestamps + longitude (deg E) -> Local Solar Time in hours [0, 24)."""
    hours, doy = _ts_parts(ts)
    lst = hours + np.asarray(lon, dtype=float) / 15.0
    if apparent:
        lst = lst + equation_of_time_minutes(doy) / 60.0
    return np.mod(lst, 24.0)


def lst_to_utc_hours(lst, lon, doy, apparent: bool = True) -> np.ndarray:
    """Inverse of :func:`utc_to_lst`; result may fall outside [0, 24) (date roll-over)."""
    out = np.asarray(lst, dtype=float) - np.asarray(lon, dtype=float) / 15.0
    if apparent:
        out = out - equation_of_time_minutes(doy) / 60.0
    return out


def firms_to_timestamp(acq_date, acq_time) -> pd.Series:
    """FIRMS ``acq_date`` ('YYYY-MM-DD') + ``acq_time`` (HHMM int/str) -> UTC datetime."""
    t = pd.to_numeric(pd.Series(acq_time), errors="coerce").fillna(0).astype(int)
    hh = (t // 100).clip(0, 23)
    mm = (t % 100).clip(0, 59)
    d = pd.to_datetime(pd.Series(acq_date))
    return d + pd.to_timedelta(hh, unit="h") + pd.to_timedelta(mm, unit="m")


# --------------------------------------------------------------------------
# Diurnal cycle
# --------------------------------------------------------------------------
def circular_hour_diff(a, b):
    d = np.abs(np.asarray(a, dtype=float) - np.asarray(b, dtype=float)) % 24.0
    return np.minimum(d, 24.0 - d)


def diurnal_factor(lst, p: DiurnalParams = PARAMS):
    """Relative fire intensity in (floor, 1]; equals 1 at the peak hour."""
    d = circular_hour_diff(lst, p.peak_hour)
    return p.floor + (1.0 - p.floor) * np.exp(-0.5 * (d / p.sigma_hours) ** 2)


def reference_lst(lst, p: DiurnalParams = PARAMS):
    """Nearest SNPP overpass (13:30 day / 01:30 night) for each observation."""
    lst = np.asarray(lst, dtype=float)
    day = (lst >= 6.0) & (lst < 18.0)
    return np.where(day, p.day_reference_hour, p.night_reference_hour)


def signed_hour_delta(target, obs):
    """Signed circular difference target - obs in (-12, 12] hours."""
    d = (np.asarray(target, dtype=float) - np.asarray(obs, dtype=float) + 12.0) % 24.0 - 12.0
    return d


def diurnal_scale(lst_obs, lst_ref=None, p: DiurnalParams = PARAMS):
    """Bounded multiplicative factor D(ref) / D(obs)."""
    if lst_ref is None:
        lst_ref = reference_lst(lst_obs, p)
    f = diurnal_factor(lst_ref, p) / diurnal_factor(lst_obs, p)
    return np.clip(f, *p.clip)


def normalize_frp(frp, lst_obs, lst_ref=None, p: DiurnalParams = PARAMS):
    """FRP expressed at the reference overpass time."""
    return np.asarray(frp, dtype=float) * diurnal_scale(lst_obs, lst_ref, p)


# --------------------------------------------------------------------------
# Orbit schedule (nominal) incl. Terra/Aqua drift
# --------------------------------------------------------------------------
def nominal_day_lst(satellite: str, year_frac) -> np.ndarray:
    """Approximate nominal daytime equator-crossing LST of each platform.

    Terra and Aqua exhausted station-keeping fuel: Terra drifts earlier since
    ~2021, Aqua drifts later since ~2022.  Rates are approximate and used both
    by the simulator and by diagnostics.
    """
    y = np.asarray(year_frac, dtype=float)
    if satellite == "Terra":
        return 10.5 - 0.20 * np.clip(y - 2021.0, 0.0, None)
    if satellite == "Aqua":
        return 13.5 + 0.45 * np.clip(y - 2022.0, 0.0, None)
    return np.full_like(y, 13.5)  # SNPP / NOAA-20 hold ~13:30


def overpass_lst(satellite: str, year_frac, night: bool = False) -> np.ndarray:
    base = nominal_day_lst(satellite, year_frac)
    return np.mod(base + 12.0, 24.0) if night else base
