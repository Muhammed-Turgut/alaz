"""AetherState: loads archives, trains/loads the harmonizer, answers every query the API needs."""
from __future__ import annotations

import logging
import time
from functools import lru_cache

import numpy as np
import pandas as pd

from src.analytics.burning_calendar import build_calendar
from src.data import benchmark_archives as arch
from src.data.firms_client import FirmsClient
from src.data.regions import BIOMES, REGIONS
from src.engine import geometry_h3 as geo
from src.engine import pipeline as pl
from src.engine import stats_diagnostics as sd
from src.engine.diurnal_normalizer import (
    PARAMS as DIURNAL,
    diurnal_factor,
    firms_to_timestamp,
    nominal_day_lst,
)
from src.engine.harmonizer_ml import MODEL_PATH, HarmonizerModel, add_cell_context
from src.engine.prepare import prepare
from src.engine.sensors import VIIRS_START
from src.engine.static_filter import KNOWN_SOURCES, tag_static

log = logging.getLogger("aether.service")
Z90 = 1.6449
VSTART = pd.Timestamp(str(VIIRS_START))
LAST_DAY = pd.Timestamp(str(arch.ARCHIVE_END))
F32 = ["latitude", "longitude", "frp", "scan", "track", "bright_t21", "bright_t31", "area_km2", "lst",
       "lst_ref", "diurnal_ratio", "frp_norm", "h_w", "h_sd", "h_lo", "h_hi", "frp_harm", "frp_harm_lo",
       "frp_harm_hi", "log_cell_n", "log_cell_frp"]
DROP = ["h3_8", "d_factor", "truth_event", "ldate", "month"]


@lru_cache(maxsize=300_000)
def _boundary(cell: int):
    return [[round(float(a), 4), round(float(b), 4)] for a, b in geo.cell_boundary(cell)]


@lru_cache(maxsize=300_000)
def _hexstr(cell: int):
    return geo.h3_str(cell)


def clean(o):
    """Recursively make an object JSON-safe (numpy -> python, NaN/inf -> None)."""
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [clean(v) for v in o]
    if isinstance(o, (np.ndarray, pd.Series, pd.Index)):
        return clean(o.tolist())
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        v = float(o)
        return v if np.isfinite(v) else None
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (pd.Timestamp, np.datetime64)):
        return str(pd.Timestamp(o))
    if o is pd.NaT:
        return None
    return o


def _slim(df: pd.DataFrame) -> pd.DataFrame:
    df = df.drop(columns=[c for c in DROP if c in df.columns])
    for c in F32:
        if c in df.columns:
            df[c] = df[c].astype(np.float32)
    for c in ("satellite", "instrument", "daynight", "static_reason"):
        if c in df.columns:
            df[c] = df[c].astype("category")
    df["biome"] = df["biome"].astype(np.int8)
    df["year"] = df["year"].astype(np.int16)
    return df


