"""Pixel-footprint geometry (bow-tie) + Uber H3 spatial indexing.

* ``modis_pixel_size`` / ``viirs_pixel_size``: along-scan x along-track ground
  footprint as a function of scan angle.  For MODIS this is the exact
  spherical-Earth slant-range geometry (1.0 x 1.0 km at nadir -> ~4.8 x 2.0 km
  at the swath edge, the "bow-tie" growth).  VIIRS uses an empirical fit to its
  on-board aggregation zones (0.375 km nadir -> ~0.8 km edge).
* ``rect_intersection_km2``: overlap area of two ground footprints in a local
  tangent plane.
* ``match_footprints``: cross-sensor spatial join keyed on **H3 resolution-8
  cells**.  Each VIIRS pixel's unit mass is partitioned among the MODIS
  footprints that cover it, in proportion to geometric intersection, so
  overlapping MODIS pixels (bow-tie overlap at swath edges) never double count.
* H3 helpers: Res-7 (~5.2 km2) for regional aggregation, Res-8 (~0.74 km2)
  for fine event matching.
"""
from __future__ import annotations

import math

import h3
import numpy as np
import pandas as pd
from h3.api import numpy_int as hn  # int64 cell ids: compact + fast joins

R_EARTH_KM = 6371.0088
KM_PER_DEG_LAT = 110.574
KM_PER_DEG_LON_EQ = 111.320

RES_REGIONAL = 7
RES_FINE = 8
# centre-to-centre spacing of neighbouring res-8 cells (~ edge * sqrt(3))
_RES8_SPACING_KM = 0.80

SENSOR_GEOMETRY = {
    "MODIS": dict(alt_km=705.0, nadir_km=1.0, half_swath_deg=55.0),
    "VIIRS": dict(alt_km=824.0, nadir_km=0.375, half_swath_deg=56.28),
}


# --------------------------------------------------------------------------
# Footprint geometry
# --------------------------------------------------------------------------
def _slant_geometry(theta_deg, alt_km):
    th = np.radians(np.asarray(theta_deg, dtype=float))
    r = R_EARTH_KM + alt_km
    sin_phi = np.clip(r * np.sin(th) / R_EARTH_KM, -1.0, 1.0)
    phi = np.arcsin(sin_phi)  # local Earth-incidence angle
    rho = r * np.cos(th) - R_EARTH_KM * np.cos(phi)  # slant range
    return rho, phi


def modis_pixel_size(scan_angle_deg):
    """Return (scan_km, track_km) for MODIS at the given scan angle(s)."""
    g = SENSOR_GEOMETRY["MODIS"]
    rho, phi = _slant_geometry(scan_angle_deg, g["alt_km"])
    ifov = g["nadir_km"] / g["alt_km"]
    track = rho * ifov
    scan = track / np.cos(phi)
    return scan, track


def viirs_pixel_size(scan_angle_deg):
    """Return (scan_km, track_km) for the VIIRS 375 m I-bands.

    On-board pixel aggregation keeps the growth far below the raw
    bow-tie; an empirical cubic fit reproduces 0.375 km at nadir and
    ~0.8 km at the swath edge.
    """
    g = SENSOR_GEOMETRY["VIIRS"]
    x = np.abs(np.asarray(scan_angle_deg, dtype=float)) / g["half_swath_deg"]
    size = g["nadir_km"] + (0.80 - g["nadir_km"]) * np.clip(x, 0, 1.2) ** 3
    return size, size


def pixel_size(instrument: str, scan_angle_deg):
    return modis_pixel_size(scan_angle_deg) if instrument == "MODIS" else viirs_pixel_size(scan_angle_deg)


def footprint_area_km2(scan_km, track_km, shape: str = "rect"):
    """Actual ground footprint area A = scan x track (rect) or pi/4 x scan x track (ellipse)."""
    a = np.asarray(scan_km, dtype=float) * np.asarray(track_km, dtype=float)
    return a * (math.pi / 4.0) if shape == "ellipse" else a


def ground_distance_to_scan_angle(dist_km, instrument: str):
    """Invert cross-track ground distance from nadir -> scan angle (deg), signed."""
    g = SENSOR_GEOMETRY[instrument]
    th = np.linspace(0.0, g["half_swath_deg"] + 2.0, 2000)
    _, phi = _slant_geometry(th, g["alt_km"])
    ground = R_EARTH_KM * (phi - np.radians(th))
    d = np.asarray(dist_km, dtype=float)
    return np.sign(d) * np.interp(np.abs(d), ground, th)


