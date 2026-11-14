"""Pydantic request / response models."""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator

Source = Literal["benchmark", "live"]


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
