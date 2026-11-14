"""Burning calendar: years x 365 day-of-year matrix and P10/P50/P90 climatology envelope."""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.engine.stats_diagnostics import zscore_anomalies

N_DOY = 365


def _doy_noleap(index: pd.DatetimeIndex) -> np.ndarray:
    doy = index.dayofyear.to_numpy().copy()
    leap = index.is_leap_year
    doy = np.where(leap & (doy >= 60), doy - 1, doy)
    return doy, ~((index.month == 2) & (index.day == 29))


def calendar_matrix(daily: pd.Series, years: range, last_date: pd.Timestamp | None = None):
    """(len(years) x 365) matrix; days after ``last_date`` (incomplete current year) are NaN."""
    mat = np.full((len(years), N_DOY), np.nan)
    doy, keep = _doy_noleap(daily.index)
    yr = daily.index.year.to_numpy()
    year_pos = {y: i for i, y in enumerate(years)}
    for y, i in year_pos.items():
        m = (yr == y) & keep
        if not m.any():
            continue
        mat[i, doy[m] - 1] = daily.to_numpy()[m]
    if last_date is not None:
        y = last_date.year
        if y in year_pos:
            d, _ = _doy_noleap(pd.DatetimeIndex([last_date]))
            mat[year_pos[y], int(d[0]):] = np.nan
    return mat


def _smooth_circular(mat: np.ndarray, window: int) -> np.ndarray:
    if window <= 1:
        return mat
    pad = window // 2
    out = np.full_like(mat, np.nan)
    for i in range(mat.shape[0]):
        row = mat[i]
        ext = np.concatenate([row[-pad:], row, row[:pad]])
        s = pd.Series(ext).rolling(window, center=True, min_periods=max(1, window // 2)).mean().to_numpy()
        out[i] = s[pad:-pad]
        out[i][np.isnan(row)] = np.nan
    return out


def climatology_envelope(matrix: np.ndarray, years: list, exclude_year: int | None = None, smooth: int = 7) -> dict:
    sm = _smooth_circular(matrix, smooth)
    use = [i for i, y in enumerate(years) if y != exclude_year]
    base = sm[use]
    with np.errstate(all="ignore"):
        return {
            "p10": np.nanpercentile(base, 10, axis=0),
            "p50": np.nanpercentile(base, 50, axis=0),
            "p90": np.nanpercentile(base, 90, axis=0),
            "mean": np.nanmean(base, axis=0),
        }


def build_calendar(daily: pd.Series, target_year: int = 2026, start_year: int = 2001, smooth: int = 7) -> dict:
    """Full calendar payload for the UI.

    ``daily`` is any daily count series (raw or harmonized).  The envelope for the
    target year excludes that year so anomalies are judged against history.
    """
    years = list(range(start_year, target_year + 1))
    last = daily.index.max()
    mat = calendar_matrix(daily, range(start_year, target_year + 1), last_date=last)
    env = climatology_envelope(mat, years, exclude_year=target_year, smooth=smooth)
    sm = _smooth_circular(mat, smooth)
    ti = years.index(target_year)

    base_years = [y for y in years if y != target_year]
    an = zscore_anomalies(daily[daily.index.year <= target_year], baseline_years=base_years, window=smooth)
    an_t = an[an.index.year == target_year]
    flagged = an_t[an_t["anomaly"]]

    def r(a):
        return [None if not np.isfinite(v) else round(float(v), 2) for v in a]

    peak_doy = [int(np.nanargmax(np.nan_to_num(sm[i], nan=-1))) + 1 for i in range(len(years))]
    totals = [float(np.nansum(mat[i])) for i in range(len(years))]
    return {
        "years": years,
        "doy": list(range(1, N_DOY + 1)),
        "matrix": [r(row) for row in sm],
        "envelope": {k: r(v) for k, v in env.items()},
        "target_year": target_year,
        "target_series": r(sm[ti]),
        "annual_totals": [round(t, 1) for t in totals],
        "peak_doy": peak_doy,
        "anomalies": {
            "n_days": int(len(flagged)),
            "n_positive": int((flagged["z"] > 0).sum()),
            "n_negative": int((flagged["z"] < 0).sum()),
            "max_z": None if flagged.empty else round(float(flagged["z"].abs().max()), 2),
            "episodes": _episodes(an_t),
        },
    }


def _episodes(an: pd.DataFrame, min_len: int = 3, max_n: int = 8) -> list:
    """Contiguous runs of flagged days (>= min_len) with direction and peak z."""
    eps, start, prev = [], None, None
    flag = an["anomaly"].to_numpy()
    idx = an.index
    for i, f in enumerate(flag):
        if f and start is None:
            start = i
        if (not f or i == len(flag) - 1) and start is not None:
            end = i if f and i == len(flag) - 1 else i - 1
            if end - start + 1 >= min_len:
                z = an["z"].to_numpy()[start:end + 1]
                eps.append({"start": str(idx[start].date()), "end": str(idx[end].date()),
                            "days": int(end - start + 1), "peak_z": round(float(z[np.argmax(np.abs(z))]), 2)})
            start = None
    eps.sort(key=lambda e: -abs(e["peak_z"]))
    return eps[:max_n]