def half_swath_km(instrument: str) -> float:
    g = SENSOR_GEOMETRY[instrument]
    th = np.array([g["half_swath_deg"]])
    _, phi = _slant_geometry(th, g["alt_km"])
    return float(R_EARTH_KM * (phi[0] - np.radians(th[0])))


def bowtie_expansion(scan_km, track_km, instrument: str = "MODIS"):
    """Footprint area relative to the sensor's nadir pixel."""
    nadir = SENSOR_GEOMETRY[instrument]["nadir_km"] ** 2
    return footprint_area_km2(scan_km, track_km) / nadir


def rect_intersection_km2(lat1, lon1, w1, h1, lat2, lon2, w2, h2):
    """Overlap (km2) of two axis-aligned ground footprints.

    Rectangle i is centred on (lat_i, lon_i) with east-west width ``w_i`` (scan)
    and north-south height ``h_i`` (track).  Satellite heading (~8 deg off
    north for sun-synchronous orbits) is ignored.
    """
    lat1 = np.asarray(lat1, dtype=float)
    lat2 = np.asarray(lat2, dtype=float)
    dlon = (np.asarray(lon2, dtype=float) - np.asarray(lon1, dtype=float) + 180.0) % 360.0 - 180.0
    latm = np.radians((lat1 + lat2) / 2.0)
    dx = dlon * KM_PER_DEG_LON_EQ * np.cos(latm)
    dy = (lat2 - lat1) * KM_PER_DEG_LAT
    w1, h1, w2, h2 = (np.asarray(v, dtype=float) for v in (w1, h1, w2, h2))
    ox = np.clip(np.minimum(w1 / 2, dx + w2 / 2) - np.maximum(-w1 / 2, dx - w2 / 2), 0, None)
    oy = np.clip(np.minimum(h1 / 2, dy + h2 / 2) - np.maximum(-h1 / 2, dy - h2 / 2), 0, None)
    return ox * oy


# --------------------------------------------------------------------------
# H3 indexing  (cells are stored as int64; use ``h3_str`` for the hex form)
# --------------------------------------------------------------------------
def h3_str(cell) -> str:
    return h3.int_to_str(int(cell)) if not isinstance(cell, str) else cell


def h3_int(cell) -> int:
    return h3.str_to_int(cell) if isinstance(cell, str) else int(cell)


def h3_index(lat, lon, res: int) -> np.ndarray:
    lat = np.asarray(lat, dtype=float)
    lon = np.asarray(lon, dtype=float)
    return np.array([hn.latlng_to_cell(a, b, res) for a, b in zip(lat, lon)], dtype=np.int64)


