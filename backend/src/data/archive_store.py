"""Salt-okunur NASA FIRMS arşiv okuyucu.

Ham CSV dosyaları ``firms_archive`` tarafından bayt bayt saklanır. Bu modül onları
okur, ortak şemaya çevirir ve alan (bbox / poligon) süzgeçler. Koordinatlar ve
orijinal tespit satırları silinmez / değiştirilmez; yeni nokta üretilmez.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

import numpy as np
import pandas as pd

from src.data.firms_archive import ARCHIVE_DIR
from src.engine.diurnal_normalizer import firms_to_timestamp

log = logging.getLogger("aether.archive_store")

# FIRMS alan API ürün adı -> varsayılan uydu (CSV'de satellite boşsa)
_DEFAULT_SAT = {
    "MODIS_SP": "Terra", "MODIS_NRT": "Terra",
    "VIIRS_SNPP_SP": "SNPP", "VIIRS_SNPP_NRT": "SNPP",
    "VIIRS_NOAA20_SP": "NOAA20", "VIIRS_NOAA20_NRT": "NOAA20",
    "VIIRS_NOAA21_SP": "NOAA21", "VIIRS_NOAA21_NRT": "NOAA21",
}
_SAT_MAP = {
    "T": "Terra", "TERRA": "Terra", "A": "Aqua", "AQUA": "Aqua",
    "N": "SNPP", "NPP": "SNPP", "SUOMI NPP": "SNPP", "SNPP": "SNPP",
    "N20": "NOAA20", "NOAA-20": "NOAA20", "NOAA20": "NOAA20", "1": "NOAA20", "J1": "NOAA20",
    "N21": "NOAA21", "NOAA-21": "NOAA21", "NOAA21": "NOAA21", "2": "NOAA21", "J2": "NOAA21",
}
_VIIRS_CONF = {"l": 30, "low": 30, "n": 65, "nominal": 65, "h": 90, "high": 90}


def product_kind(source: str) -> str:
    if not source or not isinstance(source, str):
        return "unknown"
    s = source.upper()
    if s.endswith("_SP"):
        return "standard"
    if s.endswith("_NRT"):
        return "nrt"
    return "unknown"

# Seçilebilir alan üst sınırı (yaklaşık km²). Kota alan-bağımsız; sınır performans + UX için.
# ~3°×3° civarı; daha büyük ROI için AI/brifing özet yolu kullanılır.
MAX_AREA_KM2 = 120_000.0


def _area_json(root: Path) -> dict:
    p = root / "area.json"
    if not p.exists():
        raise FileNotFoundError(f"arşiv alanı yok: {p}")
    return json.loads(p.read_text())


def _progress(root: Path) -> dict:
    p = root / "progress.json"
    if not p.exists():
        return {"state": "missing", "done_chunks": 0, "total_chunks": 0}
    return json.loads(p.read_text())


def bbox_area_km2(west: float, south: float, east: float, north: float) -> float:
    """Dikdörtgen alanın yaklaşık km²'si (küresel ortalama enlem)."""
    lat_m = (south + north) / 2.0
    dy = (north - south) * 111.32
    dx = (east - west) * 111.32 * max(0.05, float(np.cos(np.radians(lat_m))))
    return abs(dx * dy)


def polygon_bbox(polygon: list[list[float]]) -> tuple[float, float, float, float]:
    """[[lat, lon], ...] -> (west, south, east, north)."""
    lats = [float(p[0]) for p in polygon]
    lons = [float(p[1]) for p in polygon]
    return min(lons), min(lats), max(lons), max(lats)


def point_in_ring(lat: np.ndarray, lon: np.ndarray, ring: list[list[float]]) -> np.ndarray:
    """Işın yöntemi; ring = [[lat, lon], ...] (kapanış opsiyonel)."""
    n = len(ring)
    if n < 3:
        return np.zeros(len(lat), dtype=bool)
    verts = ring if ring[0] == ring[-1] else ring + [ring[0]]
    inside = np.zeros(len(lat), dtype=bool)
    for i in range(len(verts) - 1):
        y1, x1 = float(verts[i][0]), float(verts[i][1])
        y2, x2 = float(verts[i + 1][0]), float(verts[i + 1][1])
        cond = ((y1 > lat) != (y2 > lat)) & (
            lon < (x2 - x1) * (lat - y1) / (y2 - y1 + 1e-30) + x1
        )
        inside ^= cond
    return inside


