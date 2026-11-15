"""AetherState: loads archives, trains/loads the harmonizer, answers every query the API needs."""
from __future__ import annotations

import logging
import time
from functools import lru_cache

import numpy as np
import pandas as pd

from src.analytics.burning_calendar import build_calendar, current_month_vs_median, month_year_matrix
from src.data import benchmark_archives as arch
from src.data.archive_store import ArchiveStore, polygon_bbox
from src.data.firms_client import FirmsClient
from src.data.regions import BIOMES, REGIONS
from src.data.remote_roi import REMOTE_DEFAULT_YEARS, REMOTE_MAX_YEARS, RemoteRoiClient
from src.engine import geometry_h3 as geo
from src.engine import pipeline as pl
from src.engine import stats_diagnostics as sd
from src.engine.confidence_policies import PRIMARY_POLICY, confidence_filter_sensitivity
from src.engine.diurnal_normalizer import (
    PARAMS as DIURNAL,
    diurnal_factor,
    firms_to_timestamp,
    nominal_day_lst,
)
from src.engine.harmonizer_ml import MODEL_PATH, HarmonizerModel, add_cell_context
from src.engine.prepare import prepare
from src.engine.sensors import TIMELINE_MARKERS, VIIRS_START, n_operating
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
        self.archive = ArchiveStore("tr")
        self.remote = RemoteRoiClient(map_key=map_key)
        self._area_cache: dict | None = None  # seçilen ROI için günlük seri önbelleği

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
                "synthetic_holdout_2022_2026": block(
                    hold, hold["viirs_native"],
                    "Synthetic benchmark hold-out 2022-01..2026-10 (NOT real NASA FIRMS)",
                    "Synthetic VIIRS-native reference (SNPP/NOAA-20 sim)",
                ),
                "synthetic_counterfactual_2001_2011": block(
                    pre, pre["counterfactual_snpp"],
                    "Synthetic pre-VIIRS 2001-01..2011-12 (counterfactual SNPP record)",
                    "Hidden synthetic counterfactual SNPP",
                ),
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
        matchups = dict(getattr(m, "matchup_summary", {}) or {})
        matchups.setdefault("evaluation_kind", "synthetic_benchmark")
        matchups.setdefault(
            "evaluation_note",
            "Train/calib/test year blocks are synthetic-benchmark only — not real NASA FIRMS hold-out.",
        )
        matchups.setdefault("geographic_generalization", "not_validated")
        return clean({
            "version": m.version, "metrics": m.metrics,
            "importance": m.importance,
            "importance_label": "model sensitivity / inputs that affect predictions (not causal explanation)",
            "calibration": {BIOMES[g]: v for g, v in m.calibration.items()},
            "conformal_margin_log": {f"{BIOMES[g]}/{t}": v for (g, t), v in m.conformal.items()},
            "matchups": matchups,
            "feature_groups": list(__import__("src.engine.harmonizer_ml", fromlist=["FEATURE_GROUPS"]).FEATURE_GROUPS),
            "kappa": self.kappa,
            "diurnal": self._diurnal_model(),
            "tracks": {
                "synthetic_benchmark": "Known simulation truth; year blocks 2012–2019 / 2020–2021 / 2022–2026",
                "real_firms_archive": "Monthly VIIRS comparison reference on Turkey ROI — not ground truth",
            },
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
                                  for s in ("Terra", "Aqua", "SNPP", "NOAA20", "NOAA21")}},
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
            # Ham Veriyi Koruma: boş FIRMS yanıtında yapay nokta üretilmez.
            meta.update({"live": False, "tiers": meta.get("tiers") or ["empty"],
                         "warning": "NASA FIRMS verisine ulaşılamadı; sentetik veri üretilmedi."})
            empty = pd.DataFrame()
            self._live_cache[key] = {"t": time.time(), "df": empty, "meta": meta}
            return empty, meta
        df = prepare(raw)
        df = tag_static(df, self.catalog_cells, use_seeds=True, provisional=(days >= 5))
        # Time-varying platform counts (avoids step when NOAA-21 joins NOAA-20).
        nm = n_operating(df["ts"], "MODIS")
        nv = n_operating(df["ts"], "VIIRS")
        df = pl.attach_harmonized(df, self.model, n_ops_modis=nm, n_ops_viirs=nv)
        df = _slim(df).reset_index(drop=True)
        sat_counts = {str(k): int(v) for k, v in df["satellite"].value_counts().items()} if len(df) else {}
        meta.update({
            "platforms": {
                "MODIS": float(np.mean(nm)) if len(nm) else 0,
                "VIIRS": float(np.mean(nv)) if len(nv) else 0,
            },
            "satellites": sat_counts,
            "times_utc": True,
            "product_kind": "nrt",
            "timeline_markers": TIMELINE_MARKERS,
        })
        tiers = meta.get("tiers") or []
        if tiers and all(t == "bundled-sample" for t in tiers):
            meta["warning"] = "Offline: bundled real FIRMS snapshot — not live data."
            meta["live"] = False
        elif "stale-cache" in tiers or "bundled-sample" in tiers:
            meta["warning"] = (
                "Some products are stale-cache or bundled; see per-source tiers. "
                "Not a fresh NASA pull — observation window follows newest/oldest timestamps."
            )
            meta["live"] = False
        elif tiers and all(t == "cache" for t in tiers):
            meta["warning"] = (
                "Fresh disk cache (within TTL) — not a new live API/open-feed pull this request. "
                "See source_fetched_at vs retrieved_at."
            )
            meta["live"] = False
        elif "cache" in tiers and not meta.get("live"):
            meta["warning"] = (
                "Mixed tiers include cache; live=false unless every non-empty product was "
                "fetched from API/open-feed this request."
            )
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
    def _dataset(self, source: str, region: str | None, days: int = 1, bbox=None, polygon=None):
        if source == "live":
            df, meta = self.get_live(days, bbox)
            return df, "live", meta
        if source == "archive":
            # API bbox = [south, west, north, east]; archive/store = (west, south, east, north)
            store_bbox = None
            if bbox is not None:
                s, w, n, e = bbox
                store_bbox = (float(w), float(s), float(e), float(n))
            if polygon is None and store_bbox is None:
                raise ValueError(
                    "Gerçek arşiv brifingi için haritada bir poligon çiz "
                    f"(üst sınır ~{int(self.archive.status().get('max_area_km2', 120000)):,} km²; "
                    "dünya geneli — Türkiye kutusu yerel disk, dışı FIRMS Area API)."
                )
            df, _, meta = self._load_area(
                polygon=polygon, bbox=store_bbox, history_years=REMOTE_DEFAULT_YEARS,
            )
            return df, "spliced", meta
        if region not in self.regions:
            raise ValueError(f"unknown region '{region}'")
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
                   days: int = 1, bbox=None, polygon=None) -> dict:
        df, rule, meta = self._dataset(source, region, days, bbox, polygon=polygon)
        if df.empty:
            return {"h3": cell, "empty": True}
        c = geo.h3_int(cell)
        m = np.array((df["h3_7"] == c).to_numpy(), copy=True)
        if source == "benchmark":
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
        df, rule, meta = self._dataset(source, region, days, bbox, polygon=polygon)
        if df.empty:
            return clean({"source": source, "region": region, "n_cells_in_roi": len(cells),
                          "roi_area_km2": len(cells) * geo.cell_area_km2(7), "start": start, "end": end,
                          "empty": True, "meta": meta})
        m = np.array(df["h3_7"].isin(cells).to_numpy(), copy=True)
        if source == "benchmark":
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
                 polygon=None, days: int = 1, bbox=None, audience: str = "acil", lang: str = "tr") -> dict:
        from src.analytics.briefing_generator import generate
        if source == "benchmark":
            region = region or "california"
            end = end or str(LAST_DAY.date())
            start = start or str((pd.Timestamp(end) - pd.Timedelta(days=13)).date())
        df, rule, meta = self._dataset(source, region, days, bbox, polygon=polygon)
        m = np.ones(len(df), dtype=bool)
        # Arşiv sorgusu zaten poligonla süzüldü; tekrar H3 kesişimi gereksiz ama zararsız.
        if polygon and source != "archive":
            m = m & df["h3_7"].isin(geo.cells_in_polygon(polygon, geo.RES_REGIONAL)).to_numpy()
        if source == "benchmark":
            m = m & self._time_slice(df, start, end)
        sub = df[m] if len(df) else df
        if sub.empty:
            return {"empty": True, "markdown": "# Seçilen kapsamda tespit yok.", "json": {}, "meta": meta}
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
            quality = {"basis": f"FIRMS canlı besleme ({tiers})", "synthetic": False,
                       "non_live": not meta.get("live", False), "historical": False,
                       "notes": [meta["warning"]] if meta.get("warning") else []}
            scope = "FIRMS canlı" + (" — ROI poligon" if polygon else " — güncel kapsama")
            window = f"son {days} gün"
        elif source == "archive":
            dr = meta.get("date_range") or [None, None]
            tier = meta.get("data_tier") or "local-disk-archive"
            remote = tier == "remote-firms-api"
            quality = {
                "basis": (
                    "NASA FIRMS Area API (on-demand, year-capped)"
                    if remote
                    else "NASA FIRMS tarihsel arşivi (yerel Türkiye disk)"
                ),
                "synthetic": False,
                "non_live": False,
                "historical": True,
                "data_tier": tier,
                "notes": [
                    meta.get("note"),
                    meta.get("model_note")
                    or (
                        "Sayılar bu ROI’nin gerçek NASA FIRMS verisinden. "
                        "Harmonize sentetik benchmark’ta eğitildi; VIIRS referanstır, yer-gerçeği değil."
                    ),
                    "Sıcak nokta ≠ yangın sayısı ≠ yanmış alan; FRP ≠ yanmış alan. Brifing = ön tarama, talimat değil.",
                    f"Dilimler: {meta.get('done_chunks', '?')}/{meta.get('total_chunks', '?')}"
                    + (" (kısmi/önbellek)" if meta.get("partial") else ""),
                ],
            }
            quality["notes"] = [n for n in quality["notes"] if n]
            scope = (
                ("FIRMS uzaktan API" if remote else "FIRMS yerel arşiv")
                + (" — ROI poligon" if polygon else "")
            )
            window = f"{dr[0]} → {dr[1]}" if dr[0] else "mevcut arşiv aralığı"
            val = meta.get("local_validation") or val
        else:
            quality = {"basis": "Simüle 2001–2026 benchmark arşivi (bilinen yer gerçeği)", "synthetic": True,
                       "non_live": False, "notes": []}
            scope = f"{REGIONS[region].name}" + (" — ROI poligon" if polygon else " — tüm bölge")
            window = f"{start} → {end}"
        ctx = {"scope": scope, "window": window, "totals": totals, "top_cells": top, "climate": climate,
               "drivers": self._explain_summary(sub), "quality": quality, "validation": val,
               "centre": [float(sub["latitude"].mean()), float(sub["longitude"].mean())],
               "audience": audience or "acil", "lang": lang or "tr"}
        return clean(generate(ctx))

    def export_hotspots_csv(self, *, source: str, polygon: list[list[float]], days: int = 1) -> tuple[str, dict]:
        """ROI içindeki ham FIRMS sıcak noktalarını CSV metni + meta olarak döner."""
        import io

        if not polygon or len(polygon) < 3:
            raise ValueError("polygon gerekli (≥ 3 köşe)")
        if source == "archive":
            df, _, meta = self._load_area(polygon=polygon, history_years=REMOTE_DEFAULT_YEARS)
            if df.empty:
                return "latitude,longitude,acq_date,acq_time,satellite,instrument,frp,brightness,confidence\n", {
                    "n": 0, "source": "archive", "truncated": False, "meta": meta,
                }
        elif source == "live":
            df, meta = self.get_live(days=days)
            if df.empty:
                return "latitude,longitude,acq_date,acq_time,satellite,instrument,frp,brightness,confidence\n", {
                    "n": 0, "source": "live", "truncated": False, "meta": meta,
                }
            from src.data.archive_store import point_in_ring
            # True polygon clip (not bbox-only) before export
            if "latitude" in df.columns and "longitude" in df.columns:
                inside = point_in_ring(
                    df["latitude"].to_numpy(dtype=float),
                    df["longitude"].to_numpy(dtype=float),
                    polygon,
                )
                df = df.loc[inside]
            else:
                lats = [p[0] for p in polygon]
                lons = [p[1] for p in polygon]
                df = df[
                    (df["latitude"] >= min(lats)) & (df["latitude"] <= max(lats))
                    & (df["longitude"] >= min(lons)) & (df["longitude"] <= max(lons))
                ]
        else:
            raise ValueError("source live veya archive olmalı")

        limit = 50_000
        truncated = len(df) > limit
        if truncated:
            df = df.head(limit)

        bright_col = "bright_t21" if "bright_t21" in df.columns else ("brightness" if "brightness" in df.columns else None)
        rows = {
            "latitude": df["latitude"].to_numpy() if "latitude" in df.columns else [],
            "longitude": df["longitude"].to_numpy() if "longitude" in df.columns else [],
        }
        if "ts" in df.columns:
            ts = pd.to_datetime(df["ts"], errors="coerce")
            rows["acq_date"] = ts.dt.strftime("%Y-%m-%d").to_numpy()
            rows["acq_time"] = (ts.dt.hour * 100 + ts.dt.minute).fillna(0).astype(int).to_numpy()
        else:
            rows["acq_date"] = df["acq_date"].to_numpy() if "acq_date" in df.columns else [""] * len(df)
            rows["acq_time"] = df["acq_time"].to_numpy() if "acq_time" in df.columns else [0] * len(df)
        rows["satellite"] = df["satellite"].astype(str).to_numpy() if "satellite" in df.columns else [""] * len(df)
        rows["instrument"] = df["instrument"].astype(str).to_numpy() if "instrument" in df.columns else [""] * len(df)
        rows["frp"] = pd.to_numeric(df["frp"], errors="coerce").to_numpy() if "frp" in df.columns else [None] * len(df)
        rows["brightness"] = (
            pd.to_numeric(df[bright_col], errors="coerce").to_numpy() if bright_col else [None] * len(df)
        )
        rows["confidence"] = df["confidence"].to_numpy() if "confidence" in df.columns else [None] * len(df)
        for col, key in (
            ("firms_source", "firms_source"),
            ("product_kind", "product_kind"),
            ("product_version", "product_version"),
            ("daynight", "daynight"),
        ):
            if col in df.columns:
                rows[key] = df[col].astype(str).to_numpy()
        rows["row_meaning"] = np.full(len(df), "FIRMS_hotspot_detection")

        out = pd.DataFrame(rows)
        # CSV header comment block (first lines) for download clarity
        header_note = (
            "# Each row is one FIRMS hotspot detection (not fire count / burned area).\n"
            "# Times are UTC acquisition. product_kind: standard|nrt|unknown.\n"
            f"# source={source} n={len(out)} truncated={truncated} limit={limit}\n"
        )
        buf = io.StringIO()
        buf.write(header_note)
        out.to_csv(buf, index=False)
        info = {
            "n": int(len(out)),
            "source": source,
            "truncated": truncated,
            "limit": limit,
            "area_km2": (meta or {}).get("area_km2") if source == "archive" else None,
            "row_meaning": "FIRMS_hotspot_detection",
            "times_utc": True,
            "columns_help": {
                "latitude": "Detection pixel center latitude (not exact fire location)",
                "longitude": "Detection pixel center longitude",
                "acq_date": "Acquisition date UTC",
                "acq_time": "Acquisition time UTC HHMM",
                "frp": "Fire Radiative Power (MW) at detection — not burned area",
                "firms_source": "FIRMS product id (e.g. MODIS_SP, VIIRS_NOAA20_NRT)",
                "product_kind": "standard | nrt | unknown",
            },
            "attribution": "NASA FIRMS data used by an independent prototype — not a NASA-endorsed product",
            "meta": meta,
        }
        return buf.getvalue(), info

    # ------------------------------------------------------------------
    # gerçek FIRMS — yerel TR arşivi veya küresel on-demand Area API
    # ------------------------------------------------------------------
    def archive_status(self) -> dict:
        st = self.archive.status()
        st["remote_api_configured"] = bool(self.remote.configured())
        st["remote_max_years"] = REMOTE_MAX_YEARS
        st["global_roi"] = True
        st["local_coverage_note"] = (
            "Dashed box = local Turkey disk archive (fast, full depth). "
            "ROI anywhere else uses on-demand FIRMS Area API (MAP_KEY, cached, year-capped)."
        )
        return clean(st)

    def _prepare_archive_detections(self, raw: pd.DataFrame) -> pd.DataFrame:
        """Standartlaştırılmış arşiv satırlarını prepare + static + harmonize zincirinden geçir.

        ``firms_source`` / ``product_kind`` korunur (provenance). Platform normalizasyonu
        zamanla değişen ``n_operating`` ile yapılır; yeni uydu eklenince ani sıçrama olmaz.
        """
        meta_cols = [c for c in ("firms_source", "product_kind", "product_version", "daynight_raw", "nasa_type")
                     if c in raw.columns]
        base = raw.drop(columns=[c for c in meta_cols if c in raw.columns], errors="ignore")
        df = prepare(base.copy())
        for c in meta_cols:
            df[c] = raw[c].to_numpy()
        df = tag_static(df, self.catalog_cells, use_seeds=True, provisional=True)
        if "nasa_type" in df.columns:
            m = (df["nasa_type"] == 2) & ~df["is_static"]
            df.loc[m, "is_static"] = True
            df.loc[m, "static_reason"] = "nasa-type-2"
        nm = n_operating(df["ts"], "MODIS")
        nv = n_operating(df["ts"], "VIIRS")
        return pl.attach_harmonized(df, self.model, n_ops_modis=nm, n_ops_viirs=nv)

    @staticmethod
    def _area_cache_key(polygon, bbox, force: bool, history_years: int | None, tier: str) -> tuple:
        poly = tuple((round(float(a), 5), round(float(b), 5)) for a, b in (polygon or []))
        box = None if bbox is None else tuple(round(float(x), 5) for x in bbox)
        return (poly, box, bool(force), history_years, tier)

    def _resolve_query_bbox(self, polygon, store_bbox) -> tuple[float, float, float, float]:
        if polygon is not None:
            return polygon_bbox(polygon)
        assert store_bbox is not None
        return store_bbox

    def _load_area(
        self,
        *,
        polygon=None,
        bbox=None,
        force: bool = False,
        history_years: int | None = None,
    ) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
        """Seçilen alan: yerel TR arşivi veya küresel FIRMS Area API → prepare/harmonize."""
        store_bbox = None
        if bbox is not None:
            if len(bbox) != 4:
                raise ValueError("bbox must be [west, south, east, north]")
            store_bbox = (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
        if polygon is None and store_bbox is None:
            raise ValueError("polygon veya bbox gerekli")

        west, south, east, north = self._resolve_query_bbox(polygon, store_bbox)
        # Area limit always applies (local or remote)
        self.archive.assert_area_limit(west, south, east, north)

        use_local = self.archive.contains_bbox(west, south, east, north)
        hy = None if use_local else max(1, min(int(history_years or REMOTE_DEFAULT_YEARS), REMOTE_MAX_YEARS))
        tier = "local" if use_local else "remote"

        key = self._area_cache_key(polygon, store_bbox, force, hy, tier)
        arch_key = (
            int(self.archive.status().get("done_chunks") or 0),
            self.archive.status().get("state"),
            tier,
            hy,
        )
        hit = self._area_cache
        if hit and not force and hit.get("key") == key and hit.get("arch_key") == arch_key:
            return hit["df"], hit["daily"], hit["meta"]

        if use_local:
            raw, meta = self.archive.query(bbox=store_bbox, polygon=polygon, force=force)
            meta = {**meta, "data_tier": "local-disk-archive", "local_archive": True}
        else:
            if not self.remote.configured():
                raise ValueError(
                    "Bu ROI yerel Türkiye arşivi dışında. Küresel ROI için backend/.env içinde "
                    "FIRMS_MAP_KEY gerekli (veya kesik kutu içine daha küçük bir poligon çiz)."
                )
            req_hy = int(history_years or REMOTE_DEFAULT_YEARS)
            raw, meta = self.remote.fetch(
                west=west, south=south, east=east, north=north,
                years=hy or REMOTE_DEFAULT_YEARS,
                polygon=polygon,
                requested_years=req_hy,
            )

        if raw.empty:
            empty = pd.DataFrame()
            self._area_cache = {"key": key, "arch_key": arch_key, "df": empty, "daily": empty, "meta": meta}
            return empty, empty, meta

        df = self._prepare_archive_detections(raw)
        start = str(df["ts"].min().date())
        end = str(df["ts"].max().date())
        daily = pl.daily_series(df, start=start, end=end)
        meta = {
            **meta,
            "model": self.model.version,
            "model_note": (
                "Numbers from real NASA FIRMS for this ROI "
                f"({'local Turkey disk archive' if use_local else 'on-demand Area API'}). "
                "Harmonized (VIIRS-equivalent) trained on synthetic benchmark; "
                "VIIRS is a comparison reference. Prefer Raw if local RMSE worsens."
            ),
            "detections": int(len(df)),
            "raw_count": int(len(df)),
            "date_range": [start, end],
            "counts": {
                "raw_all": int(daily["raw_all"].sum()),
                "raw_modis": int(daily["raw_modis"].sum()),
                "raw_viirs": int(daily["raw_viirs"].sum()),
                "static_removed": int(daily["static_removed"].sum()),
            },
            "intensity": self._frp_intensity(df),
            "timeline_markers": TIMELINE_MARKERS,
            "empty_reason": None,
            "evaluation_note": (
                "Harmonization trained on synthetic benchmark. "
                "Local metrics are monthly MODIS→VIIRS reference comparison on real FIRMS — "
                "not event-level independent validation."
            ),
        }
        self._area_cache = {"key": key, "arch_key": arch_key, "df": df, "daily": daily, "meta": meta}
        return df, daily, meta

    @staticmethod
    def _frp_intensity(df: pd.DataFrame, start=None, end=None) -> dict:
        """FIRMS tespit sayısı + FRP (MW) şiddet özeti. start/end dahil gün aralığı."""
        if df is None or getattr(df, "empty", True) or "frp" not in getattr(df, "columns", []):
            return {}
        sub = df
        if start is not None or end is not None:
            ts = pd.to_datetime(sub["ts"])
            if start is not None:
                sub = sub.loc[ts >= pd.Timestamp(start)]
                ts = pd.to_datetime(sub["ts"])
            if end is not None:
                sub = sub.loc[ts < pd.Timestamp(end) + pd.Timedelta(days=1)]
        if sub.empty:
            return {"n_tespit": 0, "frp_toplam_mw": 0.0}
        frp = pd.to_numeric(sub["frp"], errors="coerce").dropna()
        if frp.empty:
            return {"n_tespit": int(len(sub)), "frp_toplam_mw": 0.0}
        out: dict = {
            "n_tespit": int(len(sub)),
            "frp_toplam_mw": round(float(frp.sum()), 1),
            "frp_ort_mw": round(float(frp.mean()), 2),
            "frp_medyan_mw": round(float(frp.median()), 2),
            "frp_p90_mw": round(float(frp.quantile(0.9)), 2),
            "frp_max_mw": round(float(frp.max()), 2),
        }
        if "instrument" in sub.columns:
            inst = sub["instrument"].astype(str).str.upper()
            for label, key in (("MODIS", "modis"), ("VIIRS", "viirs")):
                m = inst.eq(label)
                g = pd.to_numeric(sub.loc[m, "frp"], errors="coerce").dropna()
                out[f"{key}_n"] = int(m.sum())
                out[f"{key}_frp_toplam_mw"] = round(float(g.sum()), 1) if len(g) else 0.0
                out[f"{key}_frp_ort_mw"] = round(float(g.mean()), 2) if len(g) else None
        return out

    def _area_local_validation(
        self,
        daily: pd.DataFrame,
        meta: dict | None = None,
        df: pd.DataFrame | None = None,
    ) -> dict:
        """Monthly MODIS→VIIRS comparison on the real archive (policy A main path).

        VIIRS is a **comparison reference**, not independent ground truth.
        Covered zero-detection months stay in the sample; months without product
        coverage are excluded (not zero-filled).  Platform-scale baseline is
        learned on a calibration window and scored on later test months.

        Confidence policies B/C appear only under ``confidence_sensitivity`` —
        they never replace the production filter (policy A).
        """
        from src.analytics.reference_comparison import (
            compare_to_viirs_reference,
            product_coverage_table,
            select_evaluation_months,
        )

        if daily is None or daily.empty:
            return {"n": 0, "label": "real_archive_reference_comparison", "improved": None}
        monthly = daily.resample("MS").sum(min_count=1)
        post = monthly.index >= VSTART
        if not post.any():
            return {"n": 0, "label": "real_archive_reference_comparison",
                    "note": "No post-VIIRS months in this ROI."}

        products = (meta or {}).get("archive_products")
        # Only use the local TR product calendar for local-disk archives (never for remote ROI).
        if products is None and (meta or {}).get("data_tier") == "local-disk-archive":
            try:
                from src.data.archive_store import _area_json
                products = _area_json(self.archive.root).get("products")
            except Exception:  # noqa: BLE001
                products = None

        coverage = product_coverage_table(products or {}, monthly.index) if products else None
        # Prefer completed Standard overlap when a product calendar is available
        month_info = select_evaluation_months(
            monthly, coverage,
            require_both_sensors=True,
            standard_only=bool(products),
            post_viirs_only=True,
        )
        mask = month_info["mask"]
        if not mask.any():
            out = {
                "n": 0, "label": "real_archive_reference_comparison",
                "note": "No overlapping MODIS/VIIRS months with product coverage.",
                "n_excluded_no_coverage": month_info.get("n_excluded_no_coverage"),
                "reference_name": "VIIRS comparison reference (not ground truth)",
                "confidence_policy_primary": PRIMARY_POLICY,
            }
            if df is not None and not getattr(df, "empty", True):
                out["confidence_sensitivity"] = confidence_filter_sensitivity(df)
            return out

        months = monthly.index[mask]
        ref = monthly.loc[months, "viirs_native"].to_numpy(dtype=float)
        raw_m = monthly.loc[months, "modis_filtered"].to_numpy(dtype=float)
        harm_m = monthly.loc[months, "harm_modis_only"].to_numpy(dtype=float)
        metrics = compare_to_viirs_reference(ref, raw_m, harm_m, months=months)
        metrics["improved"] = bool(metrics.get("rmse_reduction_pct", 0) > 0)
        metrics["recommend_raw"] = not metrics["improved"]
        metrics["label"] = "real_archive_reference_comparison"
        metrics["reference_name"] = "VIIRS comparison reference (not ground truth)"
        metrics["evaluation_kind"] = "real_firms_archive"
        metrics["training_kind"] = "synthetic_benchmark"
        metrics["n_months"] = int(metrics.get("n", 0))
        metrics["n_months_locked"] = int(len(months))
        metrics["n_excluded_no_coverage"] = month_info.get("n_excluded_no_coverage")
        metrics["n_zero_detection_kept"] = month_info.get("n_zero_detection_kept")
        metrics["product_kinds_in_sample"] = month_info.get("product_kinds")
        metrics["month_selection_note"] = month_info.get("note")
        metrics["confidence_policy_primary"] = PRIMARY_POLICY
        metrics["confidence_policy_note"] = (
            "Main metrics use policy A (MODIS ≥30; VIIRS nominal/high). "
            "confidence_sensitivity compares A/B/C by MODIS→VIIRS closeness; "
            "the closest policy is diagnostic only and is not auto-selected."
        )
        if df is not None and not getattr(df, "empty", True):
            metrics["confidence_sensitivity"] = confidence_filter_sensitivity(df)
        if meta:
            metrics["roi_area_km2"] = meta.get("area_km2")
            metrics["date_range"] = meta.get("date_range")
            metrics["product_composition"] = meta.get("product_composition")
            metrics["product_kind_counts"] = meta.get("product_kind_counts")
            metrics["satellites"] = meta.get("satellites")
            metrics["counts"] = meta.get("counts")
        if metrics.get("n", 0) < 6:
            metrics["ci_note"] = "unstable — few months; confidence interval not reliable"
        return metrics

    def area_calendar(
        self,
        *,
        polygon: list[list[float]] | None = None,
        bbox: list[float] | None = None,
        series: str = "harmonized",
        target_year: int | None = None,
        start_year: int | None = None,
        end_year: int | None = None,
        month: int | None = None,
        force: bool = False,
    ) -> dict:
        """Seçilen alan için gerçek FIRMS ham + harmonize yanma takvimi."""
        df, daily, meta = self._load_area(
            polygon=polygon, bbox=bbox, force=force, history_years=REMOTE_DEFAULT_YEARS,
        )
        if df.empty:
            return clean({
                "empty": True, "meta": meta, "series": series,
                "message": {
                    "archive_not_ready": "Arşiv henüz hazır değil / indirilmedi.",
                    "archive_partial_no_rows": "Arşiv kısmi; bu ROI’de henüz satır yok.",
                    "no_detections": "Veri kapsamı içinde tespit bulunamadı (sıfır aktivite değil — tespit yok).",
                }.get(meta.get("empty_reason") or "", "Seçilen alanda arşivde tespit yok."),
                "empty_reason": meta.get("empty_reason"),
            })
        start, end = meta["date_range"]
        data_sy = int(pd.Timestamp(start).year)
        data_ey = int(pd.Timestamp(end).year)
        sy = int(start_year) if start_year is not None else data_sy
        ey = int(end_year) if end_year is not None else data_ey
        sy = max(data_sy, min(sy, data_ey))
        ey = max(sy, min(ey, data_ey))
        ty = int(target_year) if target_year is not None else ey
        ty = max(sy, min(ty, ey))
        col = "harmonized" if series == "harmonized" else "raw_all"
        series_daily = daily[col].astype(float)
        # Görüntülenen aralık dışı yılları kes (takvim matrisi daralsın)
        series_daily = series_daily[
            (series_daily.index.year >= sy) & (series_daily.index.year <= ey)
        ]
        cal = build_calendar(series_daily, target_year=ty, start_year=sy)
        cal["region"] = "archive:tr"
        cal["series"] = series
        return clean({
            "empty": False,
            "meta": {
                **meta,
                "view_years": [sy, ey],
                "target_year": ty,
            },
            "calendar": cal,
            "month_year": month_year_matrix(series_daily, start_year=sy, end_year=ty),
            "current_month_vs_median": current_month_vs_median(
                daily[col].astype(float), year=ty, month=month,
            ),
            "local_validation": self._area_local_validation(daily, meta, df=df),
            "series": series,
        })

    def area_series(
        self,
        *,
        polygon: list[list[float]] | None = None,
        bbox: list[float] | None = None,
        freq: str = "monthly",
        force: bool = False,
    ) -> dict:
        """Seçilen alan için ham / harmonize zaman serisi (yapısal kırılma grafiği)."""
        df, daily, meta = self._load_area(
            polygon=polygon, bbox=bbox, force=force, history_years=REMOTE_DEFAULT_YEARS,
        )
        if df.empty:
            return clean({"empty": True, "meta": meta, "message": "Seçilen alanda tespit yok."})
        step = "MS" if freq == "monthly" else "YS"
        m = daily.resample(step).sum(min_count=1)
        if freq == "monthly" and len(m):
            # son tamamlanmamış ayı düş (tz-naive; günlük seri UTC-naive)
            today = pd.Timestamp.utcnow().tz_localize(None).normalize()
            last_complete = (today.replace(day=1) - pd.Timedelta(days=1)).replace(day=1)
            m = m.loc[:last_complete]
        est = m["harmonized"]
        sdv = np.sqrt(m["harmonized_var"]) * self.kappa
        # Gerçek arşivde karşı-olgusal yok: 2012 sonrası VIIRS native referans.
        # Pre-VIIRS: NaN (not 0) — zero would falsely imply "no fire activity".
        truth = m["viirs_native"].where(m.index >= VSTART)
        fmt = "%Y-%m" if freq == "monthly" else "%Y"
        last_partial = False
        if freq == "annual" and len(m):
            last_partial = True
        return clean({
            "empty": False,
            "region": "archive:tr",
            "freq": freq,
            "labels": [t.strftime(fmt) for t in m.index],
            "raw_all": m["raw_all"],
            "raw_filtered": m["raw_filtered"],
            "raw_modis": m["raw_modis"],
            "raw_viirs": m["raw_viirs"],
            "harmonized": est,
            "harmonized_lo": (est - Z90 * sdv).clip(lower=0),
            "harmonized_hi": est + Z90 * sdv,
            "truth_snpp_everywhere": truth,
            "viirs_reference_available": [bool(t >= VSTART) for t in m.index],
            "viirs_reference_label": "VIIRS comparison reference (absent before 2012-01-20)",
            "static_removed": m["static_removed"],
            "transition": "2012-01-20",
            "timeline_markers": TIMELINE_MARKERS,
            "partial_last_period": last_partial or freq == "annual",
            "uncertainty_note": "P10/P50/P90 and 90% band are model prediction intervals, not fire-count confidence.",
            "meta": meta,
            "local_validation": self._area_local_validation(daily, meta, df=df),
        })

    def area_density(
        self,
        *,
        polygon: list[list[float]] | None = None,
        bbox: list[float] | None = None,
        years: int = 5,
        instrument: str = "all",
        force: bool = False,
    ) -> dict:
        """ROI içinde son ``years`` yılın FIRMS hotspot tespit yoğunluğu (H3-7 hücreleri).

        Renk = hücre başına tespit sayısı (polygon fill). Yangın olayı veya yanmış alan değildir.
        """
        requested_years = max(1, min(int(years), 25))
        inst = (instrument or "all").upper()
        if inst not in ("ALL", "MODIS", "VIIRS"):
            raise ValueError("instrument all | MODIS | VIIRS olmalı")

        # Pass the requested years; _load_area / remote clamp to REMOTE_MAX_YEARS when needed.
        df, _, meta = self._load_area(
            polygon=polygon, bbox=bbox, force=force,
            history_years=requested_years,
        )
        local = bool(meta.get("local_archive"))
        if local:
            effective_years = requested_years
        else:
            effective_years = int(meta.get("effective_years") or meta.get("history_years") or min(
                requested_years, REMOTE_MAX_YEARS,
            ))
            effective_years = max(1, min(effective_years, REMOTE_MAX_YEARS))

        year_note = None
        if not local and requested_years > REMOTE_MAX_YEARS:
            year_note = (
                f"Requested {requested_years}y; remote ROI capped at {REMOTE_MAX_YEARS}y "
                f"(effective {effective_years}y)."
            )

        def _empty_payload(message: str, win=None) -> dict:
            start = win[0] if win else None
            end = win[1] if win else None
            return clean({
                "empty": True,
                "meta": meta,
                "years": effective_years,
                "requested_years": requested_years,
                "effective_years": effective_years,
                "window_start": start,
                "window_end": end,
                "instrument": inst if inst != "ALL" else "all",
                "message": message,
                "year_note": year_note,
                "render": "h3_polygon",
                "points": [],
                "legend": self._density_legend(
                    0, 0, effective_years, start, end,
                    remote=not local, requested_years=requested_years,
                ),
            })

        if df.empty:
            return _empty_payload({
                "archive_not_ready": "Yerel arşiv henüz hazır değil.",
                "no_detections": "Seçilen alanda tespit yok.",
                "no_product_coverage": "Bu tarih penceresinde FIRMS ürünü yok.",
            }.get(meta.get("empty_reason") or "", meta.get("note") or "Seçilen alanda veri yok."))

        ts = pd.to_datetime(df["ts"])
        data_end = ts.max()
        data_start = ts.min()
        # Rolling window uses effective (capped) years — not the possibly-over-cap request.
        win_start = data_end - pd.DateOffset(years=effective_years)
        mask = ts >= win_start
        sub = df.loc[mask].copy()
        if inst in ("MODIS", "VIIRS"):
            sub = sub.loc[sub["instrument"].astype(str).str.upper() == inst]

        n_raw = int(len(sub))
        if "conf_ok" in sub.columns and "is_static" in sub.columns:
            ok = sub["conf_ok"].to_numpy() & ~sub["is_static"].to_numpy()
            excluded = int((~ok).sum())
            sub = sub.loc[ok]
        else:
            excluded = 0

        win = [str(win_start.date()), str(data_end.date())]
        if sub.empty:
            out = _empty_payload(
                "Pencerede (güven/statik süzgeç sonrası) tespit yok.",
                win=win,
            )
            out["n_raw_in_window"] = n_raw
            out["excluded_lowconf_or_static"] = excluded
            out["meta"] = {**meta, "window": win, "times_utc": True}
            return out

        if "h3_7" not in sub.columns:
            sub = geo.add_h3_columns(sub)

        g = sub.groupby("h3_7", observed=True)
        counts = g.size().rename("n")
        frp = g["frp"].sum().rename("frp_sum") if "frp" in sub.columns else None
        # Per-cell instrument mix for click popups (not fire-event counts).
        if "instrument" in sub.columns:
            inst_tab = (
                sub.groupby(["h3_7", sub["instrument"].astype(str).str.upper()], observed=True)
                .size()
                .unstack(fill_value=0)
            )
        else:
            inst_tab = None
        if "satellite" in sub.columns:
            sat_tab = (
                sub.groupby(["h3_7", sub["satellite"].astype(str)], observed=True)
                .size()
                .unstack(fill_value=0)
            )
        else:
            sat_tab = None

        tab = counts.to_frame()
        if frp is not None:
            tab["frp_sum"] = frp
        else:
            tab["frp_sum"] = 0.0
        tab = tab.reset_index()
        n_max = int(tab["n"].max()) if len(tab) else 0
        n_total = int(tab["n"].sum())

        points = []
        for row in tab.itertuples(index=False):
            cell = int(row.h3_7)
            lat, lon = geo.cell_center(cell)
            n = int(row.n)
            # Relative weight for optional kernel fallback; primary render is polygon fill by n.
            w = float(np.sqrt(n / max(n_max, 1)))
            platforms = {}
            if sat_tab is not None and cell in sat_tab.index:
                platforms = {str(k): int(v) for k, v in sat_tab.loc[cell].items() if int(v) > 0}
            instruments = {}
            if inst_tab is not None and cell in inst_tab.index:
                instruments = {str(k): int(v) for k, v in inst_tab.loc[cell].items() if int(v) > 0}
            points.append({
                "lat": round(float(lat), 5),
                "lon": round(float(lon), 5),
                "n": n,
                "frp_sum": round(float(row.frp_sum), 1),
                "w": round(w, 4),
                "h3": geo.h3_str(cell),
                "boundary": _boundary(cell),
                "platforms": platforms,
                "instruments": instruments,
            })

        points.sort(key=lambda p: p["n"], reverse=True)

        legend = self._density_legend(
            n_max, n_total, effective_years, win[0], win[1],
            remote=not local, requested_years=requested_years,
        )
        return clean({
            "empty": False,
            "years": effective_years,
            "requested_years": requested_years,
            "effective_years": effective_years,
            "window_start": win[0],
            "window_end": win[1],
            "instrument": inst if inst != "ALL" else "all",
            "window": win,
            "archive_span": [str(data_start.date()), str(data_end.date())],
            "n_detections": n_total,
            "n_raw_in_window": n_raw,
            "excluded_lowconf_or_static": excluded,
            "n_cells": len(points),
            "n_max_per_cell": n_max,
            "cell_area_km2_approx": round(geo.cell_area_km2(7), 2),
            "aggregation": (
                "H3 res-7 polygons (≈5.2 km²); fill color = detections per cell. "
                "Empty cells omitted (not shown as zero fires)."
            ),
            "render": "h3_polygon",
            "meaning": (
                "FIRMS hotspot detection density over the selected window — "
                "not fire-event count and not burned area."
            ),
            "year_note": year_note,
            "points": points,
            "legend": legend,
            "meta": {
                **meta,
                "window": win,
                "window_start": win[0],
                "window_end": win[1],
                "requested_years": requested_years,
                "effective_years": effective_years,
                "times_utc": True,
            },
        })

    @staticmethod
    def _density_legend(
        n_max: int,
        n_total: int,
        years: int,
        start: str | None,
        end: str | None,
        *,
        remote: bool = False,
        requested_years: int | None = None,
    ) -> dict:
        """Legend for H3 polygon density (exact per-cell detection counts)."""
        bins = []
        if n_max <= 0:
            bins = [
                {
                    "level": "none",
                    "min_n": 0,
                    "max_n": 0,
                    "label_tr": "Veri yok (hücre çizilmez)",
                    "label_en": "No data (cell not drawn)",
                    "approx_n_per_cell": 0,
                },
            ]
        else:
            # Discrete count bins mapped to the UI color ramp stops
            edges = [1]
            for frac in (0.12, 0.35, 0.55, 0.78, 1.0):
                edge = max(1, int(round((frac ** 2) * n_max)))
                if edge > edges[-1]:
                    edges.append(edge)
            if edges[-1] < n_max:
                edges.append(n_max)
            labels = [
                ("çok düşük", "very low"),
                ("düşük", "low"),
                ("orta", "medium"),
                ("yüksek", "high"),
                ("en yüksek", "highest"),
            ]
            for i in range(len(edges) - 1):
                lo, hi = edges[i], edges[i + 1]
                tr, en = labels[min(i, len(labels) - 1)]
                bins.append({
                    "level": en.replace(" ", "_"),
                    "min_n": lo,
                    "max_n": hi,
                    "weight": round((hi / max(n_max, 1)) ** 0.5, 4),
                    "approx_n_per_cell": hi,
                    "label_tr": f"{tr} ({lo}–{hi} tespit / hücre)",
                    "label_en": f"{en} ({lo}–{hi} detections / cell)",
                })
        title_suffix = ""
        if remote and requested_years and requested_years > years:
            title_suffix = f" (remote cap; asked {requested_years}y)"
            title_suffix_tr = f" (uzaktan üst sınır; istenen {requested_years}y)"
        else:
            title_suffix_tr = ""
        return {
            "title_tr": f"Son {years} yıl — FIRMS sıcak nokta tespit yoğunluğu{title_suffix_tr}",
            "title_en": f"Last {years} years — FIRMS hotspot detection density{title_suffix}",
            "unit_tr": "H3 hücre başına tespit sayısı (polygon dolgu; tam sayı)",
            "unit_en": "Detections per H3 cell (polygon fill; exact counts)",
            "not_tr": (
                "Yangın olayı sayısı veya yanmış alan değildir. Aynı olay tekrar sayılabilir. "
                "Boş hücre = veri yok, sıfır yangın iddiası değil."
            ),
            "not_en": (
                "Not fire-event count or burned area. The same event can be re-counted. "
                "Missing cells mean no detections drawn — not a claim of zero fires."
            ),
            "window": [start, end],
            "n_max_per_cell": n_max,
            "n_total": n_total,
            "render": "h3_polygon",
            "bins": bins,
        }

    def area_break(
        self,
        *,
        polygon: list[list[float]] | None = None,
        bbox: list[float] | None = None,
        force: bool = False,
    ) -> dict:
        """Seçilen alanda 2012 sensör geçişi yapısal kırılma testleri."""
        df, daily, meta = self._load_area(
            polygon=polygon, bbox=bbox, force=force, history_years=REMOTE_DEFAULT_YEARS,
        )
        if df.empty:
            return clean({"empty": True, "meta": meta, "message": "Seçilen alanda tespit yok."})
        m = daily.resample("MS").sum(min_count=1)
        if len(m):
            today = pd.Timestamp.utcnow().tz_localize(None).normalize()
            last_complete = (today.replace(day=1) - pd.Timedelta(days=1)).replace(day=1)
            m = m.loc[:last_complete]
        # Pre-VIIRS reference is unavailable — keep NaN (do not fill 0).
        truth = m["viirs_native"].where(m.index >= VSTART)
        series = {
            "raw_all": m["raw_all"],
            "raw_filtered": m["raw_filtered"],
            "harmonized": m["harmonized"],
            "truth": truth,
        }
        reports = {}
        for k, v in series.items():
            # Drop NaN months for tests; flag pre-VIIRS gap on truth series
            vv = v.dropna() if k == "truth" else v
            reports[k] = sd.structural_break_report(vv) if len(vv) >= 8 else {
                "note": "insufficient data", "pettitt_monthly": {"significant": False, "p_value": None},
            }
            if k == "truth":
                reports[k]["pre_viirs_note"] = (
                    "VIIRS comparison reference absent before 2012-01-20 — "
                    "zeros are not shown as 'no activity'."
                )
        verdict = sd.break_verdict(reports)
        verdict["notes"] = list(verdict.get("notes") or []) + [
            "Kapsam: çizilen ROI içindeki gerçek NASA FIRMS arşivi (simüle benchmark değil).",
            f"Ham tespit: {meta.get('raw_count', 0):,} · alan ~{meta.get('area_km2', '?')} km².",
            "p<0.05 is not a definitive break (autocorrelation can inflate significance).",
            "VIIRS column = comparison reference, not ground truth.",
        ]
        verdict["pvalue_caveat"] = (
            "Pettitt/Chow assume independent samples; monthly fire series are autocorrelated. "
            "Read p-values with effect size (step %) — not as operational decisions."
        )
        return clean({
            "empty": False,
            "region": "archive:tr",
            "reports": reports,
            "verdict": verdict,
            "meta": meta,
            "timeline_markers": TIMELINE_MARKERS,
            "local_validation": self._area_local_validation(daily, meta, df=df),
        })

    # ------------------------------------------------------------------
    # DeepSeek AI bakışı — ROI sayı özeti
    # ------------------------------------------------------------------
    def interpret_roi(
        self,
        *,
        polygon: list[list[float]],
        focus: str = "break",
        section: str = "overview",
        question: str | None = None,
        lang: str = "tr",
    ) -> dict:
        """ROI için bölüm bazlı dayanak üretir ve DeepSeek ile dil-uyumlu yorum döner."""
        from src.analytics import deepseek_client as ds

        sec = (section or "overview").strip()[:64]
        en = (lang or "tr").strip().lower() == "en"
        if not polygon or len(polygon) < 3:
            return clean({"available": ds.configured(), "empty": True, "message": "Poligon gerekli (≥ 3 köşe)."})

        # Büyük ROI'de area_break + area_calendar birlikte çok ağır; briefing/overview
        # için takvim + meta yeterli. Kırılma sekmesi bölümleri break ister.
        need_break = focus == "break" or sec in (
            "chart", "stats", "verdict", "explain", "summary", "validation",
        ) or (focus == "model" and sec in ("summary", "validation", "overview"))
        need_cal = focus in ("calendar", "briefing") or sec in (
            "overview", "month_vs_median", "heatmap", "month_year", "envelope", "anomalies",
        )
        # model overview: break meta yeter; takvim şart değil
        if focus == "model" and sec in ("summary", "validation", "overview"):
            need_cal = False

        try:
            brk = self.area_break(polygon=polygon) if need_break else {"empty": True}
        except ValueError as exc:
            return clean({"available": ds.configured(), "empty": True, "message": str(exc)})
        if need_break and brk.get("empty") and focus not in ("calendar", "briefing"):
            return clean({
                "available": ds.configured(),
                "empty": True,
                "message": brk.get("message") or "Seçilen alanda tespit yok.",
            })

        try:
            cal = self.area_calendar(polygon=polygon, series="raw") if need_cal else {"empty": True}
        except ValueError as exc:
            return clean({"available": ds.configured(), "empty": True, "message": str(exc)})
        if focus in ("calendar", "briefing") and cal.get("empty") and not need_break:
            return clean({
                "available": ds.configured(),
                "empty": True,
                "message": cal.get("message") or "Seçilen alanda tespit yok.",
            })

        meta = (brk.get("meta") if not brk.get("empty") else None) or (cal.get("meta") if not cal.get("empty") else {}) or {}
        verdict = brk.get("verdict") or {}
        reports = brk.get("reports") or {}
        loc = brk.get("local_validation") or cal.get("local_validation") or {}
        vs = (cal.get("current_month_vs_median") or {}) if not cal.get("empty") else {}
        cal_body = (cal.get("calendar") or {}) if not cal.get("empty") else {}
        anom = cal_body.get("anomalies") or {}
        my = (cal.get("month_year") or {}) if not cal.get("empty") else {}

        # FRP şiddeti: meta'da yoksa (eski önbellek) ROI'den hesapla
        intensity = meta.get("intensity") if isinstance(meta.get("intensity"), dict) else None
        area_df = None
        if not intensity or sec in ("anomalies", "envelope"):
            try:
                area_df, _, area_meta = self._load_area(
                    polygon=polygon, history_years=REMOTE_DEFAULT_YEARS,
                )
                if not intensity:
                    intensity = area_meta.get("intensity") or self._frp_intensity(area_df)
            except Exception:
                area_df = None
                intensity = intensity or {}

        base_meta = {
            "bolum": sec,
            "sekme": focus,
            "alan_km2": meta.get("area_km2"),
            "tespit_sayisi": meta.get("raw_count"),
            "modis_tespit": (meta.get("counts") or {}).get("raw_modis"),
            "viirs_tespit": (meta.get("counts") or {}).get("raw_viirs"),
            "tarih": meta.get("date_range"),
            "siddet_frp": intensity or None,
            "birim": (
                "tespit_sayisi = FIRMS sıcak nokta (yangın tespit) adedi; "
                "frp_* = radyatif güç MW (şiddet göstergesi)."
            ),
        }

        def _report_row(name: str, r: dict) -> dict:
            return {
                "seri": name,
                "adim_pct": (r.get("step") or {}).get("step_pct"),
                "oncesi_ort": (r.get("step") or {}).get("pre_mean"),
                "sonrasi_ort": (r.get("step") or {}).get("post_mean"),
                "pettitt_yillik_p": (r.get("pettitt_annual") or {}).get("p_value"),
                "chow_yillik_p": (r.get("chow_annual") or {}).get("p_value"),
                "chow_aylik_p": (r.get("chow_monthly") or {}).get("p_value"),
                "pettitt_yerel_p": (r.get("pettitt_local") or {}).get("p_value"),
            }

        used: dict
        if sec in ("stats", "istatistik"):
            used = {
                **base_meta,
                "tablo": "istatistik_testleri",
                "satirlar": [_report_row(k, v) for k, v in reports.items() if isinstance(v, dict)],
                "not": "p<0.05 = istatistiksel kırılma; ham adım çoğu zaman sensör artefaktı olabilir.",
            }
        elif sec in ("verdict", "yorum"):
            used = {
                **base_meta,
                "tablo": "makine_yorumu",
                "ham_kirilma": verdict.get("raw_break_at_transition"),
                "harmonize_adim_kalkti": verdict.get("harmonized_step_eliminated"),
                "notlar": (verdict.get("notes") or [])[:6],
            }
        elif sec in ("chart", "grafik"):
            raw_rep = reports.get("raw_filtered") or reports.get("raw_all") or {}
            harm = reports.get("harmonized") or {}
            used = {
                **base_meta,
                "tablo": "kirilma_grafigi",
                "ham_adim_pct": (raw_rep.get("step") or {}).get("step_pct"),
                "ham_oncesi": (raw_rep.get("step") or {}).get("pre_mean"),
                "ham_sonrasi": (raw_rep.get("step") or {}).get("post_mean"),
                "harmonize_adim_pct": (harm.get("step") or {}).get("step_pct"),
                "ham_kirilma": verdict.get("raw_break_at_transition"),
                "harmonize_adim_kalkti": verdict.get("harmonized_step_eliminated"),
            }
        elif sec == "explain":
            used = {
                **base_meta,
                "tablo": "bu_ne_gosteriyor",
                "baglam": "VIIRS 2012-01-20; ham MODIS+VIIRS sıçrama yapay olabilir; harmonize kapatmaya çalışır.",
                "ham_kirilma": verdict.get("raw_break_at_transition"),
                "harmonize_adim_kalkti": verdict.get("harmonized_step_eliminated"),
            }
        elif sec == "month_vs_median":
            used = {
                **base_meta,
                "tablo": "bu_ay_vs_medyan",
                "oran": vs.get("ratio"),
                "yuzdelik": vs.get("percentile"),
                "ay_toplam": vs.get("month_total"),
                "tarihsel_medyan": vs.get("historical_median"),
                "yil": vs.get("year"),
                "ay": vs.get("month"),
                "n_yil": vs.get("n_years"),
                "p10": vs.get("historical_p10"),
                "p90": vs.get("historical_p90"),
            }
        elif sec in ("heatmap", "month_year"):
            years = my.get("years") or cal_body.get("years") or []
            totals = cal_body.get("annual_totals") or []
            peaks = cal_body.get("peak_doy") or []
            pairs = [
                {"yil": y, "yillik_toplam": totals[i] if i < len(totals) else None,
                 "tepe_gun": peaks[i] if i < len(peaks) else None}
                for i, y in enumerate(years[-8:])
            ]
            used = {
                **base_meta,
                "tablo": "yil_yogunluk_ozeti",
                "seri": cal.get("series") or cal_body.get("series") or "raw",
                "son_yillar": pairs,
                "not": "Tam ay×yıl matrisi gönderilmedi; yıllık özet.",
            }
        elif sec == "envelope":
            from src.analytics.burning_calendar import envelope_ai_summary
            ty = cal_body.get("target_year")
            hedef_siddet = None
            if area_df is not None and ty:
                hedef_siddet = self._frp_intensity(area_df, f"{ty}-01-01", f"{ty}-12-31")
            used = {
                **base_meta,
                "tablo": "iklimsel_zarf",
                **envelope_ai_summary(cal_body),
                "hedef_yil_siddet": hedef_siddet,
            }
        elif sec == "anomalies":
            eps = (anom.get("episodes") or [])[:8]
            donemler = []
            for e in eps:
                row = {
                    "bas": e.get("start"),
                    "bit": e.get("end"),
                    "gun": e.get("days"),
                    "tepe_z": e.get("peak_z"),
                }
                if area_df is not None and e.get("start") and e.get("end"):
                    row["siddet"] = self._frp_intensity(area_df, e["start"], e["end"])
                donemler.append(row)
            used = {
                **base_meta,
                "tablo": "anomali_donemleri",
                "hedef_yil": cal_body.get("target_year"),
                "anomali_gun": anom.get("n_days"),
                "ustunde": anom.get("n_positive"),
                "altinda": anom.get("n_negative"),
                "max_z": anom.get("max_z"),
                "donemler": donemler,
            }
        elif sec in ("validation", "summary") and focus == "model":
            used = {
                **base_meta,
                "tablo": "model_dogrulama" if sec == "validation" else "alan_ozeti",
                "dogrulama": {
                    "rmse_ham": loc.get("rmse_raw"),
                    "rmse_harmonize": loc.get("rmse_harmonized"),
                    "rmse_degisim_pct": loc.get("rmse_reduction_pct"),
                    "sapma_ham_pct": loc.get("relative_bias_raw_pct"),
                    "sapma_harmonize_pct": loc.get("relative_bias_harmonized_pct"),
                    "korelasyon_ham": loc.get("correlation_raw"),
                    "korelasyon_harmonize": loc.get("correlation_harmonized"),
                    "n_ay": loc.get("n"),
                } if loc else None,
            }
        elif sec == "validation":
            used = {
                **base_meta,
                "tablo": "yerel_dogrulama",
                "rmse_ham": loc.get("rmse_raw"),
                "rmse_harmonize": loc.get("rmse_harmonized"),
                "rmse_degisim_pct": loc.get("rmse_reduction_pct"),
                "sapma_ham_pct": loc.get("relative_bias_raw_pct"),
                "sapma_harmonize_pct": loc.get("relative_bias_harmonized_pct"),
                "n_ay": loc.get("n"),
            }
        else:
            # overview / briefing: kompakt genel
            raw_rep = reports.get("raw_filtered") or reports.get("raw_all") or {}
            used = {
                **base_meta,
                "tablo": "genel_ozet",
                "kirilma": {
                    "ham_adim_pct": (raw_rep.get("step") or {}).get("step_pct"),
                    "ham_kirilma": verdict.get("raw_break_at_transition"),
                    "harmonize_adim_kalkti": verdict.get("harmonized_step_eliminated"),
                },
                "bu_ay_vs_medyan": {
                    "oran": vs.get("ratio"),
                    "yuzdelik": vs.get("percentile"),
                } if vs.get("available") else None,
                "anomali_gun": anom.get("n_days"),
                "dogrulama": {
                    "rmse_ham": loc.get("rmse_raw"),
                    "rmse_harmonize": loc.get("rmse_harmonized"),
                    "rmse_degisim_pct": loc.get("rmse_reduction_pct"),
                } if loc else None,
            }

        if not ds.configured():
            return clean({
                "available": False,
                "empty": False,
                "used_numbers": used,
                "lang": "en" if en else "tr",
                "message": (
                    "DeepSeek API key not set (backend/.env → DEEPSEEK_API_KEY)."
                    if en else
                    "DeepSeek API anahtarı tanımlı değil (backend/.env → DEEPSEEK_API_KEY)."
                ),
            })

        try:
            out = ds.chat_interpret(used, focus=focus, section=sec, question=question, lang=lang)
        except RuntimeError as exc:
            return clean({
                "available": True,
                "empty": False,
                "used_numbers": used,
                "lang": "en" if en else "tr",
                "error": str(exc),
            })

        return clean({
            "available": True,
            "empty": False,
            "markdown": out["markdown"],
            "model": out["model"],
            "used_numbers": used,
            "lang": "en" if en else "tr",
        })

    # ------------------------------------------------------------------
    # user-supplied detections
    # ------------------------------------------------------------------
    def harmonize_detections(self, rows: list[dict]) -> dict:
        satmap = {"terra": "Terra", "aqua": "Aqua", "snpp": "SNPP", "npp": "SNPP",
                  "noaa20": "NOAA20", "n20": "NOAA20", "noaa21": "NOAA21", "n21": "NOAA21",
                  "t": "Terra", "a": "Aqua", "n": "SNPP", "j1": "NOAA20", "j2": "NOAA21"}
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
