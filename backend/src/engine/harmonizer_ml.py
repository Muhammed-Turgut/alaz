"""Biome-stratified quantile harmonizer (HistGradientBoosting) with uncertainty.

What it learns
--------------
For every MODIS detection, the SNPP-VIIRS-scale (375 m) view of the *same* fire
at the nearest SNPP overpass:

  * ``n``   - equivalent VIIRS-scale hotspot count inside the MODIS footprint
  * ``frp`` - equivalent VIIRS-scale FRP (MW)

Targets come from *matched footprints* (``geometry_h3.match_footprints``):
each VIIRS pixel's unit mass is shared among overlapping MODIS footprints in
proportion to geometric intersection.  The model never sees a formula that
defines the target - it learns it from sensor pairs, so a good score is
evidence of harmonization rather than of memorising a rule.

Per biome (Forest / Cropland-Stubble / Savanna-Grassland) and target we fit
P10 / P50 / P90 quantile regressors plus a Poisson mean model.  The P10-P90
band is then *conformalised* (CQR, Romano et al. 2019) on a disjoint
calibration period to obtain a 90% prediction interval.

Completeness calibration
------------------------
MODIS cannot see fires below its detection threshold, so summed per-detection
predictions undercount the VIIRS-scale total.  A log-linear calibration
``log C = a[biome, day/night] + b[biome] * log(D_ref / D_obs)`` (D = diurnal
factor) maps the sum to the SNPP total and, through the diurnal term,
extrapolates the correction to drifted Terra/Aqua overpass times.
"""
from __future__ import annotations

import logging
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import LinearRegression

from src.data.regions import BIOMES
from src.engine import geometry_h3 as geo
from src.engine.diurnal_normalizer import diurnal_factor, signed_hour_delta
from src.engine.sensors import n_operating

log = logging.getLogger("aether.harmonizer")

MODEL_PATH = Path(__file__).resolve().parents[2] / "models" / "harmonizer_quantile.joblib"
MODEL_VERSION = "aether-fire-hgb-q-v2"

QUANTS = (0.10, 0.50, 0.90)
Z90 = 1.6449

FEATURES = [
    "log_frp", "log_frp_norm", "bright_t21", "bright_delta", "confidence",
    "area_km2", "scan", "track", "bowtie_ratio",
    "lst_sin", "lst_cos", "d_lst", "diurnal_ratio", "night",
    "log_cell_n", "log_cell_frp",
    "doy_sin", "doy_cos", "abs_lat",
]
FEATURE_GROUPS = {
    "Bow-tie footprint": ["area_km2", "scan", "track", "bowtie_ratio"],
    "Diurnal / overpass time": ["lst_sin", "lst_cos", "d_lst", "diurnal_ratio", "night"],
    "Fire intensity": ["log_frp", "log_frp_norm", "bright_t21", "bright_delta", "confidence"],
    "Cluster context": ["log_cell_n", "log_cell_frp"],
    "Season / latitude": ["doy_sin", "doy_cos", "abs_lat"],
}


# --------------------------------------------------------------------------
# features
# --------------------------------------------------------------------------
def add_cell_context(modis: pd.DataFrame) -> pd.DataFrame:
    """Cluster context: detections / FRP of the same satellite pass within the same H3-7 cell."""
    key = ["h3_7", "satellite", "daynight", "ldate"]
    g = modis.groupby(key, observed=True, sort=False)["frp"]
    modis = modis.copy()
    modis["log_cell_n"] = np.log1p(g.transform("size").to_numpy())
    modis["log_cell_frp"] = np.log1p(g.transform("sum").to_numpy())
    return modis


def feature_frame(df: pd.DataFrame) -> pd.DataFrame:
    if "log_cell_n" not in df.columns:
        df = add_cell_context(df)
    lst = df["lst"].to_numpy()
    two_pi = 2.0 * np.pi
    return pd.DataFrame({
        "log_frp": np.log1p(df["frp"].to_numpy(dtype=float)),
        "log_frp_norm": np.log1p(df["frp_norm"].to_numpy(dtype=float)),
        "bright_t21": df["bright_t21"].to_numpy(dtype=float),
        "bright_delta": (df["bright_t21"] - df["bright_t31"]).to_numpy(dtype=float),
        "confidence": pd.to_numeric(df["confidence"], errors="coerce").fillna(65).to_numpy(dtype=float),
        "area_km2": df["area_km2"].to_numpy(dtype=float),
        "scan": df["scan"].to_numpy(dtype=float),
        "track": df["track"].to_numpy(dtype=float),
        "bowtie_ratio": (df["scan"] / df["track"].clip(lower=1e-3)).to_numpy(dtype=float),
        "lst_sin": np.sin(two_pi * lst / 24.0),
        "lst_cos": np.cos(two_pi * lst / 24.0),
        "d_lst": signed_hour_delta(df["lst_ref"].to_numpy(), lst),
        "diurnal_ratio": df["diurnal_ratio"].to_numpy(dtype=float),
        "night": (df["daynight"].astype(str) == "N").to_numpy(dtype=float),
        "log_cell_n": df["log_cell_n"].to_numpy(dtype=float),
        "log_cell_frp": df["log_cell_frp"].to_numpy(dtype=float),
        "doy_sin": np.sin(two_pi * df["doy"].to_numpy(dtype=float) / 366.0),
        "doy_cos": np.cos(two_pi * df["doy"].to_numpy(dtype=float) / 366.0),
        "abs_lat": np.abs(df["latitude"].to_numpy(dtype=float)),
    })[FEATURES]