def standardize_raw(raw: pd.DataFrame, source: str) -> pd.DataFrame:
    """Ham FIRMS CSV satırlarını ortak şemaya çevir. Geçersiz satırları sayıp düşürür."""
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
    out["bright_t21"] = pd.to_numeric(raw[t21] if t21 in raw.columns else np.nan, errors="coerce")
    out["bright_t31"] = pd.to_numeric(raw[t31] if t31 in raw.columns else np.nan, errors="coerce")
    conf = raw["confidence"]
    if is_modis:
        out["confidence"] = pd.to_numeric(conf, errors="coerce")
    else:
        mapped = conf.astype(str).str.lower().map(_VIIRS_CONF)
        out["confidence"] = mapped.fillna(pd.to_numeric(conf, errors="coerce"))
    if "daynight" in raw.columns:
        out["daynight"] = raw["daynight"].astype(str)
    else:
        out["daynight"] = "unknown"
    out["daynight_raw"] = out["daynight"]
    out["firms_source"] = source
    out["product_kind"] = product_kind(source)
    if "version" in raw.columns:
        out["product_version"] = raw["version"].astype(str)
    else:
        out["product_version"] = "unknown"
    if "type" in raw.columns:
        out["nasa_type"] = pd.to_numeric(raw["type"], errors="coerce")
    else:
        out["nasa_type"] = np.nan
    # Ham koruma: yalnız konum/zaman/zorunlu alanlar yoksa düş (değer doldurma yok)
    before = len(out)
    out = out.dropna(subset=["latitude", "longitude", "ts", "scan", "track", "frp"])
    dropped = before - len(out)
    if dropped:
        log.debug("%s: %d satır eksik alan yüzünden atlandı", source, dropped)
    return out.reset_index(drop=True)


