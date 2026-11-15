"""AETHER-FIRE FastAPI application.

Run via ``python run.py`` (or ``uvicorn src.api.main:app``).  All heavy state is
loaded once in the lifespan hook; endpoints are plain (sync) functions executed in
the threadpool so long pandas operations never block the event loop.
"""
from __future__ import annotations

import logging
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import pandas as pd
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse

from src.api.schemas import (
    AreaCalendarRequest, AreaDensityRequest, AreaHybridRequest, AreaScopeRequest,
    AreaSeriesRequest, BriefingRequest, ExportHotspotsRequest, HarmonizeRequest,
    HealthResponse, InterpretRequest, ROIRequest,
)
from src.data.fires_feed import FiresFeed
from src.data.regions import REGIONS
from src.engine.service import AetherState

log = logging.getLogger("aether.api")

_state: AetherState | None = None
_lock = threading.Lock()
_load_error: str | None = None


def _load_env() -> None:
    env = Path(__file__).resolve().parents[2] / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def build_state() -> AetherState:
    _load_env()
    st = AetherState(map_key=os.environ.get("FIRMS_MAP_KEY") or None)
    st.load(progress=lambda *a: log.info(" ".join(str(x) for x in a)))
    return st


def get_state() -> AetherState:
    global _state
    if _state is None or not _state.ready:
        with _lock:
            if _state is None or not _state.ready:
                _state = build_state()
    return _state


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _state, _load_error
    try:
        _state = build_state()
    except Exception as exc:  # noqa: BLE001
        _load_error = repr(exc)
        log.exception("startup failed")
    yield


app = FastAPI(title="AETHER-FIRE", version="1.0.0", lifespan=lifespan,
              description="Advanced Earth Thermal Harmonization & Emission Reconstruction Engine - "
                          "MODIS / VIIRS hot-spot harmonization.")


app.add_middleware(GZipMiddleware, minimum_size=1024)
_feed: FiresFeed | None = None


@app.exception_handler(ValueError)
async def _value_error(_: Request, exc: ValueError):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


# --------------------------------------------------------------------- helpers
def _st() -> AetherState:
    if _state is None or not _state.ready:
        raise HTTPException(503, f"engine not ready: {_load_error or 'still loading'}")
    return _state


def _region(rid: Optional[str], allow_all: bool = False) -> str:
    if allow_all and rid in (None, "all"):
        return "all"
    if rid not in REGIONS:
        raise HTTPException(404, f"unknown region '{rid}'; valid: {', '.join(REGIONS)}")
    return rid


def _date(v: Optional[str], name: str) -> Optional[str]:
    if v is None or v == "":
        return None
    try:
        return str(pd.Timestamp(v).date())
    except Exception:  # noqa: BLE001
        raise HTTPException(422, f"{name} must be YYYY-MM-DD")


def _bbox(v: Optional[str]):
    if not v:
        return None
    try:
        s, w, n, e = [float(x) for x in v.split(",")]
    except Exception:  # noqa: BLE001
        raise HTTPException(422, "bbox must be 'south,west,north,east'")
    if not (-90 <= s < n <= 90 and -180 <= w < e <= 180):
        raise HTTPException(422, "bbox out of range")
    return [s, w, n, e]


def _span(start, end):
    if start and end and pd.Timestamp(start) > pd.Timestamp(end):
        raise HTTPException(422, "start must be <= end")


# ------------------------------------------------------------------- endpoints
@app.get("/api/health", response_model=HealthResponse)
def health():
    st = _state
    if st is None or not st.ready:
        return HealthResponse(status="loading" if not _load_error else "error", ready=False,
                              notes={"error": _load_error} if _load_error else {})
    return HealthResponse(
        status="ok", ready=True, model=st.model.version, model_loaded=True,
        detections=int(sum(len(r["df"]) for r in st.regions.values())), regions=list(REGIONS),
        load_seconds=round(st.load_seconds, 1), firms_key_configured=bool(getattr(st.firms, "map_key", None)),
        notes={"trained_on_startup": st.trained_now,
               "benchmark": "physically simulated 2001-2026 archive with known ground truth"},
    )


@app.get("/api/regions")
def regions():
    return _st().regions_payload()


@app.get("/api/model")
def model():
    return _st().model_payload()


@app.get("/api/validation")
def validation():
    return _st().validation_payload()


