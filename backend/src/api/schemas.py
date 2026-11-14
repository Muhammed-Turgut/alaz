"""Pydantic request / response models."""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator

Source = Literal["benchmark", "live", "archive"]


class ROIRequest(BaseModel):
    polygon: list[list[float]] = Field(..., description="[[lat, lon], ...] (>= 3 vertices)")
    source: Source = "benchmark"
    region: Optional[str] = None
    start: Optional[str] = None
    end: Optional[str] = None
    days: int = Field(1, ge=1, le=7)
    bbox: Optional[list[float]] = None

    @field_validator("polygon")
    @classmethod
    def _poly(cls, v):
        if len(v) < 3:
            raise ValueError("polygon needs at least 3 vertices")
        for p in v:
            if len(p) != 2 or not (-90 <= p[0] <= 90) or not (-180 <= p[1] <= 180):
                raise ValueError("vertices must be [lat, lon] within valid ranges")
        return v


class DetectionIn(BaseModel):
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    acq_date: str = Field(..., description="YYYY-MM-DD")
    acq_time: int = Field(..., ge=0, le=2359, description="UTC HHMM")
    satellite: str = Field(..., description="Terra | Aqua | SNPP | NOAA20")
    frp: float = Field(..., ge=0)
    scan: Optional[float] = Field(None, gt=0, description="along-scan pixel size, km")
    track: Optional[float] = Field(None, gt=0, description="along-track pixel size, km")
    brightness: Optional[float] = None
    bright_t31: Optional[float] = None
    confidence: Optional[float] = Field(None, ge=0, le=100)


class HarmonizeRequest(BaseModel):
    detections: list[DetectionIn] = Field(..., min_length=1, max_length=2000)


class BriefingRequest(BaseModel):
    source: Source = "benchmark"
    region: Optional[str] = "california"
    start: Optional[str] = None
    end: Optional[str] = None
    polygon: Optional[list[list[float]]] = None
    days: int = Field(1, ge=1, le=7)
    bbox: Optional[list[float]] = None


class AreaScopeRequest(BaseModel):
    """Gerçek FIRMS arşivinden seçilen alan (takvim / seri / kırılma ortak gövde).

    bbox: [west, south, east, north] (NASA / arşiv sırası).
    polygon: [[lat, lon], ...] — verilirse bbox poligondan türetilir.
    """
    polygon: Optional[list[list[float]]] = None
    bbox: Optional[list[float]] = None
    force: bool = False

    @field_validator("polygon")
    @classmethod
    def _poly(cls, v):
        if v is None:
            return v
        if len(v) < 3:
            raise ValueError("polygon needs at least 3 vertices")
        for p in v:
            if len(p) != 2 or not (-90 <= p[0] <= 90) or not (-180 <= p[1] <= 180):
                raise ValueError("vertices must be [lat, lon] within valid ranges")
        return v

    @field_validator("bbox")
    @classmethod
    def _bbox(cls, v):
        if v is None:
            return v
        if len(v) != 4:
            raise ValueError("bbox must be [west, south, east, north]")
        w, s, e, n = v
        if not (-180 <= w < e <= 180 and -90 <= s < n <= 90):
            raise ValueError("bbox out of range")
        return v


class AreaCalendarRequest(AreaScopeRequest):
    series: Literal["harmonized", "raw"] = "harmonized"
    target_year: Optional[int] = Field(None, ge=2000, le=2100)
    month: Optional[int] = Field(None, ge=1, le=12)


class AreaSeriesRequest(AreaScopeRequest):
    freq: Literal["monthly", "annual"] = "monthly"


class InterpretRequest(AreaScopeRequest):
    """DeepSeek AI bakışı — ROI sayı özeti + odak sekmesi + tablo bölümü."""
    focus: Literal["break", "calendar", "model", "briefing"] = "break"
    section: str = Field("overview", max_length=64, description="Tablo/kart kimliği")
    question: Optional[str] = Field(None, max_length=500)


class HealthResponse(BaseModel):
    status: str
    ready: bool
    model: Optional[str] = None
    model_loaded: bool = False
    detections: int = 0
    regions: list[str] = []
    load_seconds: float = 0.0
    firms_key_configured: bool = False
    notes: dict[str, Any] = {}