# --------------------------------------------------------------------------
# matched-footprint training targets
# --------------------------------------------------------------------------
def build_matchups(modis: pd.DataFrame, viirs_ref: pd.DataFrame, max_dt_hours: float = 4.5) -> pd.DataFrame:
    """Attach VIIRS-scale targets (n_eq, frp_eq) to every MODIS detection."""
    modis = add_cell_context(modis.reset_index(drop=True))
    viirs_ref = viirs_ref.reset_index(drop=True)
    n_eq = np.zeros(len(modis))
    f_eq = np.zeros(len(modis))
    pairs = geo.match_footprints(modis, viirs_ref, max_dt_hours)
    if len(pairs):
        mp = pairs["m_pos"].to_numpy()
        w = pairs["w"].to_numpy()
        n_eq = np.bincount(mp, weights=w, minlength=len(modis))
        f_eq = np.bincount(mp, weights=w * viirs_ref["frp"].to_numpy()[pairs["v_pos"].to_numpy()],
                           minlength=len(modis))
    return modis.assign(n_eq=n_eq, frp_eq=f_eq)


# --------------------------------------------------------------------------
# model
# --------------------------------------------------------------------------
def _hgb(loss, quantile=None, seed=7):
    kw = dict(loss=loss, max_iter=220, learning_rate=0.07, max_leaf_nodes=24,
              min_samples_leaf=40, l2_regularization=1.0, early_stopping=False, random_state=seed)
    if quantile is not None:
        kw["quantile"] = quantile
    return HistGradientBoostingRegressor(**kw)


def pinball(y, q, tau):
    d = y - q
    return float(np.mean(np.maximum(tau * d, (tau - 1) * d)))