@app.get("/api/geometry")
def geometry():
    """Bow-tie footprint table: pixel size and area versus scan angle for MODIS and VIIRS (375 m I-band)."""
    import numpy as np

    from src.engine import geometry_h3 as geo
    ang = np.arange(0, 56, 5.0)
    ms, mt = geo.modis_pixel_size(ang)
    vs, vt = geo.viirs_pixel_size(ang * 56.28 / 55.0)
    return {
        "scan_angle_deg": ang.tolist(),
        "modis": {"scan_km": ms.round(3).tolist(), "track_km": mt.round(3).tolist(),
                  "area_km2": geo.footprint_area_km2(ms, mt).round(3).tolist(),
                  "expansion": geo.bowtie_expansion(ms, mt, "MODIS").round(3).tolist(),
                  "half_swath_km": round(geo.half_swath_km("MODIS"), 1)},
        "viirs": {"scan_km": vs.round(3).tolist(), "track_km": vt.round(3).tolist(),
                  "area_km2": geo.footprint_area_km2(vs, vt).round(3).tolist(),
                  "expansion": geo.bowtie_expansion(vs, vt, "VIIRS").round(3).tolist(),
                  "half_swath_km": round(geo.half_swath_km("VIIRS"), 1)},
        "h3": {"res7_km2": round(geo.cell_area_km2(7), 3), "res8_km2": round(geo.cell_area_km2(8), 3)},
        "note": "VIIRS açı ekseni kendi yarı-şeridine (56,28°) ölçeklenir; böylece her iki sensör nadirden kenara aynı aralığı kaplar.",
    }


@app.get("/api/series")
def series(region: str = "all", freq: str = Query("monthly", pattern="^(monthly|annual)$")):
    return _st().series_payload(_region(region, True), freq)


@app.get("/api/structural-break")
def structural_break(region: str = "all"):
    return _st().break_payload(_region(region, True))


@app.get("/api/calendar")
def calendar(region: str = "all", series: str = Query("harmonized", pattern="^(harmonized|raw)$")):
    return _st().calendar_payload(_region(region, True), series)


@app.get("/api/static-sources")
def static_sources(region: Optional[str] = None):
    return _st().static_sources(_region(region) if region else None)


@app.get("/api/hexes")
def hexes(region: str, start: Optional[str] = None, end: Optional[str] = None):
    start, end = _date(start, "start"), _date(end, "end")
    _span(start, end)
    return _st().hexes(_region(region), start, end)


@app.get("/api/live")
def live(days: int = Query(1, ge=1, le=7), bbox: Optional[str] = None, refresh: bool = False):
    return _st().live_hexes(days, _bbox(bbox), refresh)


@app.get("/api/fires")
def fires(instrument: str = Query("VIIRS", pattern="^(?i:viirs|modis)$"),
          mode: str = Query("REALTIME", pattern="^(?i:realtime)$"), refresh: bool = False):
    """Gerçek zamanlı NASA FIRMS tespitleri (son 24 saat), CSV. Sentetik veri üretmez."""
    global _feed
    if _feed is None:
        _load_env()
        _feed = FiresFeed()
    try:
        text, info = _feed.csv(instrument, force=refresh)
    except LookupError as exc:
        raise HTTPException(503, str(exc))
    headers = {
        "Cache-Control": "no-store",
        "X-Firms-Live": "1" if info["live"] else "0",
        "X-Firms-Tiers": ",".join(info["tiers"]),
        "X-Firms-Rows": str(info["rows"]),
        "X-Firms-Newest": info["newest"],
        "X-Firms-Oldest": info["oldest"],
        "X-Firms-Key": "1" if info["key"] else "0",
    }
    return PlainTextResponse(text, media_type="text/csv", headers=headers)


@app.get("/api/hex/{cell}")
def hex_detail(cell: str, source: str = Query("benchmark", pattern="^(benchmark|live)$"), region: Optional[str] = None,
               start: Optional[str] = None, end: Optional[str] = None, days: int = Query(1, ge=1, le=7),
               bbox: Optional[str] = None):
    import h3
    try:
        if not h3.is_valid_cell(cell) or h3.get_resolution(cell) != 7:
            raise ValueError
    except Exception:  # noqa: BLE001
        raise HTTPException(422, "cell must be a valid H3 resolution-7 index")
    start, end = _date(start, "start"), _date(end, "end")
    _span(start, end)
    rid = _region(region) if source == "benchmark" else None
    return _st().hex_detail(cell, source, rid, start, end, days, _bbox(bbox))


