"""NASA FIRMS near-real-time ingestor (MODIS_NRT, VIIRS_NOAA20_NRT, VIIRS_NOAA21_NRT).

Live VIIRS streams use NOAA-20 and NOAA-21 only. Suomi-NPP (SNPP) NRT production
ended 2026-11-02 per NASA FIRMS; historical SNPP Standard/NRT archive chunks remain
in ``archive_store`` / ``firms_archive`` and are never deleted by this client.

Fallback chain (the app always works, with or without a key or network):

  1. FIRMS area API            - if ``FIRMS_MAP_KEY`` is set (bbox + 1-5 day range)
  2. FIRMS open global CSVs    - 24 h / 7 d feeds, no key required
  3. fresh / stale disk cache
  4. bundled real snapshot     - NASA FIRMS open feed sample
  5. (handled by the caller)   - empty result; never invent detections

Every result carries ``meta`` describing exactly which tier produced the data so
the UI never presents cached or bundled data as live.
"""
from __future__ import annotations

import io
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from src.engine.diurnal_normalizer import firms_to_timestamp

log = logging.getLogger("aether.firms")

ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = ROOT / "data" / "cache" / "firms"
SAMPLE_DIR = ROOT / "data" / "samples"

# Live NRT products only. SNPP intentionally omitted (production ended 2026-11-02).
LIVE_SOURCES = ["MODIS_NRT", "VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT"]
SOURCES = LIVE_SOURCES  # backwards-compatible alias

# Historical / retired NRT product — never used in live fetch.
RETIRED_LIVE_SOURCES = ("VIIRS_SNPP_NRT",)

OPEN_URLS = {
    ("MODIS_NRT", 1): "https://firms.modaps.eosdis.nasa.gov/data/active_fire/modis-c6.1/csv/MODIS_C6_1_Global_24h.csv",
    ("MODIS_NRT", 7): "https://firms.modaps.eosdis.nasa.gov/data/active_fire/modis-c6.1/csv/MODIS_C6_1_Global_7d.csv",
    ("VIIRS_NOAA20_NRT", 1): "https://firms.modaps.eosdis.nasa.gov/data/active_fire/noaa-20-viirs-c2/csv/J1_VIIRS_C2_Global_24h.csv",
    ("VIIRS_NOAA20_NRT", 7): "https://firms.modaps.eosdis.nasa.gov/data/active_fire/noaa-20-viirs-c2/csv/J1_VIIRS_C2_Global_7d.csv",
    ("VIIRS_NOAA21_NRT", 1): "https://firms.modaps.eosdis.nasa.gov/data/active_fire/noaa-21-viirs-c2/csv/J2_VIIRS_C2_Global_24h.csv",
    ("VIIRS_NOAA21_NRT", 7): "https://firms.modaps.eosdis.nasa.gov/data/active_fire/noaa-21-viirs-c2/csv/J2_VIIRS_C2_Global_7d.csv",
}
API_URL = "https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/{source}/{area}/{days}"

_SAT_MAP = {
    "T": "Terra", "TERRA": "Terra", "A": "Aqua", "AQUA": "Aqua",
    "N": "SNPP", "NPP": "SNPP", "SUOMI NPP": "SNPP", "SNPP": "SNPP",
    "N20": "NOAA20", "NOAA-20": "NOAA20", "NOAA20": "NOAA20", "1": "NOAA20", "J1": "NOAA20",
    "N21": "NOAA21", "NOAA-21": "NOAA21", "NOAA21": "NOAA21", "2": "NOAA21", "J2": "NOAA21",
}

_DEFAULT_SAT = {
    "MODIS_NRT": "Terra", "MODIS_SP": "Terra",
    "VIIRS_SNPP_NRT": "SNPP", "VIIRS_SNPP_SP": "SNPP",
    "VIIRS_NOAA20_NRT": "NOAA20", "VIIRS_NOAA20_SP": "NOAA20",
    "VIIRS_NOAA21_NRT": "NOAA21", "VIIRS_NOAA21_SP": "NOAA21",
}
_VIIRS_CONF = {"l": 30, "low": 30, "n": 65, "nominal": 65, "h": 90, "high": 90}


