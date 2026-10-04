"""Jurisdiction-neutral evidence model (PRD §6.5, FR-1, FR-4, NFR 5.3).

Every observation from every sensor (satellite, camera, UAV) is normalised
into the same envelope, so downstream analytics never branch on provider.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SourceMeta(BaseModel):
    """Provenance for one fetch from one provider."""
    model_config = ConfigDict(extra="forbid")
    provider: str
    dataset: str
    status: Literal["ok", "partial", "failed", "skipped", "fixture"]
    fetched_at: datetime
    latest_observation: datetime | None = None
    records: int = 0
    native_resolution: str
    expected_refresh: str
    license: str
    attribution: str
    source_uri: str            # never includes credentials
    message: str = ""


class Observation(BaseModel):
    """Normalised metadata envelope for a single sensor observation."""
    model_config = ConfigDict(extra="forbid")
    id: str
    provider: str
    dataset: str
    sensor: str                 # e.g. VIIRS, MODIS, UAV_THERMAL
    platform: str               # e.g. NOAA-20, Aqua, drone-07
    kind: Literal["thermal_anomaly", "smoke", "flame"] = "thermal_anomaly"
    observation_time: datetime
    ingestion_time: datetime
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    native_resolution_m: float = Field(gt=0)
    confidence: float = Field(ge=0, le=1)      # provider confidence mapped to 0..1
    confidence_raw: str
    frp_mw: float | None = Field(default=None, ge=0)
    daynight: Literal["D", "N"] | None = None
    quality: dict[str, Any] = Field(default_factory=dict)
    license: str
    attribution: str
    jurisdiction: str

    @field_validator("observation_time", "ingestion_time")
    @classmethod
    def tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("timestamps must be timezone-aware (UTC)")
        return v

    @staticmethod
    def make_id(*parts: Any) -> str:
        h = hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:12]
        return f"obs-{h}"


class Incident(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    lat: float
    lon: float
    size_acres: float | None = None
    percent_contained: float | None = None
    discovered: datetime | None = None
    updated: datetime | None = None
    kind: str = ""
    provider: str
    perimeter: dict | None = None    # GeoJSON geometry if available