class HarmonizerModel:
    def __init__(self, seed: int = 7):
        self.seed = seed
        self.models: dict = {}
        self.conformal: dict = {}
        self.medians: dict = {}
        self.calibration: dict = {}
        self.metrics: dict = {}
        self.importance: dict = {}
        self.sigma_inflation: float = 1.0
        self.trained = False
        self.version = MODEL_VERSION

    # ---- fitting ---------------------------------------------------------
    def _fit_group(self, g, X, n, f):
        self.medians[g] = X.median()
        for tgt, y in (("n", n), ("frp", f)):
            ylog = np.log1p(y)
            for q in QUANTS:
                self.models[(g, tgt, f"q{int(q * 100)}")] = _hgb("quantile", q, self.seed).fit(X, ylog)
            self.models[(g, tgt, "mean")] = _hgb("poisson", None, self.seed).fit(X, y)

    def fit(self, train: pd.DataFrame, calib: pd.DataFrame, test: pd.DataFrame | None = None,
            max_rows_per_biome: int = 60000):
        rng = np.random.default_rng(self.seed)
        Xtr_all = feature_frame(train)
        groups = {g: np.where(train["biome"].to_numpy() == g)[0] for g in range(3)}
        groups["all"] = np.arange(len(train))
        for g, idx in groups.items():
            if len(idx) < 200:
                continue
            if len(idx) > max_rows_per_biome:
                idx = rng.choice(idx, max_rows_per_biome, replace=False)
            log.info("fitting group %s on %d matchups", g, len(idx))
            self._fit_group(g, Xtr_all.iloc[idx], train["n_eq"].to_numpy()[idx], train["frp_eq"].to_numpy()[idx])

        self._fit_conformal(calib)
        self.trained = True
        if test is not None and len(test):
            self.metrics = self.evaluate(test)
            self.importance = self.permutation_importance(test)
        return self

    def _group_for(self, g):
        return g if (g, "n", "q50") in self.models else "all"

    def _raw_group_predict(self, g, X):
        g = self._group_for(g)
        out = {}
        for tgt in ("n", "frp"):
            qs = np.column_stack([self.models[(g, tgt, f"q{int(q * 100)}")].predict(X) for q in QUANTS])
            out[tgt + "_q"] = np.sort(qs, axis=1)  # enforce quantile monotonicity
            out[tgt + "_mean"] = np.maximum(self.models[(g, tgt, "mean")].predict(X), 0.0)
        return out

    def _fit_conformal(self, calib: pd.DataFrame, alpha: float = 0.10):
        X = feature_frame(calib)
        for g in range(3):
            m = calib["biome"].to_numpy() == g
            if m.sum() < 50:
                continue
            raw = self._raw_group_predict(g, X[m])
            for tgt, col in (("n", "n_eq"), ("frp", "frp_eq")):
                y = np.log1p(calib[col].to_numpy()[m])
                q = raw[tgt + "_q"]
                score = np.maximum(q[:, 0] - y, y - q[:, 2])
                n = len(score)
                level = min(1.0, np.ceil((n + 1) * (1 - alpha)) / n)
                self.conformal[(g, tgt)] = float(np.quantile(score, level))

    # ---- inference -------------------------------------------------------
    def predict(self, df: pd.DataFrame) -> pd.DataFrame:
        """Per-detection harmonized values with P10/P50/P90 and a conformal 90% interval."""
        X = feature_frame(df)
        out = {k: np.zeros(len(df)) for k in (
            "n_p10", "n_p50", "n_p90", "n_lo90", "n_hi90", "n_mean",
            "frp_p10", "frp_p50", "frp_p90", "frp_lo90", "frp_hi90", "frp_mean")}
        biome = df["biome"].to_numpy()
        for g in range(3):
            idx = np.where(biome == g)[0]
            if len(idx) == 0:
                continue
            raw = self._raw_group_predict(g, X.iloc[idx])
            for tgt in ("n", "frp"):
                q = raw[tgt + "_q"]
                qh = self.conformal.get((g, tgt), 0.0)
                for j, name in enumerate(("p10", "p50", "p90")):
                    out[f"{tgt}_{name}"][idx] = np.expm1(q[:, j])
                out[f"{tgt}_lo90"][idx] = np.expm1(np.maximum(q[:, 0] - qh, 0.0))
                out[f"{tgt}_hi90"][idx] = np.expm1(q[:, 2] + qh)
                out[f"{tgt}_mean"][idx] = raw[tgt + "_mean"]
        res = pd.DataFrame(out, index=df.index)
        frp = df["frp"].to_numpy(dtype=float)
        res["frp_harm"] = res["frp_p50"]
        res["scale_factor"] = np.where(frp > 0, res["frp_p50"].to_numpy() / np.maximum(frp, 1e-9), np.nan)
        return res

    # ---- explanation ----------------------------------------------------
    def explain(self, df: pd.DataFrame, top_k: int = 5, target: str = "frp") -> list[dict]:
        """Why was each hotspot scaled?  Ablation of feature groups to biome-typical values.

        Effect is the multiplicative change in the P50 harmonized FRP caused by the group,
        relative to a typical detection of the same biome.
        """
        X = feature_frame(df)
        biome = df["biome"].to_numpy()
        results = [dict() for _ in range(len(df))]
        for g in range(3):
            idx = np.where(biome == g)[0]
            if len(idx) == 0:
                continue
            gg = self._group_for(g)
            model = self.models[(gg, target, "q50")]
            base = self.medians[gg]
            Xg = X.iloc[idx]
            full = model.predict(Xg)
            for name, cols in FEATURE_GROUPS.items():
                Xa = Xg.copy()
                for c in cols:
                    Xa[c] = base[c]
                delta = full - model.predict(Xa)
                for k, i in enumerate(idx):
                    results[i][name] = float(np.exp(delta[k]) - 1.0)
        out = []
        for r in results:
            items = sorted(r.items(), key=lambda kv: -abs(kv[1]))[:top_k]
            out.append({"contributions": [{"driver": k, "effect_pct": round(100 * v, 1)} for k, v in items]})
        return out

    # ---- evaluation -----------------------------------------------------
    def evaluate(self, test: pd.DataFrame) -> dict:
        pred = self.predict(test)
        metrics = {"n_test": int(len(test)), "per_target": {}, "per_biome": {}}
        base_X = test[["frp"]].assign(lf=np.log1p(test["frp"].to_numpy()))[["lf"]]
        for tgt, col in (("n", "n_eq"), ("frp", "frp_eq")):
            y = np.log1p(test[col].to_numpy())
            p50 = np.log1p(pred[f"{tgt}_p50"].to_numpy())
            p10, p90 = np.log1p(pred[f"{tgt}_p10"].to_numpy()), np.log1p(pred[f"{tgt}_p90"].to_numpy())
            lo, hi = np.log1p(pred[f"{tgt}_lo90"].to_numpy()), np.log1p(pred[f"{tgt}_hi90"].to_numpy())
            ols = LinearRegression().fit(base_X, y)  # naive "FRP-only linear" baseline, fit on test for a *generous* baseline
            naive = ols.predict(base_X)
            metrics["per_target"][tgt] = {
                "mae_log_model": float(np.mean(np.abs(y - p50))),
                "mae_log_naive_linear": float(np.mean(np.abs(y - naive))),
                "mae_improvement_pct": float(100 * (1 - np.mean(np.abs(y - p50)) / max(np.mean(np.abs(y - naive)), 1e-12))),
                "pinball_p10": pinball(y, p10, 0.10),
                "pinball_p50": pinball(y, p50, 0.50),
                "pinball_p90": pinball(y, p90, 0.90),
                "coverage_p10_p90": float(np.mean((y >= p10) & (y <= p90))),
                "coverage_90_conformal": float(np.mean((y >= lo) & (y <= hi))),
                "mean_width_90_log": float(np.mean(hi - lo)),
            }
        for g in range(3):
            m = test["biome"].to_numpy() == g
            if m.sum() < 30:
                continue
            y = np.log1p(test["n_eq"].to_numpy()[m])
            p50 = np.log1p(pred["n_p50"].to_numpy()[m])
            lo, hi = np.log1p(pred["n_lo90"].to_numpy()[m]), np.log1p(pred["n_hi90"].to_numpy()[m])
            metrics["per_biome"][BIOMES[g]] = {
                "n": int(m.sum()),
                "mae_log": float(np.mean(np.abs(y - p50))),
                "coverage_90": float(np.mean((y >= lo) & (y <= hi))),
            }
        return metrics

    def permutation_importance(self, test: pd.DataFrame, n_sample: int = 4000) -> dict:
        return {"frp": self._perm_imp(test, "frp", "frp_eq", n_sample),
                "n": self._perm_imp(test, "n", "n_eq", n_sample)}

    def _perm_imp(self, test: pd.DataFrame, tgt: str, col: str, n_sample: int) -> dict:
        rng = np.random.default_rng(self.seed + 1)
        idx = rng.choice(len(test), min(n_sample, len(test)), replace=False)
        sub = test.iloc[idx]
        X = feature_frame(sub)
        biome = sub["biome"].to_numpy()
        y = np.log1p(sub[col].to_numpy())
        base_err = np.zeros(len(sub))
        preds = {}
        for g in range(3):
            m = biome == g
            if m.sum() == 0:
                continue
            gg = self._group_for(g)
            preds[g] = (m, self.models[(gg, tgt, "q50")])
            base_err[m] = np.abs(y[m] - preds[g][1].predict(X[m]))
        imp = {}
        for f in FEATURES:
            Xp = X.copy()
            Xp[f] = rng.permutation(Xp[f].to_numpy())
            err = np.zeros(len(sub))
            for g, (m, mdl) in preds.items():
                err[m] = np.abs(y[m] - mdl.predict(Xp[m]))
            imp[f] = float(err.mean() - base_err.mean())
        tot = sum(max(v, 0) for v in imp.values()) or 1.0
        return {k: round(max(v, 0) / tot, 4) for k, v in sorted(imp.items(), key=lambda kv: -kv[1])}

    # ---- completeness calibration --------------------------------------
    def fit_calibration(self, modis_pred: pd.DataFrame, viirs_counts: pd.DataFrame):
        """Fit log C = a[biome, D/N] + b[biome] * x  (x = log D_ref/D_obs, per detection).

        Parameters are chosen by non-linear least squares so that, for every
        (biome, satellite, day/night) group g,  sum_i C(x_i) * E[n_i]  matches the SNPP
        total V_g.  The residual uses the *same per-detection rule* that is applied at
        inference, so there is no Jensen-gap bias.

        modis_pred: rows with biome, satellite, daynight, diurnal_ratio, n_mean.
        viirs_counts: DataFrame[biome, daynight, n] of SNPP detections in the same window.
        """
        from scipy.optimize import least_squares

        for g in range(3):
            groups = []
            for (sat, dn), sub in modis_pred[modis_pred["biome"] == g].groupby(["satellite", "daynight"]):
                V = float(viirs_counts[(viirs_counts["biome"] == g) & (viirs_counts["daynight"] == dn)]["n"].sum())
                if V <= 0 or sub["n_mean"].sum() <= 0 or len(sub) < 100:
                    continue
                groups.append((dn == "N", np.clip(sub["diurnal_ratio"].to_numpy(), -1.0, 2.5),
                               sub["n_mean"].to_numpy(), V))
            if not groups:
                self.calibration[g] = {"a_D": 0.0, "a_N": 0.0, "b": 0.0}
                continue
            has_n = any(gr[0] for gr in groups)
            has_d = any(not gr[0] for gr in groups)
            wts = np.sqrt(np.array([gr[3] for gr in groups]))
            wts = wts / wts.sum()

            def resid(theta):
                aD, aN, b = theta
                if not has_n:
                    aN = aD
                if not has_d:
                    aD = aN
                r = []
                for (night, x, n, V), w in zip(groups, wts):
                    r.append(w * (np.sum(n * np.exp((aN if night else aD) + b * x)) / V - 1.0))
                return np.array(r)

            init = np.array([0.8, 2.5, 1.0])
            sol = least_squares(resid, init, bounds=([-5, -5, -0.5], [7, 7, 3.0]))
            aD, aN, b = (float(v) for v in sol.x)
            if not has_n:
                aN = aD
            if not has_d:
                aD = aN
            self.calibration[g] = {"a_D": aD, "a_N": aN, "b": b}

    def calibration_factor(self, biome, daynight, diurnal_ratio) -> np.ndarray:
        biome = np.asarray(biome)
        night = np.asarray(daynight).astype(str) == "N"
        x = np.clip(np.asarray(diurnal_ratio, dtype=float), -1.0, 2.5)
        out = np.ones(len(biome))
        for g in range(3):
            c = self.calibration.get(g)
            if c is None:
                continue
            m = biome == g
            a = np.where(night[m], c["a_N"], c["a_D"])
            out[m] = np.exp(a + c["b"] * x[m])
        return out

    # ---- harmonized hotspot-equivalent weights --------------------------
    def harmonize(self, df: pd.DataFrame, pred: pd.DataFrame | None = None,
                  n_ops_modis=None, n_ops_viirs=None) -> pd.DataFrame:
        """Per-detection contribution to the *equivalent SNPP-VIIRS-scale hotspot count*.

        MODIS rows contribute C * E[n] / n_modis_platforms (averaging platforms keeps the
        series continuous when Aqua joins Terra); VIIRS rows are native and contribute
        1 / n_viirs_platforms.  Returns columns h_w (expected), h_sd (1-sigma), h_lo, h_hi.
        """
        is_modis = (df["instrument"].astype(str) == "MODIS").to_numpy()
        w = np.zeros(len(df))
        sd = np.zeros(len(df))
        lo = np.zeros(len(df))
        hi = np.zeros(len(df))
        nm = np.asarray(n_ops_modis if n_ops_modis is not None else n_operating(df["ts"], "MODIS"), dtype=float)
        nv = np.asarray(n_ops_viirs if n_ops_viirs is not None else n_operating(df["ts"], "VIIRS"), dtype=float)
        if nm.ndim == 0:
            nm = np.full(len(df), float(nm))
        if nv.ndim == 0:
            nv = np.full(len(df), float(nv))
        if (~is_modis).any():
            w[~is_modis] = 1.0 / nv[~is_modis]
            lo[~is_modis] = hi[~is_modis] = w[~is_modis]
        if is_modis.any():
            sub = df[is_modis]
            if pred is None:
                pred = self.predict(sub)
            else:
                pred = pred.loc[sub.index]
            c = self.calibration_factor(sub["biome"].to_numpy(), sub["daynight"].to_numpy(),
                                        sub["diurnal_ratio"].to_numpy())
            den = nm[is_modis]
            w[is_modis] = c * pred["n_mean"].to_numpy() / den
            lo[is_modis] = c * pred["n_lo90"].to_numpy() / den
            hi[is_modis] = c * pred["n_hi90"].to_numpy() / den
            sd[is_modis] = c * (pred["n_hi90"].to_numpy() - pred["n_lo90"].to_numpy()) / (2 * Z90) / den
        return pd.DataFrame({"h_w": w, "h_sd": sd, "h_lo": lo, "h_hi": hi}, index=df.index)

    # ---- persistence ------------------------------------------------------
    def save(self, path: Path = MODEL_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(self, path, compress=3)

    @staticmethod
    def load(path: Path = MODEL_PATH) -> "HarmonizerModel":
        m = joblib.load(path)
        if not isinstance(m, HarmonizerModel) or m.version != MODEL_VERSION:
            raise ValueError("stale or incompatible model file")
        return m