def product_kind(source: str) -> str:
    """Return Standard / NRT / unknown from a FIRMS product id."""
    if not source or not isinstance(source, str):
        return "unknown"
    s = source.upper()
    if s.endswith("_SP") or "_SP_" in s:
        return "standard"
    if s.endswith("_NRT") or "_NRT_" in s or "NRT" in s:
        return "nrt"
    return "unknown"


@dataclass
class FetchMeta:
    tier: str = "unknown"          # api | open-feed | cache | stale-cache | bundled-sample | none
    live: bool = False
    fetched_at: str = ""           # legacy alias of retrieved_at (this call)
    retrieved_at: str = ""         # when this process read/returned the rows
    source_fetched_at: str = ""    # last successful network fetch (API/open-feed)
    notes: list = field(default_factory=list)
    rows: int = 0
    source: str = ""
    product_kind: str = "nrt"
    instrument: str = ""
    satellite_default: str = ""

    def to_dict(self):
        return {
            "tier": self.tier,
            "live": self.live,
            "fetched_at": self.fetched_at or self.retrieved_at,
            "retrieved_at": self.retrieved_at or self.fetched_at,
            "source_fetched_at": self.source_fetched_at or None,
            "notes": self.notes,
            "rows": self.rows,
            "source": self.source,
            "product_kind": self.product_kind,
            "instrument": self.instrument,
            "satellite_default": self.satellite_default,
        }


def _now_iso() -> str:
    return pd.Timestamp.now("UTC").isoformat()


def _sidecar_path(cache: Path) -> Path:
    return cache.with_suffix(cache.suffix + ".meta.json")


def _write_cache_sidecar(cache: Path, *, source_fetched_at: str, tier: str) -> None:
    side = _sidecar_path(cache)
    payload = {
        "source_fetched_at": source_fetched_at,
        "tier": tier,
        "cache_file": cache.name,
    }
    try:
        side.write_text(json.dumps(payload), encoding="utf-8")
    except OSError as exc:
        log.warning("could not write cache sidecar %s: %s", side, exc)


