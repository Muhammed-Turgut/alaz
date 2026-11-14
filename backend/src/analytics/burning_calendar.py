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


def month_year_matrix(daily: pd.Series, start_year: int | None = None, end_year: int | None = None) -> dict:
    """Ay × yıl ısı haritası: hangi ay/yıl yoğun (challenge'ın istediği görünüm)."""
    s = daily.astype(float).dropna()
    if s.empty:
        return {"years": [], "months": list(range(1, 13)), "matrix": [], "totals_by_month": [0.0] * 12}
    years = list(range(
        start_year or int(s.index.year.min()),
        (end_year or int(s.index.year.max())) + 1,
    ))
    mat = np.full((len(years), 12), np.nan)
    g = s.groupby([s.index.year, s.index.month]).sum()
    for (y, m), v in g.items():
        if y in years:
            mat[years.index(y), int(m) - 1] = float(v)
    with np.errstate(all="ignore"):
        totals = [float(np.nansum(mat[:, i])) for i in range(12)]
    return {
        "years": years,
        "months": list(range(1, 13)),
        "matrix": [[None if not np.isfinite(v) else round(float(v), 2) for v in row] for row in mat],
        "totals_by_month": [round(t, 1) for t in totals],
    }


def current_month_vs_median(daily: pd.Series, year: int | None = None, month: int | None = None) -> dict:
    """Seçili ayın toplamını aynı ayın tarihsel medyanı ile karşılaştır."""
    s = daily.astype(float).dropna()
    if s.empty:
        return {"available": False}
    last = s.index.max()
    year = int(year or last.year)
    month = int(month or last.month)
    monthly = s.groupby([s.index.year, s.index.month]).sum()
    hist = [float(v) for (y, m), v in monthly.items() if int(m) == month and int(y) != year]
    cur = float(monthly.get((year, month), 0.0))
    if not hist:
        return {
            "available": True, "year": year, "month": month,
            "month_total": round(cur, 2), "historical_median": None,
            "ratio": None, "percentile": None, "n_years": 0,
        }
    med = float(np.median(hist))
    pct = float(100.0 * np.mean(np.asarray(hist) <= cur))
    return {
        "available": True,
        "year": year,
        "month": month,
        "month_total": round(cur, 2),
        "historical_median": round(med, 2),
        "ratio": None if med <= 0 else round(cur / med, 3),
        "percentile": round(pct, 1),
        "n_years": len(hist),
        "historical_p10": round(float(np.percentile(hist, 10)), 2),
        "historical_p90": round(float(np.percentile(hist, 90)), 2),
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


_MONTH_BOUNDS = (1, 32, 60, 91, 121, 152, 182, 213, 244, 274, 305, 335, 366)
_MONTH_TR = ("Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara")


def _finite_slice(arr: list, a: int, b: int) -> list[float]:
    out: list[float] = []
    for v in (arr or [])[a:b]:
        if v is None:
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if np.isfinite(f):
            out.append(f)
    return out


def envelope_ai_summary(cal: dict) -> dict:
    """AI dayanağı: 365 günlük diziyi aylık P10/P50/P90 + hedef yılı özetine indirger."""
    env = cal.get("envelope") or {}
    p10 = env.get("p10") or []
    p50 = env.get("p50") or []
    p90 = env.get("p90") or []
    target = cal.get("target_series") or []
    ty = cal.get("target_year")

    aylik = []
    for i in range(12):
        a, b = _MONTH_BOUNDS[i] - 1, _MONTH_BOUNDS[i + 1] - 1
        t_sl = _finite_slice(target, a, b)
        s10, s50, s90 = _finite_slice(p10, a, b), _finite_slice(p50, a, b), _finite_slice(p90, a, b)

        def _mean(xs: list[float]) -> float | None:
            return round(sum(xs) / len(xs), 2) if xs else None

        aylik.append({
            "ay": _MONTH_TR[i],
            "zarf_p10": _mean(s10),
            "zarf_p50": _mean(s50),
            "zarf_p90": _mean(s90),
            "hedef_ort": _mean(t_sl),
            "hedef_max": round(max(t_sl), 2) if t_sl else None,
        })

    above_p90 = below_p10 = 0
    n = min(len(target), len(p10), len(p90), N_DOY)
    for i in range(n):
        t, lo, hi = target[i], p10[i], p90[i]
        if t is None or lo is None or hi is None:
            continue
        try:
            tv, lov, hiv = float(t), float(lo), float(hi)
        except (TypeError, ValueError):
            continue
        if not (np.isfinite(tv) and np.isfinite(lov) and np.isfinite(hiv)):
            continue
        if tv > hiv:
            above_p90 += 1
        if tv < lov:
            below_p10 += 1

    peak_doy = None
    peak_val = None
    t_all = [(i + 1, float(v)) for i, v in enumerate(target) if v is not None and np.isfinite(float(v))]
    if t_all:
        peak_doy, peak_val = max(t_all, key=lambda x: x[1])
        peak_val = round(peak_val, 2)

    # Mevsimsel tepe: tarihsel P50'nin en yüksek ayı
    p50_months = [(m["ay"], m["zarf_p50"]) for m in aylik if m["zarf_p50"] is not None]
    tipik_tepe_ay = max(p50_months, key=lambda x: x[1])[0] if p50_months else None

    return {
        "hedef_yil": ty,
        "tipik_tepe_ay_p50": tipik_tepe_ay,
        "hedef_tepe_gun": peak_doy,
        "hedef_tepe_deger": peak_val,
        "gun_p90_ustu": above_p90,
        "gun_p10_alti": below_p10,
        "aylik_zarf": aylik,
        "aciklama": (
            "zarf_p10/p50/p90 = tarihsel iklimsel zarf (hedef yıl hariç); "
            "hedef_* = seçili yılın günlük serisi. Birim: tespit/gün."
        ),
    }
