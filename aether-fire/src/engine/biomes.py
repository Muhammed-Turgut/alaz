"""Single source of truth for biome assignment.

The same function is used by the benchmark simulator, model training and
inference, so there can be no train/serve encoding mismatch.

Biome class is a deterministic function of the H3 resolution-4 parent cell
(edge ~22 km):
  * inside a benchmark region -> region biome mix,
  * elsewhere                  -> coarse latitude-band prior.

This is an offline *proxy* for a land-cover product (e.g. MCD12Q1); swap
``biome_for_cells`` for a raster lookup to use real land cover.
"""
from __future__ import annotations

import h3
import numpy as np

from src.data.regions import BIOMES, REGIONS

BIOME_INDEX = {b: i for i, b in enumerate(BIOMES)}
BIOME_RES = 4

# (forest, crop, grass) priors by absolute latitude outside benchmark regions
_GLOBAL_PRIORS = [
    (10.0, (0.65, 0.15, 0.20)),   # wet tropics
    (23.0, (0.15, 0.20, 0.65)),   # savanna belt
    (45.0, (0.25, 0.45, 0.30)),   # temperate agriculture
    (90.0, (0.60, 0.20, 0.20)),   # boreal / high latitude
]


def _hash01(cell: str) -> float:
    v = int(cell, 16) & 0xFFFFFFFFFFFFFFFF
    v = (v * 11400714819323198485) & 0xFFFFFFFFFFFFFFFF
    v ^= v >> 29
    v = (v * 0xBF58476D1CE4E5B9) & 0xFFFFFFFFFFFFFFFF
    v ^= v >> 32
    return (v % 1_000_003) / 1_000_003.0


def _probs_for(lat: float, lon: float) -> tuple:
    for r in REGIONS.values():
        s, w, n, e = r.bbox
        if s <= lat <= n and w <= lon <= e:
            return r.biome_mix
    a = abs(lat)
    for limit, probs in _GLOBAL_PRIORS:
        if a <= limit:
            return probs
    return _GLOBAL_PRIORS[-1][1]


def biome_for_cell4(cell4: str) -> int:
    lat, lon = h3.cell_to_latlng(cell4)
    p = _probs_for(lat, lon)
    u = _hash01(cell4)
    if u < p[0]:
        return 0
    if u < p[0] + p[1]:
        return 1
    return 2


def biome_codes(lat, lon) -> np.ndarray:
    """Vectorised biome index (0 Forest, 1 Cropland/Stubble, 2 Savanna/Grassland)."""
    lat = np.asarray(lat, dtype=float)
    lon = np.asarray(lon, dtype=float)
    cells = np.array([h3.latlng_to_cell(a, b, BIOME_RES) for a, b in zip(lat, lon)], dtype=object)
    uniq, inv = np.unique(cells, return_inverse=True)
    codes = np.array([biome_for_cell4(c) for c in uniq], dtype=np.int8)
    return codes[inv]


def biome_codes_from_h3_7(cells7) -> np.ndarray:
    """Biome index from precomputed res-7 cells (int64 or hex str) via res-4 parent."""
    cells7 = np.asarray(cells7)
    uniq7, inv7 = np.unique(cells7, return_inverse=True)
    cache4: dict[str, int] = {}
    out = np.empty(len(uniq7), dtype=np.int8)
    for i, c7 in enumerate(uniq7):
        c7s = h3.int_to_str(int(c7)) if not isinstance(c7, str) else c7
        p4 = h3.cell_to_parent(c7s, BIOME_RES)
        if p4 not in cache4:
            cache4[p4] = biome_for_cell4(p4)
        out[i] = cache4[p4]
    return out[inv7]


def biome_name(code: int) -> str:
    return BIOMES[int(code)]
