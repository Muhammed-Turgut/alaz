"""Region definitions for the four benchmark fire regimes.

The benchmark archives are *physically simulated* (see ``benchmark_archives``):
fire populations are scenario-driven, sensors are modelled from their real
orbital / footprint geometry, and the latent ground truth is known.  Region
parameters below follow the documented fire-season timing of each regime;
``anomaly_years`` injects well-documented extreme seasons as multipliers so
the anomaly detector has something meaningful to find.
"""
from __future__ import annotations

from dataclasses import dataclass, field

FOREST = "Forest"
CROP = "Cropland/Stubble"
GRASS = "Savanna/Grassland"
BIOMES = [FOREST, CROP, GRASS]


@dataclass(frozen=True)
class StaticSource:
    name: str
    kind: str  # "gas_flare" | "steel_plant" | "volcano"
    lat: float
    lon: float
    power_mw: float


@dataclass(frozen=True)
class RegionConfig:
    id: str
    name: str
    regime: str
    bbox: tuple  # (south, west, north, east)
    # biome shares of ignitions (forest, crop, grass)
    biome_mix: tuple
    # fire season per biome: (centre day-offset from 1 Jan, sigma in days).
    # Offsets may be negative / >365 so seasons can straddle the new year.
    season: dict
    base_events_per_year: int
    interannual_sigma: float
    anomaly_years: dict = field(default_factory=dict)
    static_sources: tuple = ()
    p_cloud: float = 0.22

    @property
    def center(self):
        s, w, n, e = self.bbox
        return ((s + n) / 2, (w + e) / 2)


REGIONS: dict[str, RegionConfig] = {
    "anatolia": RegionConfig(
        id="anatolia",
        name="Mediterranean / Anatolia",
        regime="Summer drought wildfires + wheat-stubble burning",
        bbox=(36.0, 27.0, 41.5, 37.0),
        biome_mix=(0.30, 0.45, 0.25),
        season={FOREST: (212, 30), CROP: (172, 14), GRASS: (200, 35)},
        base_events_per_year=170,
        interannual_sigma=0.25,
        anomaly_years={2021: 3.0, 2025: 1.7},
        static_sources=(
            StaticSource("Eregli steelworks", "steel_plant", 41.28, 31.42, 4.5),
            StaticSource("Karabuk steelworks", "steel_plant", 41.20, 32.62, 3.5),
            StaticSource("Iskenderun steelworks", "steel_plant", 36.70, 36.18, 5.0),
        ),
    ),
    "amazon": RegionConfig(
        id="amazon",
        name="Amazon Basin (arc of deforestation)",
        regime="Dry-season deforestation & pasture fires",
        bbox=(-12.0, -68.0, -2.0, -50.0),
        biome_mix=(0.55, 0.15, 0.30),
        season={FOREST: (245, 28), CROP: (240, 25), GRASS: (235, 30)},
        base_events_per_year=190,
        interannual_sigma=0.28,
        anomaly_years={2005: 1.6, 2010: 1.7, 2019: 1.5, 2024: 1.8},
        static_sources=(
            StaticSource("Urucu gas field flare", "gas_flare", -4.88, -65.27, 15.0),
        ),
        p_cloud=0.30,
    ),
    "california": RegionConfig(
        id="california",
        name="California",
        regime="Mediterranean-climate megafires + rice-stubble burning",
        bbox=(34.0, -123.5, 40.5, -117.5),
        biome_mix=(0.40, 0.15, 0.45),
        season={FOREST: (240, 35), CROP: (285, 20), GRASS: (215, 40)},
        base_events_per_year=150,
        interannual_sigma=0.35,
        anomaly_years={2018: 1.9, 2020: 3.0, 2021: 2.0},
        static_sources=(
            StaticSource("Kern River oil field flare", "gas_flare", 35.43, -119.00, 14.0),
            StaticSource("Midway-Sunset oil field flare", "gas_flare", 35.15, -119.50, 11.0),
            StaticSource("Belridge oil field flare", "gas_flare", 35.45, -119.70, 9.0),
        ),
        p_cloud=0.12,
    ),
    "australia": RegionConfig(
        id="australia",
        name="Southeast Australia",
        regime="Austral-summer bushfires (season straddles new year)",
        bbox=(-39.0, 144.0, -33.0, 151.5),
        biome_mix=(0.55, 0.10, 0.35),
        season={FOREST: (12, 30), CROP: (-35, 20), GRASS: (-5, 32)},
        base_events_per_year=140,
        interannual_sigma=0.30,
        anomaly_years={2020: 5.0, 2009: 2.2},
        static_sources=(
            StaticSource("Longford gas plant flare", "gas_flare", -38.20, 147.10, 9.0),
            StaticSource("Port Kembla steelworks", "steel_plant", -34.47, 150.90, 4.0),
        ),
        p_cloud=0.20,
    ),
}


def get_region(region_id: str) -> RegionConfig:
    if region_id not in REGIONS:
        raise KeyError(f"unknown region '{region_id}', choose from {list(REGIONS)}")
    return REGIONS[region_id]


def region_of_point(lat: float, lon: float) -> str | None:
    for rid, r in REGIONS.items():
        s, w, n, e = r.bbox
        if s <= lat <= n and w <= lon <= e:
            return rid
    return None