def add_h3_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Add ``h3_8`` (fine) and ``h3_7`` (regional) int64 columns."""
    c8 = h3_index(df["latitude"].to_numpy(), df["longitude"].to_numpy(), RES_FINE)
    uniq, inv = np.unique(c8, return_inverse=True)
    parents = np.array([hn.cell_to_parent(int(c), RES_REGIONAL) for c in uniq], dtype=np.int64)
    df = df.copy()
    df["h3_8"] = c8
    df["h3_7"] = parents[inv]
    return df


def cell_area_km2(res: int) -> float:
    return float(h3.average_hexagon_area(res, unit="km^2"))


def cell_boundary(cell):
    """[[lat, lon], ...] ring suitable for Leaflet polygons."""
    return [[a, b] for a, b in h3.cell_to_boundary(h3_str(cell))]


def cell_center(cell):
    return h3.cell_to_latlng(h3_str(cell))


def cells_in_polygon(polygon_latlon, res: int = RES_REGIONAL) -> set:
    """Set of int H3 cells whose centre lies within the polygon [[lat, lon], ...]."""
    poly = h3.LatLngPoly([(float(a), float(b)) for a, b in polygon_latlon])
    return {h3.str_to_int(c) for c in h3.polygon_to_cells(poly, res)}


def aggregate_to_hex(df: pd.DataFrame, value_cols: dict, res_col: str = "h3_7") -> pd.DataFrame:
    """Group by an H3 column using {out_name: (src_col, agg)} specs."""
    return df.groupby(res_col, observed=True).agg(**value_cols).reset_index()


# --------------------------------------------------------------------------
# Cross-sensor footprint matching on H3 keys
# --------------------------------------------------------------------------
def match_footprints(modis: pd.DataFrame, viirs: pd.DataFrame, max_dt_hours: float = 4.5) -> pd.DataFrame:
    """Join MODIS footprints with intersecting VIIRS pixels via H3 res-8 keys.

    Both frames need: latitude, longitude, scan, track, ts, h3_8 (+ ``satellite``
    on MODIS).  Returns one row per intersecting (MODIS, VIIRS) pair with:

        m_pos, v_pos : positional indices into the input frames
        inter_km2    : geometric intersection area
        w            : partition weight in [0, 1] -- the share of the VIIRS
                       pixel's unit mass assigned to this MODIS footprint
        dt_h         : |t_viirs - t_modis| in hours
    """
    if len(modis) == 0 or len(viirs) == 0:
        return pd.DataFrame(columns=["m_pos", "v_pos", "inter_km2", "w", "dt_h"])

    m_cells = modis["h3_8"].to_numpy(dtype=np.int64)
    reach = np.maximum(modis["scan"].to_numpy(), modis["track"].to_numpy()) / 2.0 + 0.45
    ks = np.clip(np.ceil(reach / _RES8_SPACING_KM).astype(int), 1, 5)

    disk_cache: dict = {}
    lens = np.empty(len(modis), dtype=np.int64)
    chunks = []
    for i, (c, k) in enumerate(zip(m_cells, ks)):
        key = (c, int(k))
        d = disk_cache.get(key)
        if d is None:
            d = np.asarray(hn.grid_disk(int(c), int(k)), dtype=np.int64)
            disk_cache[key] = d
        chunks.append(d)
        lens[i] = len(d)
    cand = pd.DataFrame({
        "m_pos": np.repeat(np.arange(len(modis)), lens),
        "h3_8": np.concatenate(chunks),
    })
    m_ts = modis["ts"].to_numpy().astype("datetime64[s]").astype(np.int64)
    v_ts = viirs["ts"].to_numpy().astype("datetime64[s]").astype(np.int64)
    cand["m_day"] = (m_ts // 86400)[cand["m_pos"].to_numpy()]

    v = pd.DataFrame({
        "v_pos": np.arange(len(viirs)),
        "h3_8": viirs["h3_8"].to_numpy(dtype=np.int64),
        "v_day": v_ts // 86400,
    })

    parts = []
    for off in (-1, 0, 1):
        c = cand.assign(v_day=cand["m_day"] + off)
        parts.append(c.merge(v, on=["h3_8", "v_day"], how="inner")[["m_pos", "v_pos"]])
    pairs = pd.concat(parts, ignore_index=True)
    if pairs.empty:
        return pd.DataFrame(columns=["m_pos", "v_pos", "inter_km2", "w", "dt_h"])

    mp = pairs["m_pos"].to_numpy()
    vp = pairs["v_pos"].to_numpy()
    dt_h = np.abs(v_ts[vp] - m_ts[mp]) / 3600.0
    keep = dt_h <= max_dt_hours
    mp, vp, dt_h = mp[keep], vp[keep], dt_h[keep]

    inter = rect_intersection_km2(
        modis["latitude"].to_numpy()[mp], modis["longitude"].to_numpy()[mp],
        modis["scan"].to_numpy()[mp], modis["track"].to_numpy()[mp],
        viirs["latitude"].to_numpy()[vp], viirs["longitude"].to_numpy()[vp],
        viirs["scan"].to_numpy()[vp], viirs["track"].to_numpy()[vp],
    )
    ok = inter > 0
    out = pd.DataFrame({"m_pos": mp[ok], "v_pos": vp[ok], "inter_km2": inter[ok], "dt_h": dt_h[ok]})
    if out.empty:
        out["w"] = []
        return out

    # partition each VIIRS pixel among overlapping MODIS footprints of the same satellite
    out["m_sat"] = modis["satellite"].astype(str).to_numpy()[out["m_pos"].to_numpy()]
    v_area = footprint_area_km2(viirs["scan"].to_numpy(), viirs["track"].to_numpy())[out["v_pos"].to_numpy()]
    grp = out.groupby(["v_pos", "m_sat"], sort=False)["inter_km2"]
    total = grp.transform("sum").to_numpy()
    coverage = np.minimum(1.0, total / v_area)
    out["w"] = coverage * out["inter_km2"].to_numpy() / total
    return out.drop(columns=["m_sat"]).reset_index(drop=True)