def _read_cache_sidecar(cache: Path) -> dict:
    side = _sidecar_path(cache)
    if not side.exists():
        return {}
    try:
        data = json.loads(side.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _mtime_iso(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


def standardize(raw: pd.DataFrame, source: str) -> pd.DataFrame:
    """Map a raw FIRMS CSV to the common detection schema."""
    if raw is None or raw.empty:
        return pd.DataFrame()
    is_modis = source.startswith("MODIS")
    out = pd.DataFrame()
    out["latitude"] = pd.to_numeric(raw["latitude"], errors="coerce")
    out["longitude"] = pd.to_numeric(raw["longitude"], errors="coerce")
    out["ts"] = firms_to_timestamp(raw["acq_date"], raw["acq_time"]).to_numpy()
    sat = raw["satellite"].astype(str).str.strip().str.upper().map(_SAT_MAP)
    out["satellite"] = sat.fillna(_DEFAULT_SAT.get(source, "Terra")).to_numpy()
    out["instrument"] = "MODIS" if is_modis else "VIIRS"
    out["scan"] = pd.to_numeric(raw["scan"], errors="coerce")
    out["track"] = pd.to_numeric(raw["track"], errors="coerce")
    out["frp"] = pd.to_numeric(raw["frp"], errors="coerce")
    t21 = "brightness" if is_modis else "bright_ti4"
    t31 = "bright_t31" if is_modis else "bright_ti5"
    out["bright_t21"] = pd.to_numeric(raw.get(t21), errors="coerce")
    out["bright_t31"] = pd.to_numeric(raw.get(t31), errors="coerce")
    conf = raw["confidence"]
    if is_modis:
        out["confidence"] = pd.to_numeric(conf, errors="coerce")
    else:
        mapped = conf.astype(str).str.lower().map(_VIIRS_CONF)
        out["confidence"] = mapped.fillna(pd.to_numeric(conf, errors="coerce")).fillna(65)
    # Preserve product day/night; LST-consistent flag is added in prepare().
    out["daynight"] = raw.get("daynight", "D")
    out["daynight_raw"] = out["daynight"]
    out["firms_source"] = source
    out["product_kind"] = product_kind(source)
    if "version" in raw.columns:
        out["product_version"] = raw["version"].astype(str)
    else:
        out["product_version"] = "unknown"
    out = out.dropna(subset=["latitude", "longitude", "ts", "scan", "track", "frp"])
    out["confidence"] = out["confidence"].fillna(65).astype(float)
    out["bright_t21"] = out["bright_t21"].fillna(330.0)
    out["bright_t31"] = out["bright_t31"].fillna(290.0)
    return out.reset_index(drop=True)


class FirmsClient:
    def __init__(self, map_key: str | None = None, cache_ttl_s: int = 3 * 3600, timeout: int = 40):
        self.map_key = (map_key if map_key is not None else os.getenv("FIRMS_MAP_KEY", "")).strip()
        self.cache_ttl_s = cache_ttl_s
        self.timeout = timeout
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "AETHER-FIRE/1.0 (NASA Space Apps 2026)"

    # ---- one source ---------------------------------------------------
    def _cache_file(self, source: str, days: int, bbox) -> Path:
        tag = "world" if bbox is None else "_".join(f"{v:.1f}" for v in bbox)
        return CACHE_DIR / f"{source}_{days}d_{tag}.csv"

    def fetch_source(self, source: str, days: int = 1, bbox=None, force: bool = False):
        """Returns (standardised_df, FetchMeta) for a single FIRMS product.

        Refuses retired live sources (e.g. VIIRS_SNPP_NRT) so stale SNPP caches
        are never presented as current live data.
        """
        now = _now_iso()
        meta = FetchMeta(
            fetched_at=now,
            retrieved_at=now,
            source=source,
            product_kind=product_kind(source),
            instrument="MODIS" if source.startswith("MODIS") else "VIIRS",
            satellite_default=_DEFAULT_SAT.get(source, ""),
        )
        if source in RETIRED_LIVE_SOURCES:
            meta.tier = "retired"
            meta.live = False
            meta.notes.append(
                f"{source} is retired from live fetch (SNPP NRT ended 2026-11-02); "
                "historical archive records are unchanged."
            )
            return pd.DataFrame(), meta
        if source not in LIVE_SOURCES:
            meta.tier = "none"
            meta.notes.append(f"unknown live source: {source}")
            return pd.DataFrame(), meta

        days = 7 if days > 1 and self.map_key == "" else max(1, min(int(days), 5))
        cache = self._cache_file(source, days, bbox if self.map_key else None)

        if cache.exists() and cache.stat().st_size > 300 and not force and (time.time() - cache.stat().st_mtime) < self.cache_ttl_s:
            # Fresh disk cache is NOT a live API pull this request.
            side = _read_cache_sidecar(cache)
            source_at = side.get("source_fetched_at") or _mtime_iso(cache)
            meta.tier = "cache"
            meta.live = False
            meta.source_fetched_at = str(source_at)
            age_s = int(time.time() - cache.stat().st_mtime)
            meta.notes.append(
                f"fresh cache ({age_s} s old); retrieved_at=this call, "
                f"source_fetched_at=last successful network fetch"
            )
            df = standardize(pd.read_csv(cache), source)
            meta.rows = len(df)
            return df, meta

        raw, tier, note = None, None, None
        try:
            if self.map_key:
                area = "world" if bbox is None else f"{bbox[1]:.3f},{bbox[0]:.3f},{bbox[3]:.3f},{bbox[2]:.3f}"
                # Alan API'sinde gün sayısı UTC takvim günüdür (1 = yalnız bugün). Açık "24h" beslemesiyle
                # aynı pencereyi (dün + bugün) vermek için bir gün fazlası istenir; API üst sınırı 5.
                url = API_URL.format(key=self.map_key, source=source, area=area, days=min(days + 1, 5))
                r = self.session.get(url, timeout=self.timeout)
                r.raise_for_status()
                if r.text.lstrip().lower().startswith("invalid") or "latitude" not in r.text[:300]:
                    raise RuntimeError(r.text[:120])
                raw, tier = pd.read_csv(io.StringIO(r.text)), "api"
            else:
                url = OPEN_URLS[(source, 7 if days > 1 else 1)]
                r = self.session.get(url, timeout=self.timeout)
                r.raise_for_status()
                raw, tier = pd.read_csv(io.StringIO(r.text)), "open-feed"
            if len(raw) > 0:
                raw.to_csv(cache, index=False)
                _write_cache_sidecar(cache, source_fetched_at=now, tier=tier)
            else:
                meta.notes.append("feed returned 0 rows (not cached)")
        except Exception as exc:  # noqa: BLE001 - network is best-effort
            msg = str(exc).replace(self.map_key, "***") if self.map_key else str(exc)
            note = f"{type(exc).__name__}: {msg[:100]}"
            log.warning("FIRMS %s fetch failed: %s", source, note)

        if raw is not None:
            meta.tier, meta.live = tier, True
            meta.source_fetched_at = now
            df = standardize(raw, source)
            meta.rows = len(df)
            return df, meta

        meta.notes.append(f"live fetch failed ({note})")
        if cache.exists():
            side = _read_cache_sidecar(cache)
            source_at = side.get("source_fetched_at") or _mtime_iso(cache)
            meta.tier = "stale-cache"
            meta.live = False
            meta.source_fetched_at = str(source_at)
            meta.notes.append(
                f"using stale cache {int((time.time() - cache.stat().st_mtime) / 3600)} h old"
            )
            df = standardize(pd.read_csv(cache), source)
            meta.rows = len(df)
            return df, meta

        sample = SAMPLE_DIR / f"{source}_sample.csv"
        if sample.exists():
            meta.tier = "bundled-sample"
            meta.live = False
            meta.source_fetched_at = ""
            meta.notes.append("bundled real FIRMS snapshot — not live")
            df = standardize(pd.read_csv(sample), source)
            meta.rows = len(df)
            return df, meta
        meta.tier = "none"
        return pd.DataFrame(), meta

    # ---- all live products --------------------------------------------
    def fetch_live(self, days: int = 1, bbox=None, force: bool = False):
        """Fetch MODIS + NOAA-20/21 VIIRS; returns (df, overall_meta)."""
        frames, metas = [], {}
        for src in LIVE_SOURCES:
            df, m = self.fetch_source(src, days, bbox, force)
            metas[src] = m.to_dict()
            if len(df):
                frames.append(df)
        # Explicitly record that SNPP is not part of the live stream
        metas["VIIRS_SNPP_NRT"] = FetchMeta(
            tier="retired", live=False, source="VIIRS_SNPP_NRT",
            product_kind="nrt", instrument="VIIRS", satellite_default="SNPP",
            fetched_at=pd.Timestamp.now("UTC").isoformat(),
            notes=["SNPP NRT retired from live stream (ended 2026-11-02); use archive for historical SNPP."],
        ).to_dict()

        df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        if len(df) and bbox is not None:
            s, w, n, e = bbox
            df = df[(df.latitude >= s) & (df.latitude <= n) & (df.longitude >= w) & (df.longitude <= e)]
            df = df.reset_index(drop=True)
        tiers = {m["tier"] for m in metas.values() if m["tier"] != "retired"}
        sat_counts = {}
        if len(df) and "satellite" in df.columns:
            sat_counts = {str(k): int(v) for k, v in df["satellite"].value_counts().items()}
        source_times = [
            m.get("source_fetched_at")
            for m in metas.values()
            if m.get("source_fetched_at") and m.get("tier") != "retired"
        ]
        overall = {
            "live": all(m["live"] for m in metas.values() if m["rows"] > 0 and m["tier"] != "retired") and bool(len(df)),
            "tiers": sorted(tiers),
            "rows": int(len(df)),
            "newest": str(df["ts"].max()) if len(df) else None,
            "oldest": str(df["ts"].min()) if len(df) else None,
            "retrieved_at": _now_iso(),
            "source_fetched_at": max(source_times) if source_times else None,
            "key_configured": bool(self.map_key),
            "sources": metas,
            "live_sources": list(LIVE_SOURCES),
            "satellites": sat_counts,
            "product_kind": "nrt",
            "times_utc": True,
            "note": "Live stream: MODIS_NRT + VIIRS_NOAA20_NRT + VIIRS_NOAA21_NRT. SNPP historical only.",
        }
        return df, overall