@app.post("/api/roi")
def roi(req: ROIRequest):
    start, end = _date(req.start, "start"), _date(req.end, "end")
    _span(start, end)
    rid = _region(req.region) if req.source == "benchmark" else None
    try:
        return _st().roi(req.polygon, req.source, rid, start, end, req.days, _bbox(",".join(map(str, req.bbox))) if req.bbox else None)
    except FileNotFoundError as exc:
        raise HTTPException(503, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/harmonize")
def harmonize(req: HarmonizeRequest):
    return _st().harmonize_detections([d.model_dump() for d in req.detections])


@app.get("/api/archive/{area_id}")
def archive_status(area_id: str = "tr"):
    st = _st()
    if area_id != st.archive.area_id:
        raise HTTPException(404, f"unknown archive '{area_id}'; known: {st.archive.area_id}")
    return st.archive_status()


def _area_call(fn, req):
    if getattr(req, "polygon", None) is None and getattr(req, "bbox", None) is None:
        raise HTTPException(422, "polygon veya bbox gerekli")
    try:
        return fn()
    except FileNotFoundError as exc:
        raise HTTPException(503, str(exc)) from exc
    except RuntimeError as exc:
        # e.g. missing FIRMS_MAP_KEY for global (non-TR) ROI
        raise HTTPException(503, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/area-calendar")
def area_calendar(req: AreaCalendarRequest):
    return _area_call(
        lambda: _st().area_calendar(
            polygon=req.polygon, bbox=req.bbox, series=req.series,
            target_year=req.target_year, start_year=req.start_year,
            end_year=req.end_year, month=req.month, force=req.force,
        ),
        req,
    )


@app.post("/api/area-series")
def area_series(req: AreaSeriesRequest):
    return _area_call(
        lambda: _st().area_series(
            polygon=req.polygon, bbox=req.bbox, freq=req.freq, force=req.force,
        ),
        req,
    )


@app.post("/api/area-break")
def area_break(req: AreaScopeRequest):
    return _area_call(
        lambda: _st().area_break(polygon=req.polygon, bbox=req.bbox, force=req.force),
        req,
    )


@app.post("/api/area-density")
def area_density(req: AreaDensityRequest):
    """ROI + son N yıl FIRMS hotspot tespit yoğunluğu (ısı haritası noktaları)."""
    return _area_call(
        lambda: _st().area_density(
            polygon=req.polygon, bbox=req.bbox, years=req.years,
            instrument=req.instrument, force=req.force,
        ),
        req,
    )


@app.post("/api/area-hybrid")
def area_hybrid(req: AreaHybridRequest):
    """Hibrit uyumlaştırma: aktif hücre ızgarası + dinamik bölgesel α."""
    return _area_call(
        lambda: _st().area_hybrid(
            polygon=req.polygon, bbox=req.bbox, grid=req.grid,
            fallback_alpha=req.fallback_alpha, force=req.force,
            map_recent_days=req.map_recent_days,
            map_years=req.map_years,
            map_start=req.map_start,
            map_end=req.map_end,
        ),
        req,
    )


@app.post("/api/export-hybrid-csv")
def export_hybrid_csv(req: AreaHybridRequest):
    """Aylık hibrit uyumlaştırma tablosunu CSV indir."""
    try:
        text, info = _st().export_hybrid_csv(
            polygon=req.polygon, bbox=req.bbox, grid=req.grid,
            fallback_alpha=req.fallback_alpha, force=req.force,
        )
    except FileNotFoundError as exc:
        raise HTTPException(503, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    n = info.get("n", 0)
    fname = f"alaz-hybrid-monthly-{n}.csv"
    headers = {
        "Content-Disposition": f'attachment; filename="{fname}"',
        "X-Alaz-Rows": str(n),
        "X-Alaz-Alpha": str(info.get("alpha") or ""),
        "X-Alaz-Fallback": "1" if info.get("fallback_used") else "0",
    }
    return PlainTextResponse(text, media_type="text/csv; charset=utf-8", headers=headers)


@app.post("/api/briefing")
def briefing(req: BriefingRequest):
    start, end = _date(req.start, "start"), _date(req.end, "end")
    _span(start, end)
    rid = _region(req.region) if req.source == "benchmark" else None
    try:
        return _st().briefing(
            req.source, rid, start, end, req.polygon, req.days,
            _bbox(",".join(map(str, req.bbox))) if req.bbox else None,
            audience=req.audience or "acil",
            lang=req.lang or "tr",
        )
    except FileNotFoundError as exc:
        raise HTTPException(503, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/export-hotspots")
def export_hotspots(req: ExportHotspotsRequest):
    """ROI içindeki sıcak noktaları CSV indir (challenge opsiyonel çıktı)."""
    try:
        text, info = _st().export_hotspots_csv(
            source=req.source, polygon=req.polygon, days=req.days,
        )
    except FileNotFoundError as exc:
        raise HTTPException(503, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    fname = f"alaz-hotspots-{req.source}-{info.get('n', 0)}.csv"
    headers = {
        "Content-Disposition": f'attachment; filename="{fname}"',
        "X-Alaz-Rows": str(info.get("n", 0)),
        "X-Alaz-Truncated": "1" if info.get("truncated") else "0",
    }
    return PlainTextResponse(text, media_type="text/csv; charset=utf-8", headers=headers)


@app.post("/api/interpret")
def interpret(req: InterpretRequest):
    """ROI dayanak özeti → DeepSeek Türkçe AI bakışı."""
    if not req.polygon or len(req.polygon) < 3:
        raise HTTPException(422, "AI bakışı için poligon gerekli (≥ 3 köşe).")
    try:
        return _st().interpret_roi(
            polygon=req.polygon,
            focus=req.focus,
            section=req.section or "overview",
            question=req.question,
            lang=req.lang or "tr",
            series=req.series or "raw",
            start_year=req.start_year,
            end_year=req.end_year,
            target_year=req.target_year,
        )
    except FileNotFoundError as exc:
        raise HTTPException(503, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc