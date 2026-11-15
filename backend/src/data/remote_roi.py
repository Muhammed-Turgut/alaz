"""On-demand NASA FIRMS Area API fetch for ROIs outside the local disk archive.

Local Turkey archive remains the fast path when the ROI sits fully inside its
coverage. Anywhere else (or straddling the edge) uses this module: Standard +
NRT products for a rolling window, chunk-cached under ``data/cache/remote_roi/``.

Requires ``FIRMS_MAP_KEY``. Not a silent global archive — meta always labels
``data_tier: remote-firms-api``.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

from src.data.archive_store import (
    bbox_area_km2,
    point_in_ring,
    standardize_raw,
)
from src.data.firms_archive import (
    BASE,
    HEADER,
    Chunk,
    TxLimiter,
    plan_chunks,
)

log = logging.getLogger("aether.remote_roi")

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "data" / "cache" / "remote_roi"

# Interactive remote history cap (full planet multi-decade is not feasible online).
REMOTE_MAX_YEARS = 10
REMOTE_DEFAULT_YEARS = 3
REMOTE_ROW_CAP = 500_000

# Same product families as the local archive planner, including historical SNPP.
# Live NRT retirement of SNPP does NOT remove Standard (or archived NRT) from remote history.
REMOTE_PRODUCTS = {
    "MODIS": ("MODIS_SP", "MODIS_NRT"),
    "VIIRS_SNPP": ("VIIRS_SNPP_SP", "VIIRS_SNPP_NRT"),
    "VIIRS_NOAA20": ("VIIRS_NOAA20_SP", "VIIRS_NOAA20_NRT"),
    "VIIRS_NOAA21": ("VIIRS_NOAA21_SP", "VIIRS_NOAA21_NRT"),
}

_CHUNK_START_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_")


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _bbox_key(west: float, south: float, east: float, north: float) -> str:
    raw = f"{west:.3f},{south:.3f},{east:.3f},{north:.3f}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def clip_availability(
    availability: dict[str, tuple[date, date]],
    win_first: date,
    win_last: date,
) -> dict[str, tuple[date, date]]:
    """Intersect product availability with the requested window."""
    out: dict[str, tuple[date, date]] = {}
    for src, (a, b) in availability.items():
        lo, hi = max(a, win_first), min(b, win_last)
        if lo <= hi:
            out[src] = (lo, hi)
    return out


def _product_family(source: str) -> str:
    for fam, (sp, nrt) in REMOTE_PRODUCTS.items():
        if source in (sp, nrt):
            return fam
    if source.startswith("MODIS"):
        return "MODIS"
    if "SNPP" in source:
        return "VIIRS_SNPP"
    if "NOAA20" in source or "NOAA-20" in source:
        return "VIIRS_NOAA20"
    if "NOAA21" in source or "NOAA-21" in source:
        return "VIIRS_NOAA21"
    return source.split("_")[0]


def _chunk_start_from_path(path: Path) -> date | None:
    m = _CHUNK_START_RE.match(path.name)
    if not m:
        return None
    try:
        return date.fromisoformat(m.group(1))
    except ValueError:
        return None


def _index_cached_csvs(raw_dir: Path) -> list[tuple[date, str, Path]]:
    """Return (chunk_start, source, path) for readable CSVs under raw_dir."""
    indexed: list[tuple[date, str, Path]] = []
    for path in raw_dir.rglob("*.csv"):
        if path.name.endswith(".tmp"):
            continue
        try:
            source = path.relative_to(raw_dir).parts[0]
        except ValueError:
            continue
        start = _chunk_start_from_path(path)
        if start is None:
            # Unknown naming — put at epoch so balanced reader still sees it last
            start = date(1970, 1, 1)
        indexed.append((start, source, path))
    return indexed


def _balanced_newest_first(
    indexed: list[tuple[date, str, Path]],
) -> list[tuple[date, str, Path]]:
    """Interleave product families, newest chunk-first within each family.

    Avoids alphabetical path order (which can drop VIIRS entirely under a row cap).
    """
    by_fam: dict[str, list[tuple[date, str, Path]]] = defaultdict(list)
    for item in indexed:
        by_fam[_product_family(item[1])].append(item)
    for fam in by_fam:
        by_fam[fam].sort(key=lambda t: (t[0], t[1]), reverse=True)

    families = sorted(by_fam.keys())
    pointers = {f: 0 for f in families}
    out: list[tuple[date, str, Path]] = []
    while True:
        ready: list[tuple[date, str, str, Path]] = []
        for fam in families:
            i = pointers[fam]
            if i < len(by_fam[fam]):
                start, source, path = by_fam[fam][i]
                ready.append((start, fam, source, path))
        if not ready:
            break
        ready.sort(key=lambda t: t[0], reverse=True)
        start, fam, source, path = ready[0]
        out.append((start, source, path))
        pointers[fam] += 1
    return out


def load_cached_parts(
    raw_dir: Path,
    *,
    row_cap: int = REMOTE_ROW_CAP,
) -> tuple[list[pd.DataFrame], dict]:
    """Load cached CSVs with balanced newest-first selection under ``row_cap``.

    Returns parts and truncation metadata. Does not invent detections.
    """
    indexed = _index_cached_csvs(raw_dir)
    ordered = _balanced_newest_first(indexed)
    parts: list[pd.DataFrame] = []
    rows_loaded = 0
    files_loaded = 0
    files_skipped = 0
    sources_loaded: set[str] = set()
    sources_seen: set[str] = {s for _, s, _ in ordered}
    families_loaded: set[str] = set()
    date_min: date | None = None
    date_max: date | None = None
    truncated = False
    rows_excluded_est = 0

    def _est_rows(path: Path) -> int:
        try:
            return max(0, int((path.stat().st_size - 80) / 80))
        except OSError:
            return 0

    for start, source, path in ordered:
        if rows_loaded >= row_cap:
            truncated = True
            rows_excluded_est += _est_rows(path)
            files_skipped += 1
            continue
        try:
            raw = pd.read_csv(path, usecols=lambda c: True)
        except Exception:  # noqa: BLE001
            files_skipped += 1
            continue
        if raw.empty or "latitude" not in raw.columns:
            files_skipped += 1
            continue
        part = standardize_raw(raw, source)
        n = len(part)
        if n == 0:
            files_skipped += 1
            continue
        if rows_loaded + n > row_cap:
            keep = max(0, row_cap - rows_loaded)
            if keep <= 0:
                truncated = True
                rows_excluded_est += n
                files_skipped += 1
                continue
            part = part.iloc[:keep].copy()
            rows_excluded_est += n - keep
            truncated = True
            n = keep
        parts.append(part)
        rows_loaded += n
        files_loaded += 1
        sources_loaded.add(source)
        families_loaded.add(_product_family(source))
        if date_min is None or start < date_min:
            date_min = start
        if date_max is None or start > date_max:
            date_max = start

    families_seen = {_product_family(s) for s in sources_seen}
    families_missing = sorted(families_seen - families_loaded)
    meta = {
        "truncated": truncated,
        "row_cap": row_cap,
        "rows_loaded": rows_loaded,
        "rows_excluded_est": rows_excluded_est,
        "files_loaded": files_loaded,
        "files_skipped": files_skipped,
        "sources_loaded": sorted(sources_loaded),
        "sources_seen": sorted(sources_seen),
        "product_families_loaded": sorted(families_loaded),
        "product_families_seen": sorted(families_seen),
        "product_families_missing_after_cap": families_missing,
        "chunk_date_span_loaded": [
            date_min.isoformat() if date_min else None,
            date_max.isoformat() if date_max else None,
        ],
        "truncation_reason": (
            f"Row cap {row_cap:,} reached; loaded balanced newest-first across product families."
            if truncated
            else None
        ),
    }
    return parts, meta


class RemoteRoiClient:
    def __init__(self, map_key: str | None = None, timeout: int = 120, workers: int = 2):
        self.key = (map_key if map_key is not None else os.getenv("FIRMS_MAP_KEY", "")).strip()
        self.timeout = timeout
        self.workers = max(1, min(int(workers), 4))
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "AETHER-FIRE/1.0 (NASA Space Apps 2026)"

    def configured(self) -> bool:
        return bool(self.key)

    def availability(self) -> dict[str, tuple[date, date]]:
        if not self.key:
            raise RuntimeError("FIRMS_MAP_KEY gerekli (küresel ROI için Area API).")
        r = self.session.get(f"{BASE}/api/data_availability/csv/{self.key}/all", timeout=60)
        r.raise_for_status()
        out: dict[str, tuple[date, date]] = {}
        for line in r.text.strip().splitlines()[1:]:
            parts = line.split(",")
            if len(parts) == 3:
                out[parts[0]] = (date.fromisoformat(parts[1]), date.fromisoformat(parts[2]))
        if not out:
            raise RuntimeError("FIRMS veri kapsamı okunamadı.")
        return out

    def _fetch_chunk(self, c: Chunk, area_arg: str, dest: Path, limiter: TxLimiter) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists() and dest.stat().st_size > 40:
            return dest
        url = f"{BASE}/api/area/csv/{self.key}/{c.source}/{area_arg}/{c.days}/{c.start.isoformat()}"
        err = ""
        for attempt in range(5):
            limiter.acquire(c.cost)
            try:
                r = self.session.get(url, timeout=self.timeout)
                if r.status_code == 200 and r.content.lstrip().startswith(HEADER):
                    tmp = dest.with_suffix(dest.suffix + ".tmp")
                    tmp.write_bytes(r.content)
                    tmp.replace(dest)
                    return dest
                err = f"http {r.status_code}: {r.text[:120]!r}".replace(self.key, "***")
            except requests.RequestException as exc:
                err = f"{type(exc).__name__}: {str(exc)[:120]}"
            time.sleep(min(5 * 2 ** attempt, 45))
        raise RuntimeError(f"{c.source} {c.start} alınamadı: {err}")

    def fetch(
        self,
        *,
        west: float,
        south: float,
        east: float,
        north: float,
        years: int = REMOTE_DEFAULT_YEARS,
        polygon: list[list[float]] | None = None,
        requested_years: int | None = None,
    ) -> tuple[pd.DataFrame, dict]:
        """Download (or load cache) detections for bbox over the last ``years``.

        ``years`` is clamped to ``REMOTE_MAX_YEARS``. Pass ``requested_years`` to
        record the caller's original request when it exceeded the remote cap.
        """
        if not self.key:
            raise RuntimeError(
                "Küresel / arşiv-dışı ROI için FIRMS_MAP_KEY gerekli (backend/.env). "
                "Türkiye kutusu içindeki ROI yerel arşivi kullanır."
            )
        req_years = int(requested_years) if requested_years is not None else int(years)
        years = max(1, min(int(years), REMOTE_MAX_YEARS))
        km2 = bbox_area_km2(west, south, east, north)
        today = _today()
        win_last = today
        win_first = today - timedelta(days=365 * years)

        key = _bbox_key(west, south, east, north)
        root = CACHE / f"{key}_{years}y"
        raw_dir = root / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)

        full_avail = self.availability()
        avail = clip_availability(full_avail, win_first, win_last)
        known = {p for pair in REMOTE_PRODUCTS.values() for p in pair}
        wanted_in_window = {k: v for k, v in avail.items() if k in known}
        # Products known to FIRMS availability but outside our REMOTE_PRODUCTS set
        # are ignored intentionally; SNPP Standard/NRT are included when present.
        missing_wanted = sorted(known - set(full_avail.keys()))
        avail_outside_window = sorted(
            k for k in known
            if k in full_avail and k not in wanted_in_window
        )
        avail = wanted_in_window
        if not avail:
            return pd.DataFrame(), {
                "empty_reason": "no_product_coverage",
                "data_tier": "remote-firms-api",
                "window": [win_first.isoformat(), win_last.isoformat()],
                "window_start": win_first.isoformat(),
                "window_end": win_last.isoformat(),
                "requested_years": req_years,
                "effective_years": years,
                "history_years": years,
                "history_years_cap": REMOTE_MAX_YEARS,
                "area_km2": round(km2, 1),
                "rows": 0,
                "partial": False,
                "products_missing_from_availability": missing_wanted,
                "products_outside_window": avail_outside_window,
                "note": "Requested window has no overlapping FIRMS product availability.",
            }

        chunks = plan_chunks(avail, today)
        # Newest first so a rate-limited first pass still prefers recent coverage.
        chunks = sorted(chunks, key=lambda c: c.start, reverse=True)
        area_arg = f"{west:g},{south:g},{east:g},{north:g}"
        limiter = TxLimiter(budget=4000, already_used=0)

        fetched = 0
        cached = 0
        failed = 0
        failed_detail: list[dict] = []
        for c in chunks:
            path = c.path(raw_dir)
            try:
                before = path.exists() and path.stat().st_size > 40
                self._fetch_chunk(c, area_arg, path, limiter)
                if before:
                    cached += 1
                else:
                    fetched += 1
            except Exception as exc:  # noqa: BLE001
                failed += 1
                failed_detail.append({
                    "source": c.source,
                    "start": c.start.isoformat(),
                    "error": str(exc)[:160].replace(self.key, "***"),
                })
                log.warning("remote chunk failed %s %s: %s", c.source, c.start, exc)

        parts, load_meta = load_cached_parts(raw_dir, row_cap=REMOTE_ROW_CAP)
        df = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
        if len(df):
            df = df.sort_values("ts").reset_index(drop=True)
            m = (
                (df["longitude"] >= west) & (df["longitude"] <= east)
                & (df["latitude"] >= south) & (df["latitude"] <= north)
            )
            df = df.loc[m]
            if polygon is not None and len(df):
                inside = point_in_ring(
                    df["latitude"].to_numpy(dtype=float),
                    df["longitude"].to_numpy(dtype=float),
                    polygon,
                )
                df = df.loc[inside]
            df = df.reset_index(drop=True)

        composition = {}
        kind_counts = {}
        sat_counts = {}
        if len(df):
            if "firms_source" in df.columns:
                composition = {str(k): int(v) for k, v in df["firms_source"].value_counts().items()}
            if "product_kind" in df.columns:
                kind_counts = {str(k): int(v) for k, v in df["product_kind"].value_counts().items()}
            if "satellite" in df.columns:
                sat_counts = {str(k): int(v) for k, v in df["satellite"].value_counts().items()}

        done_chunks = cached + fetched
        truncated = bool(load_meta.get("truncated"))
        # partial = incomplete coverage only (failed chunks or row-cap truncation).
        # A successful first-time download alone is NOT partial.
        partial = failed > 0 or truncated

        viirs_platforms = {
            k: v for k, v in sat_counts.items()
            if str(k).upper() in ("SNPP", "NOAA20", "NOAA21", "N", "N20", "N21")
            or "NOAA" in str(k).upper() or "SNPP" in str(k).upper()
        }
        snpp_in_avail = any(k.startswith("VIIRS_SNPP") for k in avail)
        snpp_in_rows = any(str(k).startswith("VIIRS_SNPP") for k in composition)

        meta = {
            "data_tier": "remote-firms-api",
            "local_archive": False,
            "area_id": f"remote:{key}",
            "name": "Remote FIRMS Area API",
            "available": True,
            "state": "remote",
            "partial": partial,
            "done_chunks": done_chunks,
            "total_chunks": len(chunks),
            "failed_chunks": failed,
            "failed_chunk_detail": failed_detail[:20],
            "area_km2": round(km2, 1),
            "query_bbox": {"west": west, "south": south, "east": east, "north": north},
            "rows": int(len(df)),
            "date_range": [
                str(df["ts"].min().date()) if len(df) else None,
                str(df["ts"].max().date()) if len(df) else None,
            ],
            "window": [win_first.isoformat(), win_last.isoformat()],
            "window_start": win_first.isoformat(),
            "window_end": win_last.isoformat(),
            "requested_years": req_years,
            "effective_years": years,
            "history_years": years,
            "history_years_cap": REMOTE_MAX_YEARS,
            "times_utc": True,
            "product_composition": composition,
            "product_kind_counts": kind_counts,
            "satellites": sat_counts,
            "viirs_platform_counts": viirs_platforms,
            "products_planned": sorted({c.source for c in chunks}),
            "products_missing_from_availability": missing_wanted,
            "products_outside_window": avail_outside_window,
            "snpp_availability_in_window": snpp_in_avail,
            "snpp_rows_present": snpp_in_rows,
            "empty_reason": None if len(df) else "no_detections",
            "chunks_fetched_now": fetched,
            "chunks_from_cache": cached,
            "first_download": fetched > 0 and cached == 0 and failed == 0 and not truncated,
            "cache_complete": fetched == 0 and failed == 0 and not truncated and done_chunks == len(chunks),
            "nrt_tail_note": (
                "Remote ROI mixes Standard/NRT per FIRMS availability; "
                "NRT tail is provisional."
                if kind_counts.get("nrt") and kind_counts.get("standard")
                else None
            ),
            "data_source": (
                "NASA FIRMS Area API (on-demand; independent prototype — not a NASA product). "
                "Cached under data/cache/remote_roi/."
            ),
            "note": (
                f"ROI outside local disk archive → remote API, last {years} year(s) "
                f"(cap {REMOTE_MAX_YEARS}"
                + (f"; requested {req_years}" if req_years != years else "")
                + "). First load may take several minutes; later loads use cache."
            ),
            "truncated": truncated,
            "row_cap": REMOTE_ROW_CAP,
            "rows_loaded": int(load_meta.get("rows_loaded", len(df))),
            "rows_excluded_est": int(load_meta.get("rows_excluded_est", 0)),
            "files_loaded": load_meta.get("files_loaded"),
            "files_skipped": load_meta.get("files_skipped"),
            "sources_loaded": load_meta.get("sources_loaded"),
            "product_families_loaded": load_meta.get("product_families_loaded"),
            "product_families_seen": load_meta.get("product_families_seen"),
            "product_families_missing_after_cap": load_meta.get(
                "product_families_missing_after_cap"
            ),
            "chunk_date_span_loaded": load_meta.get("chunk_date_span_loaded"),
            "truncation_reason": load_meta.get("truncation_reason"),
        }
        if truncated:
            meta["note"] = (
                (meta.get("note") or "")
                + " "
                + (load_meta.get("truncation_reason") or "Row cap reached; coverage is partial.")
            ).strip()
        if failed > 0:
            meta["note"] = (
                (meta.get("note") or "")
                + f" {failed} chunk(s) failed; see failed_chunk_detail."
            ).strip()
        if snpp_in_avail and not snpp_in_rows and len(df):
            meta["note"] = (
                (meta.get("note") or "")
                + " SNPP products were in the plan/window but contributed 0 rows after filters."
            ).strip()
        elif not snpp_in_avail:
            meta["note"] = (
                (meta.get("note") or "")
                + " No SNPP product availability intersects this window "
                "(VIIRS coverage may start with NOAA-20/21 only — not zero VIIRS activity)."
            ).strip()
        return df, meta
