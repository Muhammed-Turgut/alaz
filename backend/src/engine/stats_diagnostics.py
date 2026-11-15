"""Statistical diagnostics: structural-break tests, error metrics, anomaly detection.

* ``pettitt_test``  - non-parametric change-point test (Pettitt 1979).  U_t is
  computed from ranks (Mann-Whitney form) in O(n log n); the p-value uses the
  standard approximation p ~= 2 exp(-6 K^2 / (n^3 + n^2)).
* ``chow_test``     - Chow (1960) F-test for a structural break in a linear
  trend at a *known* date (here: the 2012 VIIRS introduction), on log scale so
  a multiplicative step is a level shift.
* ``harmonization_metrics`` - RMSE, bias and Bias-Reduction % of a raw vs a
  harmonized series against a reference.
* ``zscore_anomalies``      - robust (median/MAD) z-scores against a day-of-year
  climatology.

Caveat: both tests assume independent errors.  Monthly fire series are
autocorrelated, which makes p-values anti-conservative; they are best read as
effect-size evidence together with ``step_change_pct``.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats


def pettitt_test(x) -> dict:
    x = np.asarray(x, dtype=float)
    x = x[~np.isnan(x)] if np.isnan(x).any() else x
    n = len(x)
    if n < 8:
        return {"n": n, "K": 0.0, "change_index": None, "p_value": 1.0, "significant": False}
    r = stats.rankdata(x)
    t = np.arange(1, n)
    u = 2.0 * np.cumsum(r)[:-1] - t * (n + 1)
    k = float(np.max(np.abs(u)))
    cp = int(np.argmax(np.abs(u)))  # change after index cp (0-based) -> new regime starts at cp+1
    p = float(min(1.0, 2.0 * np.exp(-6.0 * k**2 / (n**3 + n**2))))
    return {"n": n, "K": k, "change_index": cp + 1, "p_value": p, "significant": p < 0.05,
            "mean_before": float(np.mean(x[: cp + 1])), "mean_after": float(np.mean(x[cp + 1:]))}


def chow_test(y, break_index: int, log: bool = True) -> dict:
    y = np.asarray(y, dtype=float)
    if log:
        y = np.log1p(y)
    n = len(y)
    k = 2
    if break_index < 4 or n - break_index < 4:
        return {"F": float("nan"), "p_value": 1.0, "significant": False, "df": (k, n - 2 * k)}
    t = np.arange(n, dtype=float)

    def rss(tt, yy):
        a = np.column_stack([np.ones(len(tt)), tt])
        beta, *_ = np.linalg.lstsq(a, yy, rcond=None)
        res = yy - a @ beta
        return float(res @ res)

    rss_p = rss(t, y)
    rss_1 = rss(t[:break_index], y[:break_index])
    rss_2 = rss(t[break_index:], y[break_index:])
    rss_u = rss_1 + rss_2
    f = ((rss_p - rss_u) / k) / (rss_u / (n - 2 * k))
    p = float(stats.f.sf(f, k, n - 2 * k))
    return {"F": float(f), "p_value": p, "significant": p < 0.05, "df": (k, n - 2 * k)}


def step_change_pct(series: pd.Series, break_date: str = "2012-01-20", window_years: int = 5) -> dict:
    """Mean level in the ``window_years`` after vs before the break (annual-sum basis)."""
    bd = pd.Timestamp(break_date)
    pre = series[(series.index >= bd - pd.DateOffset(years=window_years)) & (series.index < bd)]
    post = series[(series.index >= bd) & (series.index < bd + pd.DateOffset(years=window_years))]
    if len(pre) == 0 or len(post) == 0 or pre.mean() <= 0:
        return {"pre_mean": float(pre.mean()) if len(pre) else None,
                "post_mean": float(post.mean()) if len(post) else None, "step_pct": None}
    return {"pre_mean": float(pre.mean()), "post_mean": float(post.mean()),
            "step_pct": float(100.0 * (post.mean() / pre.mean() - 1.0))}


def deseasonalize(monthly: pd.Series) -> pd.Series:
    """Divide by the mean annual cycle (month-of-year means of the series itself)."""
    m = monthly.groupby(monthly.index.month).transform("mean")
    return monthly / m.replace(0, np.nan)


def structural_break_report(monthly: pd.Series, break_date: str = "2012-01-20") -> dict:
    """Pettitt + Chow on the deseasonalised monthly series and on annual totals."""
    monthly = monthly.astype(float)
    bd = pd.Timestamp(break_date)
    ds = deseasonalize(monthly).fillna(0.0)
    bi_m = int((ds.index < bd).sum())
    annual = monthly.groupby(monthly.index.year).sum()
    annual = annual[annual.index <= monthly.index[-1].year - 1]  # drop the incomplete last year
    bi_a = int((annual.index < bd.year + (1 if bd.month > 1 or bd.day > 1 else 0)).sum())
    pm = pettitt_test(ds.to_numpy())
    pa = pettitt_test(annual.to_numpy())
    lo, hi = max(0, bi_m - 72), min(len(ds), bi_m + 72)
    pl_ = pettitt_test(ds.to_numpy()[lo:hi])  # +/- 6 years around the transition only
    pl_["change_date"] = (str(ds.index[lo + pl_["change_index"]].date())
                          if pl_["change_index"] is not None and lo + pl_["change_index"] < len(ds) else None)
    cm = chow_test(monthly.to_numpy(), bi_m)
    ca = chow_test(annual.to_numpy(), bi_a)
    cp_date = None
    if pm["change_index"] is not None:
        cp_date = str(ds.index[min(pm["change_index"], len(ds) - 1)].date())
    return {
        "break_date": break_date,
        "pettitt_monthly": {**pm, "change_date": cp_date},
        "pettitt_local": pl_,
        "pettitt_annual": {**pa, "change_year": int(annual.index[pa["change_index"]]) if pa["change_index"] is not None
                            and pa["change_index"] < len(annual) else None},
        "chow_monthly": cm,
        "chow_annual": ca,
        "step": step_change_pct(annual.rename_axis("year").pipe(lambda s: pd.Series(
            s.to_numpy(), index=pd.to_datetime(s.index.astype(str) + "-07-01")))),
    }


def harmonization_metrics(reference, raw, harmonized) -> dict:
    ref = np.asarray(reference, dtype=float)
    raw = np.asarray(raw, dtype=float)
    har = np.asarray(harmonized, dtype=float)
    ok = ~(np.isnan(ref) | np.isnan(raw) | np.isnan(har))
    ref, raw, har = ref[ok], raw[ok], har[ok]
    if len(ref) == 0:
        return {}

    def rmse(a):
        return float(np.sqrt(np.mean((a - ref) ** 2)))

    def bias(a):
        return float(np.mean(a - ref))

    def rel_bias(a):
        return float(100.0 * (a.sum() - ref.sum()) / max(ref.sum(), 1e-12))

    b_raw, b_har = bias(raw), bias(har)
    corr = lambda a: float(np.corrcoef(a, ref)[0, 1]) if np.std(a) > 0 and np.std(ref) > 0 else float("nan")
    return {
        "n": int(len(ref)),
        "rmse_raw": rmse(raw), "rmse_harmonized": rmse(har),
        "rmse_reduction_pct": float(100.0 * (1 - rmse(har) / max(rmse(raw), 1e-12))),
        "bias_raw": b_raw, "bias_harmonized": b_har,
        "bias_reduction_pct": float(100.0 * (1 - abs(b_har) / max(abs(b_raw), 1e-12))),
        "relative_bias_raw_pct": rel_bias(raw), "relative_bias_harmonized_pct": rel_bias(har),
        "correlation_raw": corr(raw), "correlation_harmonized": corr(har),
    }


def zscore_anomalies(daily: pd.Series, baseline_years=None, window: int = 7, z_thresh: float = 2.0) -> pd.DataFrame:
    """Robust z-score of each day versus a smoothed day-of-year climatology."""
    s = daily.astype(float)
    sm = s.rolling(window, center=True, min_periods=1).mean()
    lv = np.log1p(sm.to_numpy())
    doy = s.index.dayofyear.to_numpy()
    base_mask = np.ones(len(s), dtype=bool)
    if baseline_years is not None:
        base_mask = np.isin(s.index.year, list(baseline_years))
    # Fire counts are heteroscedastic (roughly log-normal), so z is computed on log1p.
    # The scale is floored at 0.35 (~40% typical year-to-year spread) because per-DOY MAD
    # from ~25 values collapses in quiet seasons and would produce absurd z-scores.
    frame = pd.DataFrame({"v": lv, "doy": doy})[base_mask]
    med = frame.groupby("doy")["v"].median()
    mad = frame.groupby("doy")["v"].agg(lambda x: np.median(np.abs(x - np.median(x))))
    scale = (1.4826 * mad).clip(lower=0.35)
    med_a = med.reindex(doy).to_numpy()
    sc_a = scale.reindex(doy).to_numpy()
    z = (lv - med_a) / sc_a
    med_a = np.expm1(med_a)
    return pd.DataFrame({"value": sm.to_numpy(), "climatology": med_a, "z": z,
                         "anomaly": np.abs(z) >= z_thresh}, index=s.index)


def break_verdict(reports: dict, break_date: str = "2012-01-20") -> dict:
    """Machine-generated reading of {name: structural_break_report} (raw / harmonized / truth)."""
    bd = pd.Timestamp(break_date)

    def months_from_break(d):
        if not d:
            return None
        return abs((pd.Timestamp(d).year - bd.year) * 12 + (pd.Timestamp(d).month - bd.month))

    out = {"notes": []}
    raw = reports.get("raw_filtered") or reports.get("raw_all")
    har = reports.get("harmonized")
    tru = reports.get("truth")
    if raw:
        m = months_from_break(raw["pettitt_monthly"].get("change_date"))
        out["raw_break_at_transition"] = bool(raw["pettitt_monthly"]["significant"] and m is not None and m <= 12)
        sp = raw["step"].get("step_pct")
        step_txt = f"{sp:+.0f}%" if sp is not None and np.isfinite(sp) else "n/a"
        out["notes"].append(
            f"Ham seri: Pettitt değişim noktası {raw['pettitt_monthly'].get('change_date')} "
            f"(aylık p={raw['pettitt_monthly']['p_value']:.1e}, yıllık p={raw['pettitt_annual']['p_value']:.1e}), "
            f"adım {step_txt} → sensör geçişine bağlı yapay kırılma.")
    if har:
        pa, ca = har["pettitt_annual"]["p_value"], har["chow_annual"]["p_value"]
        pm = har["pettitt_monthly"]
        m = months_from_break(pm.get("change_date"))
        local = har.get("pettitt_local", {})
        out["harmonized_step_eliminated"] = bool(pa > 0.05 and ca > 0.05 and (not pm["significant"] or (m is not None and m > 18)))
        hsp = har["step"].get("step_pct")
        hstep_txt = f"{hsp:+.1f}%" if hsp is not None and np.isfinite(hsp) else "yok"
        out["notes"].append(
            f"Harmonize seri: yıllık Pettitt p={pa:.2f}, Chow p={ca:.2f}, adım {hstep_txt}.")
        if local:
            out["notes"].append(
                f"Yerel Pettitt (2012 ±6 yıl) p={local['p_value']:.2f} "
                f"({'kırılma yok' if not local['significant'] else 'kırılma: ' + str(local.get('change_date'))}).")
        if pm["significant"]:
            if tru and tru["pettitt_monthly"]["significant"] and months_from_break(tru["pettitt_monthly"].get("change_date")) is not None \
                    and abs((pd.Timestamp(tru["pettitt_monthly"]["change_date"]) - pd.Timestamp(pm["change_date"])).days) < 400:
                out["notes"].append(
                    f"Aylık Pettitt hâlâ {pm.get('change_date')} işaretliyor (p={pm['p_value']:.1e}); "
                    f"VIIRS karşılaştırma referansı aynı kırılmayı gösteriyor "
                    f"(p={tru['pettitt_monthly']['p_value']:.1e}) — senaryoda rejim değişimi olabilir, "
                    f"sensör artefaktı değil (p kesin karar değildir).")
            else:
                out["notes"].append(
                    f"Aylık Pettitt {pm.get('change_date')} işaretliyor (p={pm['p_value']:.1e}); "
                    f"kalan sapmayı incele — p<0.05 kesin kırılma değildir.")
    out["notes"].append(
        "Uyarı: Pettitt/Chow bağımsız örnek varsayar; otokorelasyonlu serilerde p anti-konservatif olabilir. "
        "Çoklu testlerde yanlış pozitif riski artar. Adım % ve etki büyüklüğüyle birlikte okuyun."
    )
    out["pvalue_caveat"] = (
        "p<0.05 is not a definitive structural break under autocorrelation."
    )
    return out