class ArchiveStore:
    """Bir alan kimliği için indirme durumu + bellek içi standartlaştırılmış tablo."""

    def __init__(self, area_id: str = "tr"):
        self.area_id = area_id
        self.root = ARCHIVE_DIR / area_id
        self._lock = threading.Lock()
        self._df: pd.DataFrame | None = None
        self._cache_key: tuple | None = None

    def status(self) -> dict:
        if not self.root.exists():
            return {
                "area_id": self.area_id, "available": False, "state": "missing",
                "done_chunks": 0, "total_chunks": 0, "rows_cached": 0,
            }
        area = _area_json(self.root)
        prog = _progress(self.root)
        w, s, e, n = area["bbox"]
        return {
            "area_id": self.area_id,
            "name": area.get("name", self.area_id),
            "available": True,
            "state": prog.get("state", "unknown"),
            "done_chunks": int(prog.get("done_chunks", 0)),
            "total_chunks": int(prog.get("total_chunks", 0)),
            "failed_chunks": int(prog.get("failed_chunks", 0)),
            "bbox": {"west": w, "south": s, "east": e, "north": n},
            "bbox_order": area.get("bbox_order", "west,south,east,north"),
            "max_area_km2": MAX_AREA_KM2,
            "rows_cached": 0 if self._df is None else int(len(self._df)),
            "partial": prog.get("state") != "done",
            "archive_products": area.get("products") or {},
        }

    def coverage_bbox(self) -> tuple[float, float, float, float]:
        area = _area_json(self.root)
        w, s, e, n = area["bbox"]
        return float(w), float(s), float(e), float(n)

    def _manifest_key(self) -> tuple:
        prog = _progress(self.root)
        man = self.root / "manifest.jsonl"
        size = man.stat().st_size if man.exists() else 0
        return (int(prog.get("done_chunks", 0)), size, prog.get("state", ""))

    def load(self, force: bool = False) -> pd.DataFrame:
        """Tüm indirilmiş dilimleri birleştir. İndirme sürerken kısmi veri döner."""
        key = self._manifest_key()
        with self._lock:
            if not force and self._df is not None and self._cache_key == key:
                return self._df
            raw_dir = self.root / "raw"
            if not raw_dir.exists():
                raise FileNotFoundError(f"ham arşiv yok: {raw_dir}")
            parts: list[pd.DataFrame] = []
            for path in sorted(raw_dir.rglob("*.csv")):
                # .../raw/<SOURCE>/<YEAR>/<file>.csv
                try:
                    source = path.relative_to(raw_dir).parts[0]
                except ValueError:
                    continue
                if source not in _DEFAULT_SAT:
                    continue
                try:
                    raw = pd.read_csv(path)
                except Exception as exc:  # noqa: BLE001
                    log.warning("okunamadı %s: %s", path, exc)
                    continue
                if raw.empty or "latitude" not in raw.columns:
                    continue
                parts.append(standardize_raw(raw, source))
            df = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
            if len(df):
                df = df.sort_values("ts").reset_index(drop=True)
            self._df = df
            self._cache_key = key
            log.info("arşiv %s yüklendi: %d satır (key=%s)", self.area_id, len(df), key)
            return df

    def contains_bbox(self, west: float, south: float, east: float, north: float) -> bool:
        """True if the query box lies fully inside the local disk archive coverage."""
        try:
            cw, cs, ce, cn = self.coverage_bbox()
        except FileNotFoundError:
            return False
        return (
            west >= cw - 1e-6 and east <= ce + 1e-6
            and south >= cs - 1e-6 and north <= cn + 1e-6
        )

    def assert_inside_coverage(self, west: float, south: float, east: float, north: float) -> None:
        if self.contains_bbox(west, south, east, north):
            return
        cw, cs, ce, cn = self.coverage_bbox()
        raise ValueError(
            f"Seçilen alan yerel arşiv kapsamı dışında. Yerel kapsama: "
            f"W{cw} S{cs} E{ce} N{cn}. "
            f"Kapsama dışı ROI için sistem FIRMS Area API (uzaktan) kullanır."
        )

    def assert_area_limit(self, west: float, south: float, east: float, north: float) -> float:
        km2 = bbox_area_km2(west, south, east, north)
        if km2 > MAX_AREA_KM2:
            raise ValueError(
                f"Alan çok büyük ({km2:,.0f} km²). Üst sınır {MAX_AREA_KM2:,.0f} km² "
                f"(daha küçük poligon veya yarıçap kullan)."
            )
        if east <= west or north <= south:
            raise ValueError("Geçersiz sınır kutusu.")
        return km2

    def query(
        self,
        *,
        bbox: tuple[float, float, float, float] | None = None,
        polygon: list[list[float]] | None = None,
        force: bool = False,
    ) -> tuple[pd.DataFrame, dict]:
        """bbox = (west, south, east, north); polygon = [[lat, lon], ...].

        Önce bbox ile kaba süzgeç, sonra (varsa) poligon. Ham satırlar korunur.
        """
        if polygon is None and bbox is None:
            raise ValueError("bbox veya polygon gerekli.")
        if polygon is not None:
            if len(polygon) < 3:
                raise ValueError("polygon en az 3 köşe ister.")
            west, south, east, north = polygon_bbox(polygon)
        else:
            west, south, east, north = bbox  # type: ignore[misc]

        self.assert_inside_coverage(west, south, east, north)
        km2 = self.assert_area_limit(west, south, east, north)

        df = self.load(force=force)
        status = self.status()
        empty_reason = None
        if df.empty:
            if status.get("state") in ("missing", "unknown") or int(status.get("done_chunks") or 0) == 0:
                empty_reason = "archive_not_ready"
            elif status.get("partial"):
                empty_reason = "archive_partial_no_rows"
            else:
                empty_reason = "no_detections"
            meta = {
                **status, "area_km2": km2, "rows": 0, "dropped_by_filter": 0,
                "empty_reason": empty_reason, "times_utc": True,
                "product_composition": {}, "nrt_tail_note": None,
            }
            return df.copy(), meta

        m = (
            (df["longitude"] >= west) & (df["longitude"] <= east)
            & (df["latitude"] >= south) & (df["latitude"] <= north)
        )
        sub = df.loc[m]
        if polygon is not None and len(sub):
            inside = point_in_ring(
                sub["latitude"].to_numpy(dtype=float),
                sub["longitude"].to_numpy(dtype=float),
                polygon,
            )
            sub = sub.loc[inside]

        composition: dict[str, int] = {}
        kind_counts: dict[str, int] = {}
        sat_counts: dict[str, int] = {}
        if len(sub):
            if "firms_source" in sub.columns:
                composition = {str(k): int(v) for k, v in sub["firms_source"].value_counts().items()}
            if "product_kind" in sub.columns:
                kind_counts = {str(k): int(v) for k, v in sub["product_kind"].value_counts().items()}
            if "satellite" in sub.columns:
                sat_counts = {str(k): int(v) for k, v in sub["satellite"].value_counts().items()}

        nrt_rows = int(kind_counts.get("nrt", 0))
        std_rows = int(kind_counts.get("standard", 0))
        unk_rows = int(kind_counts.get("unknown", 0))
        nrt_tail_note = None
        if nrt_rows and std_rows:
            nrt_tail_note = (
                "ROI mixes Standard (science-quality) and NRT rows. "
                "NRT tail is provisional and may be replaced by Standard later."
            )
        elif nrt_rows and not std_rows:
            nrt_tail_note = "ROI uses NRT products only for the selected span (provisional)."
        elif unk_rows and not (nrt_rows or std_rows):
            nrt_tail_note = "Product kind unknown for archived rows — not assumed Standard or NRT."

        if len(sub) == 0:
            empty_reason = "no_detections"

        area_products = {}
        try:
            area_products = _area_json(self.root).get("products") or {}
        except FileNotFoundError:
            area_products = {}
        meta = {
            **status,
            "area_km2": round(km2, 1),
            "query_bbox": {"west": west, "south": south, "east": east, "north": north},
            "rows": int(len(sub)),
            "raw_total_in_archive": int(len(df)),
            "date_range": [
                str(sub["ts"].min().date()) if len(sub) else None,
                str(sub["ts"].max().date()) if len(sub) else None,
            ],
            "empty_reason": empty_reason,
            "times_utc": True,
            "product_composition": composition,
            "product_kind_counts": kind_counts,
            "satellites": sat_counts,
            "nrt_tail_note": nrt_tail_note,
            "archive_products": area_products,
            "data_source": "NASA FIRMS archive (independent prototype; not a NASA product)",
        }
        return sub.reset_index(drop=True), meta