class AetherState:
    def __init__(self, sim_cfg: arch.SimConfig | None = None, model_path=MODEL_PATH, map_key: str | None = None):
        self.sim_cfg = sim_cfg or arch.SimConfig()
        self.model_path = model_path
        self.firms = FirmsClient(map_key=map_key)
        self.model: HarmonizerModel | None = None
        self.regions: dict = {}
        self.dailies: dict = {}
        self.kappa = 1.0
        self.validation: dict = {}
        self.catalog_cells: set = set()
        self.ready = False
        self._live_cache: dict = {}
        self.load_seconds = 0.0
        self.trained_now = False

    # ------------------------------------------------------------------
    # loading
    # ------------------------------------------------------------------
    def load(self, retrain: bool = False, progress=print):
        t0 = time.time()
        progress("loading benchmark archives ...")
        data = arch.load_all(self.sim_cfg, progress=lambda rid, y: None)
        prepared, cfs, cats = {}, {}, {}
        for rid, d in data.items():
            df, cf, cat = pl.prepare_region(d)
            prepared[rid], cfs[rid], cats[rid] = df, cf, cat
            self.catalog_cells |= cat["cells"]
        progress("prepared %d detections across %d regions" % (sum(len(v) for v in prepared.values()), len(prepared)))

        model = None
        if not retrain and self.model_path.exists():
            try:
                model = HarmonizerModel.load(self.model_path)
                progress("loaded trained model %s" % model.version)
            except Exception as exc:  # noqa: BLE001
                progress(f"model unusable ({exc}); retraining")
        if model is None:
            progress("training biome-stratified quantile harmonizer ...")
            model = pl.train_harmonizer(prepared, progress=progress)
            model.save(self.model_path)
            self.trained_now = True
            progress("model saved -> %s" % self.model_path)
        self.model = model

        for rid in list(prepared):
            df = pl.attach_harmonized(prepared[rid], model)
            daily = pl.daily_series(df, cfs[rid])
            self.dailies[rid] = daily
            self.regions[rid] = {
                "df": _slim(df).reset_index(drop=True), "catalog": cats[rid], "cf_rows": int(len(cfs[rid])),
                "events": int(len(data[rid]["events"])),
            }
            del prepared[rid], df
        self._compute_validation()
        self.load_seconds = time.time() - t0
        self.ready = True
        progress("ready in %.1fs" % self.load_seconds)
        return self

    # ------------------------------------------------------------------
    # series & validation
    # ------------------------------------------------------------------
    def daily(self, region: str = "all") -> pd.DataFrame:
        if region == "all":
            allp = pd.concat(self.dailies.values())
            return allp.groupby(level=0).sum(min_count=1)
        return self.dailies[region]

    def _monthly(self, region: str):
        m = self.daily(region).resample("MS").sum(min_count=1)
        return m.loc[:"2026-10-01"]  # complete months only

    def _est_sd(self, m: pd.DataFrame) -> pd.Series:
        return np.sqrt(m["harmonized_var"]) * self.kappa

    def _compute_validation(self):
        # empirical variance inflation from the calibration years (2012-2021)
        z = []
        for rid in self.dailies:
            m = self._monthly(rid).loc["2012-01-01":"2021-12-01"]
            sd_ = np.sqrt(m["harm_modis_var"])
            ok = sd_ > 0
            z.append(((m["viirs_native"] - m["harm_modis_only"]) / sd_)[ok])
        z = pd.concat(z)
        self.kappa = float(max(1.0, np.std(z))) if len(z) else 1.0

        def block(m, ref, label, ref_label):
            est, raw = m["harm_modis_only"], m["modis_filtered"]
            sdv = np.sqrt(m["harm_modis_var"]) * self.kappa
            metrics = sd.harmonization_metrics(ref.to_numpy(), raw.to_numpy(), est.to_numpy())
            cov = float(np.mean(np.abs(ref - est) <= Z90 * sdv)) if len(ref) else None
            metrics.update({"window": label, "reference": ref_label, "ci90_coverage": cov})
            return metrics

        out = {"kappa": self.kappa, "regions": {}}
        for rid in list(self.dailies) + ["all"]:
            m = self._monthly(rid)
            hold = m.loc["2022-01-01":]
            pre = m.loc[:"2011-12-01"]
            out["regions"][rid] = {
                "holdout_2022_2026": block(hold, hold["viirs_native"], "2022-01..2026-10 (hold-out, drifted orbits)",
                                           "VIIRS native (SNPP/NOAA-20)"),
                "counterfactual_2001_2011": block(pre, pre["counterfactual_snpp"], "2001-01..2011-12 (pre-VIIRS)",
                                                  "hidden counterfactual SNPP record"),
            }
        from src.engine.static_filter import evaluate_against_truth
        out["static_filter"] = {rid: evaluate_against_truth(r["df"], r["df"]["is_static"].to_numpy())
                                for rid, r in self.regions.items()}
        out["static_filter"]["catalog_cells"] = {rid: int(len(r["catalog"]["cells"])) for rid, r in self.regions.items()}
        self.validation = out

    def series_payload(self, region: str = "all", freq: str = "monthly") -> dict:
        d = self.daily(region)
        step = "MS" if freq == "monthly" else "YS"
        m = d.resample(step).sum(min_count=1)
        if freq == "monthly":
            m = m.loc[:"2026-10-01"]
        est = m["harmonized"]
        sdv = np.sqrt(m["harmonized_var"]) * self.kappa
        truth = m["counterfactual_snpp"].where(m.index < VSTART, m["viirs_native"])
        fmt = "%Y-%m" if freq == "monthly" else "%Y"
        return clean({
            "region": region, "freq": freq, "labels": [t.strftime(fmt) for t in m.index],
            "raw_all": m["raw_all"], "raw_filtered": m["raw_filtered"], "raw_modis": m["raw_modis"], "raw_viirs": m["raw_viirs"],
            "harmonized": est, "harmonized_lo": (est - Z90 * sdv).clip(lower=0), "harmonized_hi": est + Z90 * sdv,
            "truth_snpp_everywhere": truth, "static_removed": m["static_removed"],
            "transition": "2012-01-20", "partial_last_period": freq == "annual",
        })

    def break_payload(self, region: str = "all") -> dict:
        m = self._monthly(region)
        truth = m["counterfactual_snpp"].where(m.index < VSTART, m["viirs_native"])
        series = {"raw_all": m["raw_all"], "raw_filtered": m["raw_filtered"],
                  "harmonized": m["harmonized"], "truth": truth}
        reports = {k: sd.structural_break_report(v) for k, v in series.items()}
        return clean({"region": region, "reports": reports, "verdict": sd.break_verdict(reports)})

    def calendar_payload(self, region: str = "all", series: str = "harmonized") -> dict:
        d = self.daily(region)
        col = "harmonized" if series == "harmonized" else "raw_all"
        out = build_calendar(d[col].astype(float), target_year=2026)
        out["region"], out["series"] = region, series
        return clean(out)

    def validation_payload(self) -> dict:
        return clean(self.validation)

    def model_payload(self) -> dict:
        m = self.model
        return clean({
            "version": m.version, "metrics": m.metrics, "importance": m.importance,
            "calibration": {BIOMES[g]: v for g, v in m.calibration.items()},
            "conformal_margin_log": {f"{BIOMES[g]}/{t}": v for (g, t), v in m.conformal.items()},
            "matchups": getattr(m, "matchup_summary", {}),
            "feature_groups": list(__import__("src.engine.harmonizer_ml", fromlist=["FEATURE_GROUPS"]).FEATURE_GROUPS),
            "kappa": self.kappa,
            "diurnal": self._diurnal_model(),
        })

    @staticmethod
    def _diurnal_model() -> dict:
        hrs = np.arange(0, 24.01, 0.25)
        years = np.arange(2012, 2027.01, 0.5)
        return {
            "params": {"peak_hour": DIURNAL.peak_hour, "sigma_hours": DIURNAL.sigma_hours, "floor": DIURNAL.floor,
                       "day_reference_hour": DIURNAL.day_reference_hour,
                       "night_reference_hour": DIURNAL.night_reference_hour, "clip": list(DIURNAL.clip)},
            "curve": {"lst": hrs.tolist(), "relative_intensity": np.asarray(diurnal_factor(hrs), dtype=float).round(4).tolist()},
            "overpass_drift": {"years": years.tolist(),
                               **{s: np.asarray(nominal_day_lst(s, years), dtype=float).round(3).tolist()
                                  for s in ("Terra", "Aqua", "SNPP", "NOAA20")}},
        }

    def regions_payload(self) -> list:
        out = []
        for rid, cfg in REGIONS.items():
            r = self.regions[rid]
            df = r["df"]
            out.append(clean({
                "id": rid, "name": cfg.name, "regime": cfg.regime, "bbox": cfg.bbox, "center": cfg.center,
                "biome_mix": dict(zip(BIOMES, cfg.biome_mix)), "detections": int(len(df)),
                "static_cells": int(len(r["catalog"]["cells"])), "events": r["events"],
                "range": [str(df["ts"].min().date()), str(df["ts"].max().date())],
            }))
        return out

    def static_sources(self, region: str | None = None) -> dict:
        out = {"detected": [], "known_seed_sources": [
            {"name": n, "kind": k, "lat": a, "lon": b, "radius_km": r} for n, k, a, b, r in KNOWN_SOURCES]}
        for rid, r in self.regions.items():
            if region and region != rid:
                continue
            tab = r["catalog"]["table"]
            cfg = REGIONS[rid]
            for _, row in tab[tab["static"]].iterrows():
                lat, lon = geo.cell_center(int(row["cell"]))
                near = None
                for s in cfg.static_sources:
                    if abs(s.lat - lat) < 0.06 and abs(s.lon - lon) < 0.08:
                        near = s.name
                out["detected"].append({
                    "region": rid, "cell": _hexstr(int(row["cell"])), "lat": lat, "lon": lon,
                    "years_flagged": int(row["years_flagged"]), "median_days_per_year": float(row["median_days"]),
                    "monthly_cv": float(row["monthly_cv"]) if pd.notna(row["monthly_cv"]) else None,
                    "halo": bool(row["halo"]), "kind": r["catalog"]["kinds"].get(int(row["cell"]), "industrial"),
                    "scenario_source": near,
                })
        return clean(out)

    # ------------------------------------------------------------------
    # hex layers
    # ------------------------------------------------------------------
    @staticmethod
    def _time_slice(df: pd.DataFrame, start, end):
        m = np.ones(len(df), dtype=bool)
        if start is not None:
            m &= (df["ts"] >= pd.Timestamp(start)).to_numpy()
        if end is not None:
            m &= (df["ts"] < pd.Timestamp(end) + pd.Timedelta(days=1)).to_numpy()
        return m

    @staticmethod
    def _hex_table(sub: pd.DataFrame, rule: str, kappa: float):
        valid = (sub["conf_ok"] & ~sub["is_static"]).to_numpy()
        modis = (sub["instrument"] == "MODIS").to_numpy()
        ts = sub["ts"]
        if rule == "spliced":
            use_ref = (modis & (ts < VSTART).to_numpy()) | (~modis & (ts >= VSTART).to_numpy())
        else:
            use_ref = np.ones(len(sub), dtype=bool)
        hw = sub["h_w"].to_numpy(dtype=float)
        g = pd.DataFrame({
            "cell": sub["h3_7"].to_numpy(),
            "raw_total": 1, "raw_modis": modis.astype(int), "raw_viirs": (~modis).astype(int),
            "static_n": sub["is_static"].to_numpy().astype(int),
            "lowconf_n": (~sub["conf_ok"].to_numpy() & ~sub["is_static"].to_numpy()).astype(int),
            "harm_modis": hw * (valid & modis), "viirs_native": hw * (valid & ~modis),
            "viirs_valid": (valid & ~modis).astype(int),
            "ref": hw * (valid & use_ref),
            "var": sub["h_sd"].to_numpy(dtype=float) ** 2 * (valid & use_ref & modis),
            "var_m": sub["h_sd"].to_numpy(dtype=float) ** 2 * (valid & modis),
            "frp_raw": sub["frp"].to_numpy(dtype=float) * valid,
            "frp_harm": sub["frp_harm"].to_numpy(dtype=float) * (valid & use_ref),
            "n_ref": (valid & use_ref).astype(int),
        })
        t = g.groupby("cell", sort=False).sum()
        t["harmonized"] = t["ref"] if rule == "spliced" else np.where(t["viirs_valid"] > 0, t["viirs_native"], t["harm_modis"])
        if rule == "spliced":
            sdv = np.sqrt(t["var"]) * kappa
        else:
            sdv = np.where(t["viirs_valid"] > 0, 0.0, np.sqrt(t["var_m"]) * kappa)
        t["lo"] = np.maximum(t["harmonized"] - Z90 * sdv, 0)
        t["hi"] = t["harmonized"] + Z90 * sdv
        t["frp_raw_mean"] = t["frp_raw"] / np.maximum(t["raw_total"] - t["static_n"] - t["lowconf_n"], 1)
        t["frp_harm_mean"] = t["frp_harm"] / np.maximum(t["n_ref"], 1)
        return t.reset_index()

    @staticmethod
    def _totals(t: pd.DataFrame) -> dict:
        sig = float(np.sqrt(((t["hi"] - t["lo"]) / (2 * Z90)).pow(2).sum()))
        est = float(t["harmonized"].sum())
        return {
            "raw_total": int(t["raw_total"].sum()), "raw_modis": int(t["raw_modis"].sum()),
            "raw_viirs": int(t["raw_viirs"].sum()), "static_removed": int(t["static_n"].sum()),
            "lowconf_removed": int(t["lowconf_n"].sum()), "harmonized": est,
            "harmonized_lo90": max(est - Z90 * sig, 0.0), "harmonized_hi90": est + Z90 * sig,
            "n_cells": int(len(t)), "static_cells": int((t["static_n"] >= 0.5 * t["raw_total"]).sum()),
        }

    def _hex_payload(self, sub: pd.DataFrame, rule: str, limit: int = 5000):
        if sub.empty:
            return {"cells": [], "truncated": False, "totals": {}}
        t = self._hex_table(sub, rule, self.kappa)
        truncated = len(t) > limit
        tot = self._totals(t)          # totals always cover ALL cells, not just the drawn ones
        t = t.sort_values("raw_total", ascending=False).head(limit)
        cells = []
        for r in t.itertuples(index=False):
            c = int(r.cell)
            cells.append({
                "h3": _hexstr(c), "boundary": _boundary(c),
                "raw_total": int(r.raw_total), "raw_modis": int(r.raw_modis), "raw_viirs": int(r.raw_viirs),
                "static_n": int(r.static_n), "static": bool(r.static_n > 0 and r.static_n >= 0.5 * r.raw_total),
                "harmonized": round(float(r.harmonized), 3), "lo90": round(float(r.lo), 3), "hi90": round(float(r.hi), 3),
                "harm_modis": round(float(r.harm_modis), 3), "viirs_native": round(float(r.viirs_native), 3),
                "frp_raw_mean": round(float(r.frp_raw_mean), 2), "frp_harm_mean": round(float(r.frp_harm_mean), 2),
            })
        return {"cells": cells, "truncated": truncated, "totals": tot, "rule": rule}

    def hexes(self, region: str, start=None, end=None) -> dict:
        df = self.regions[region]["df"]
        sub = df[self._time_slice(df, start, end)]
        out = self._hex_payload(sub, "spliced")
        out.update({"region": region, "start": start, "end": end, "source": "benchmark"})
        return clean(out)

    # ------------------------------------------------------------------
    # live
    # ------------------------------------------------------------------
    def _synthetic_tail(self, days: int, bbox):
        parts = []
        for rid, r in self.regions.items():
            df = r["df"]
            parts.append(df[df["ts"] >= LAST_DAY - pd.Timedelta(days=days)])
        df = pd.concat(parts, ignore_index=True)
        if bbox is not None:
            s, w, n, e = bbox
            df = df[(df.latitude >= s) & (df.latitude <= n) & (df.longitude >= w) & (df.longitude <= e)]
        return df.reset_index(drop=True)

    def get_live(self, days: int = 1, bbox=None, force: bool = False) -> tuple:
        key = (int(days), None if bbox is None else tuple(round(v, 1) for v in bbox))
        hit = self._live_cache.get(key)
        if hit and not force and time.time() - hit["t"] < 600:
            return hit["df"], hit["meta"]
        raw, meta = self.firms.fetch_live(days, bbox, force)
        if raw.empty:
            df = self._synthetic_tail(days, bbox)
            meta.update({"live": False, "tiers": ["synthetic-benchmark-tail"],
                         "warning": "No FIRMS data reachable; showing the synthetic benchmark tail."})
            self._live_cache[key] = {"t": time.time(), "df": df, "meta": meta}
            return df, meta
        df = prepare(raw)
        df = tag_static(df, self.catalog_cells, use_seeds=True, provisional=(days >= 5))
        nm = max(1, df.loc[df["instrument"] == "MODIS", "satellite"].nunique())
        nv = max(1, df.loc[df["instrument"] == "VIIRS", "satellite"].nunique())
        df = pl.attach_harmonized(df, self.model, n_ops_modis=nm, n_ops_viirs=nv)
        df = _slim(df).reset_index(drop=True)
        meta.update({"platforms": {"MODIS": nm, "VIIRS": nv}})
        if meta["tiers"] and all(t == "bundled-sample" for t in meta["tiers"]):
            meta["warning"] = "Offline: showing the bundled real FIRMS snapshot (2026-11-13/14), not live data."
        elif "stale-cache" in meta["tiers"] or "bundled-sample" in meta["tiers"]:
            meta["warning"] = "Some products are cached or bundled; see per-source tiers."
        self._live_cache[key] = {"t": time.time(), "df": df, "meta": meta}
        return df, meta

    def live_hexes(self, days: int = 1, bbox=None, force: bool = False) -> dict:
        df, meta = self.get_live(days, bbox, force)
        out = self._hex_payload(df, "live", limit=4000)
        out.update({"source": "live", "meta": meta})
        return clean(out)

    # ------------------------------------------------------------------
    # detail / ROI
    # ------------------------------------------------------------------
    def _dataset(self, source: str, region: str | None, days: int = 1, bbox=None):
        if source == "live":
            df, meta = self.get_live(days, bbox)
            return df, "live", meta
        return self.regions[region]["df"], "spliced", {}

    def _explain_summary(self, sub: pd.DataFrame, max_rows: int = 300) -> dict:
        modis = sub[(sub["instrument"] == "MODIS") & sub["conf_ok"] & ~sub["is_static"]]
        if modis.empty:
            return {"frp": [], "n": [], "n_rows": 0}
        modis = modis.nlargest(max_rows, "frp") if len(modis) > max_rows else modis
        res = {"n_rows": int(len(modis))}
        for tgt in ("frp", "n"):
            ex = self.model.explain(modis, top_k=5, target=tgt)
            acc: dict = {}
            for e in ex:
                for c in e["contributions"]:
                    acc.setdefault(c["driver"], []).append(c["effect_pct"])
            res[tgt] = sorted(({"driver": k, "mean_effect_pct": round(float(np.mean(v)), 1),
                                "p10": round(float(np.percentile(v, 10)), 1), "p90": round(float(np.percentile(v, 90)), 1)}
                               for k, v in acc.items()), key=lambda d: -abs(d["mean_effect_pct"]))
        return res

    @staticmethod
    def _diurnal(sub: pd.DataFrame) -> dict:
        if sub.empty:
            return {}
        lst = sub["lst"].to_numpy(dtype=float)
        sat = sub["satellite"].astype(str)
        by_sat = {}
        for s, g in sub.groupby(sat):
            by_sat[s] = {"mean_lst": round(float(np.mean(g["lst"])), 2),
                         "mean_diurnal_factor": round(float(np.mean(diurnal_factor(g["lst"].to_numpy(dtype=float)))), 3),
                         "n": int(len(g))}
        return {"by_satellite": by_sat, "mean_lst": round(float(np.mean(lst)), 2),
                "peak_hour": DIURNAL.peak_hour, "reference_day": DIURNAL.day_reference_hour,
                "reference_night": DIURNAL.night_reference_hour}

    def hex_detail(self, cell: str, source: str = "benchmark", region: str | None = None, start=None, end=None,
                   days: int = 1, bbox=None) -> dict:
        df, rule, meta = self._dataset(source, region, days, bbox)
        c = geo.h3_int(cell)
        m = np.array((df["h3_7"] == c).to_numpy(), copy=True)
        if source != "live":
            m = m & self._time_slice(df, start, end)
        sub = df[m]
        if sub.empty:
            return {"h3": cell, "empty": True}
        tab = self._hex_table(sub, rule, self.kappa).iloc[0]
        lat, lon = geo.cell_center(c)
        sat = sub.groupby(sub["satellite"].astype(str)).agg(
            n=("frp", "size"), frp_mean=("frp", "mean"), area_mean=("area_km2", "mean"))
        top = sub[(sub["instrument"] == "MODIS") & sub["conf_ok"] & ~sub["is_static"]].nlargest(8, "frp")
        rows = []
        if len(top):
            ex = self.model.explain(top, top_k=4, target="frp")
            pr = self.model.predict(add_cell_context(top) if "log_cell_n" not in top.columns else top)
            for (i, r), e, (_, p) in zip(top.iterrows(), ex, pr.iterrows()):
                rows.append({
                    "time": str(r["ts"]), "satellite": str(r["satellite"]), "lat": float(r["latitude"]),
                    "lon": float(r["longitude"]), "frp_obs": float(r["frp"]), "footprint_km2": float(r["area_km2"]),
                    "bowtie_x": float(r["area_km2"]), "lst": float(r["lst"]),
                    "diurnal_factor": float(diurnal_factor(r["lst"])),
                    "frp_harm_p10": float(p["frp_p10"]), "frp_harm_p50": float(p["frp_p50"]),
                    "frp_harm_p90": float(p["frp_p90"]), "frp_lo90": float(p["frp_lo90"]), "frp_hi90": float(p["frp_hi90"]),
                    "n_p50": float(p["n_p50"]), "n_lo90": float(p["n_lo90"]), "n_hi90": float(p["n_hi90"]),
                    "scale_factor": float(p["scale_factor"]), "drivers": e["contributions"],
                })
        return clean({
            "h3": cell, "lat": lat, "lon": lon, "boundary": _boundary(c), "empty": False,
            "area_km2": geo.cell_area_km2(7),
            "totals": {k: tab[k] for k in ("raw_total", "raw_modis", "raw_viirs", "static_n", "lowconf_n", "harmonized",
                                           "lo", "hi", "harm_modis", "viirs_native", "frp_raw_mean", "frp_harm_mean")},
            "biome": BIOMES[int(sub["biome"].iloc[0])],
            "by_satellite": sat.round(2).to_dict("index"),
            "diurnal": self._diurnal(sub), "top_modis_detections": rows,
            "drivers": self._explain_summary(sub),
        })

    def roi(self, polygon, source: str = "benchmark", region: str | None = None, start=None, end=None,
            days: int = 1, bbox=None) -> dict:
        cells = geo.cells_in_polygon(polygon, geo.RES_REGIONAL)
        df, rule, meta = self._dataset(source, region, days, bbox)
        m = np.array(df["h3_7"].isin(cells).to_numpy(), copy=True)
        if source != "live":
            m = m & self._time_slice(df, start, end)
        sub = df[m]
        out = {"source": source, "region": region, "n_cells_in_roi": len(cells),
               "roi_area_km2": len(cells) * geo.cell_area_km2(7), "start": start, "end": end}
        if sub.empty:
            out["empty"] = True
            return clean(out)
        t = self._hex_table(sub, rule, self.kappa)
        sig = np.sqrt(((t["hi"] - t["lo"]) / (2 * Z90)).pow(2).sum())
        est = float(t["harmonized"].sum())
        valid = sub["conf_ok"] & ~sub["is_static"]
        by_biome = {}
        for b, g in sub[valid].groupby("biome"):
            tb = self._hex_table(g, rule, self.kappa)
            by_biome[BIOMES[int(b)]] = {"harmonized": round(float(tb["harmonized"].sum()), 2),
                                         "raw": int(tb["raw_total"].sum())}
        sat = sub.groupby(sub["satellite"].astype(str)).size().to_dict()
        tsd = sub["ts"].dt.floor("D" if (sub["ts"].max() - sub["ts"].min()).days < 400 else "MS")
        ts_raw = sub.groupby(tsd).size()
        hsub = sub[valid.to_numpy()]
        if rule == "spliced":
            modis_h = (hsub["instrument"] == "MODIS").to_numpy()
            use = (modis_h & (hsub["ts"] < VSTART).to_numpy()) | (~modis_h & (hsub["ts"] >= VSTART).to_numpy())
            hsub = hsub[use]
        ts_h = hsub.groupby(hsub["ts"].dt.floor("D" if (sub["ts"].max() - sub["ts"].min()).days < 400 else "MS"))["h_w"].sum()
        out.update({
            "empty": False,
            "totals": {
                "raw_total": int(t["raw_total"].sum()), "raw_modis": int(t["raw_modis"].sum()),
                "raw_viirs": int(t["raw_viirs"].sum()), "static_removed": int(t["static_n"].sum()),
                "lowconf_removed": int(t["lowconf_n"].sum()), "harmonized": est,
                "harmonized_lo90": max(est - Z90 * sig, 0.0), "harmonized_hi90": est + Z90 * sig,
                "frp_raw_mean": float((sub["frp"][valid]).mean()) if valid.any() else None,
                "frp_harm_mean": float(hsub["frp_harm"].mean()) if len(hsub) else None,
            },
            "by_biome": by_biome, "by_satellite": sat,
            "timeseries": {"labels": [str(i.date()) for i in ts_raw.index], "raw": ts_raw.tolist(),
                           "harmonized": ts_h.reindex(ts_raw.index, fill_value=0.0).round(2).tolist()},
            "diurnal": self._diurnal(sub), "drivers": self._explain_summary(sub),
            "static_cells": int((t["static_n"] > 0).sum()),
        })
        return clean(out)

    # ------------------------------------------------------------------
    # briefing
    # ------------------------------------------------------------------
    def _recent_z(self, region: str, end, win: int = 7):
        """Robust z of the latest ``win``-day harmonized mean vs the same calendar window in earlier years."""
        d = self.daily(region)["harmonized"].astype(float)
        end = pd.Timestamp(end).normalize()

        def wmean(e):
            s = d.loc[e - pd.Timedelta(days=win - 1): e]
            return float(s.mean()) if len(s) == win else np.nan

        cur = wmean(end)
        hist = np.array([wmean(end - pd.DateOffset(years=k)) for k in range(1, end.year - 2000)])
        hist = hist[np.isfinite(hist)]
        if len(hist) < 8 or not np.isfinite(cur):
            return None
        lh = np.log1p(hist)
        med = float(np.median(lh))
        scale = max(1.4826 * float(np.median(np.abs(lh - med))), 0.35)
        return round((float(np.log1p(cur)) - med) / scale, 2)

    def briefing(self, source: str = "benchmark", region: str | None = "california", start=None, end=None,
                 polygon=None, days: int = 1, bbox=None) -> dict:
        from src.analytics.briefing_generator import generate
        if source == "benchmark":
            region = region or "california"
            end = end or str(LAST_DAY.date())
            start = start or str((pd.Timestamp(end) - pd.Timedelta(days=13)).date())
        df, rule, meta = self._dataset(source, region, days, bbox)
        m = np.ones(len(df), dtype=bool)
        if polygon:
            m = m & df["h3_7"].isin(geo.cells_in_polygon(polygon, geo.RES_REGIONAL)).to_numpy()
        if source != "live":
            m = m & self._time_slice(df, start, end)
        sub = df[m]
        if sub.empty:
            return {"empty": True, "markdown": "# No detections in the selected scope.", "json": {}}
        t = self._hex_table(sub, rule, self.kappa)
        sig = float(np.sqrt(((t["hi"] - t["lo"]) / (2 * Z90)).pow(2).sum()))
        est = float(t["harmonized"].sum())
        n_valid = float((t["raw_total"] - t["static_n"] - t["lowconf_n"]).sum())
        totals = {
            "raw_total": int(t["raw_total"].sum()), "static_removed": int(t["static_n"].sum()),
            "lowconf_removed": int(t["lowconf_n"].sum()), "harmonized": est,
            "harmonized_lo90": max(est - Z90 * sig, 0.0), "harmonized_hi90": est + Z90 * sig,
            "frp_raw_mean": float(t["frp_raw"].sum() / n_valid) if n_valid else None,
            "frp_harm_mean": float(t["frp_harm"].sum() / max(t["n_ref"].sum(), 1)),
        }
        t2 = t[t["static_n"] < 0.5 * t["raw_total"]].sort_values("harmonized", ascending=False).head(8)
        top = [{"h3": _hexstr(int(r.cell)), "raw_total": int(r.raw_total), "harmonized": float(r.harmonized),
                "lo90": float(r.lo), "hi90": float(r.hi), "frp_harm_mean": float(r.frp_harm_mean)}
               for r in t2.itertuples(index=False)]
        climate = None
        if source == "benchmark" and region in self.regions:
            cal = self.calendar_payload(region)
            eps = [e for e in cal["anomalies"]["episodes"]]
            climate = {"recent_z": self._recent_z(region, end), "episodes": eps}
        val = (self.validation.get("regions", {}) or {}).get(region or "all") or (self.validation.get("regions", {}) or {}).get("all")
        if source == "live":
            tiers = ", ".join(meta.get("tiers", []))
            quality = {"basis": f"FIRMS live feed ({tiers})", "synthetic": False,
                       "non_live": not meta.get("live", False), "notes": [meta["warning"]] if meta.get("warning") else []}
            scope = "FIRMS live" + (" - ROI polygon" if polygon else " - current extent")
            window = f"last {days} day(s)"
        else:
            quality = {"basis": "Simulated 2001-2026 benchmark archive (known ground truth)", "synthetic": True,
                       "non_live": False, "notes": []}
            scope = f"{REGIONS[region].name}" + (" - ROI polygon" if polygon else " - whole region")
            window = f"{start} to {end}"
        ctx = {"scope": scope, "window": window, "totals": totals, "top_cells": top, "climate": climate,
               "drivers": self._explain_summary(sub), "quality": quality, "validation": val,
               "centre": [float(sub["latitude"].mean()), float(sub["longitude"].mean())]}
        return clean(generate(ctx))

    # ------------------------------------------------------------------
    # user-supplied detections
    # ------------------------------------------------------------------
    def harmonize_detections(self, rows: list[dict]) -> dict:
        satmap = {"terra": "Terra", "aqua": "Aqua", "snpp": "SNPP", "npp": "SNPP", "noaa20": "NOAA20", "n20": "NOAA20",
                  "t": "Terra", "a": "Aqua", "n": "SNPP"}
        recs = []
        for r in rows:
            sat = satmap.get(str(r["satellite"]).lower(), "SNPP")
            recs.append({
                "latitude": r["latitude"], "longitude": r["longitude"], "acq_date": r["acq_date"],
                "acq_time": r["acq_time"], "satellite": sat, "instrument": "MODIS" if sat in ("Terra", "Aqua") else "VIIRS",
                "scan": r.get("scan") or (1.0 if sat in ("Terra", "Aqua") else 0.4),
                "track": r.get("track") or (1.0 if sat in ("Terra", "Aqua") else 0.4), "frp": r["frp"],
                "bright_t21": r.get("brightness") or 330.0, "bright_t31": r.get("bright_t31") or 290.0,
                "confidence": r.get("confidence") if r.get("confidence") is not None else 65,
            })
        df = pd.DataFrame(recs)
        df["ts"] = firms_to_timestamp(df.pop("acq_date"), df.pop("acq_time")).to_numpy()
        df = prepare(df)
        df = tag_static(df, self.catalog_cells, use_seeds=True)
        df = pl.attach_harmonized(df, self.model)
        modis = df[(df["instrument"] == "MODIS") & df["conf_ok"] & ~df["is_static"]]
        expl = {}
        if len(modis):
            ctx = add_cell_context(modis)
            pr = self.model.predict(ctx)
            ex = self.model.explain(ctx, top_k=5, target="frp")
            for (i, p), e in zip(pr.iterrows(), ex):
                expl[i] = (p, e)
        out = []
        for i, r in df.iterrows():
            item = {
                "index": int(i), "satellite": str(r["satellite"]), "h3_res7": geo.h3_str(r["h3_7"]),
                "h3_res8": geo.h3_str(geo.h3_index([r["latitude"]], [r["longitude"]], 8)[0]),
                "biome": BIOMES[int(r["biome"])], "footprint_km2": float(r["area_km2"]),
                "local_solar_time": float(r["lst"]), "diurnal_factor": float(diurnal_factor(r["lst"])),
                "frp_diurnal_normalized": float(r["frp_norm"]), "is_static": bool(r["is_static"]),
                "low_confidence": bool(not r["conf_ok"]), "frp_obs": float(r["frp"]),
            }
            if i in expl:
                p, e = expl[i]
                item.update({k: float(p[k]) for k in ("frp_p10", "frp_p50", "frp_p90", "frp_lo90", "frp_hi90",
                                                      "n_p10", "n_p50", "n_p90", "n_lo90", "n_hi90", "scale_factor")})
                item["frp_harm"] = float(p["frp_p50"])
                item["equivalent_viirs_hotspots"] = float(p["n_p50"])
                item["drivers"] = e["contributions"]
            elif r["instrument"] == "VIIRS":
                item.update({"frp_harm": float(r["frp"]), "equivalent_viirs_hotspots": 1.0, "scale_factor": 1.0,
                             "drivers": [], "note": "VIIRS is the reference scale"})
            out.append(item)
        return clean({"detections": out, "model": self.model.version})
