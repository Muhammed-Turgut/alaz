"""Multi-decadal (2001-2026) benchmark archives for four global fire regimes.

IMPORTANT - what this is.  These archives are **physically simulated**, not
downloaded from NASA.  A latent population of fire events (with real-regime
seasonality, interannual variability and a few scripted extreme seasons) is
observed by simulated Terra / Aqua (MODIS) and SNPP / NOAA-20 (VIIRS)
overpasses that use:

  * real orbit schedules incl. Terra/Aqua drift,
  * exact MODIS bow-tie footprint geometry (1x1 km -> ~4.8x2.0 km),
  * the diurnal fire-intensity cycle (14:30 peak),
  * footprint-dependent detection thresholds (sub-pixel dilution),
  * shared cloud obscuration, persistent industrial heat sources.

Because the generating process is known, the benchmark supports **honest
validation**: the simulator also emits a *counterfactual* SNPP record for
2001-2011 (a sensor that did not exist) which is hidden from the harmonizer
and used only to score it.  Nothing in the harmonizer reads the generating
formulae; it learns from matched MODIS/VIIRS footprints exactly as it would
from real FIRMS data.
"""
from __future__ import annotations

import logging
import pickle
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

from src.data.regions import BIOMES, REGIONS, RegionConfig
from src.engine import geometry_h3 as geo
from src.engine.biomes import _hash01, biome_codes
from src.engine.diurnal_normalizer import (
    diurnal_factor,
    lst_to_utc_hours,
    overpass_lst,
)
import h3

log = logging.getLogger("aether.benchmark")

CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "cache"
CACHE_VERSION = "v3"

ARCHIVE_START = np.datetime64("2001-01-01")
ARCHIVE_END = np.datetime64("2026-11-14")

from src.engine.sensors import SENSORS  # noqa: E402

# counterfactual SNPP record before launch -- scoring only
CF_SENSOR = dict(name="SNPP", instrument="VIIRS", start="2001-01-01", end="2012-01-19")

# detection thresholds: threshold(area) = thr0 * (area / area_nadir) ** 0.55  [MW]
THRESH = {"MODIS": dict(thr0=3.0, area0=1.0), "VIIRS": dict(thr0=0.45, area0=0.375 ** 2)}
BIAS = {"MODIS": 0.95, "VIIRS": 1.00}
NOISE_SIGMA = {"MODIS": 0.14, "VIIRS": 0.10}

# per-biome fire-population parameters
BIOME_PARAMS = {
    0: dict(p_median=90.0, p_sigma=1.1, p_elem=2.5),   # Forest
    1: dict(p_median=9.0, p_sigma=0.7, p_elem=1.5),    # Cropland/Stubble
    2: dict(p_median=28.0, p_sigma=0.9, p_elem=2.0),   # Savanna/Grassland
}


@dataclass
class SimConfig:
    start_year: int = 2001
    end_year: int = 2026
    event_scale: float = 0.7
    seed: int = 20260


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _splitmix(x: np.ndarray) -> np.ndarray:
    x = x.astype(np.uint64) + np.uint64(0x9E3779B97F4A7C15)
    z = (x ^ (x >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
    z = (z ^ (z >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
    return z ^ (z >> np.uint64(31))


def _uniform_hash(a: np.ndarray, b: np.ndarray, c: np.ndarray, salt: int) -> np.ndarray:
    key = (
        a.astype(np.uint64) * np.uint64(1_000_003)
        + b.astype(np.uint64) * np.uint64(7919)
        + c.astype(np.uint64) * np.uint64(104729)
        + np.uint64(salt)
    )
    return (_splitmix(key) >> np.uint64(11)).astype(np.float64) / float(1 << 53)


def _date_parts(dates: np.ndarray):
    years = dates.astype("datetime64[Y]").astype(int) + 1970
    doy = (dates - dates.astype("datetime64[Y]").astype("datetime64[D]")).astype(int) + 1
    return years, doy


# --------------------------------------------------------------------------
# latent fire population
# --------------------------------------------------------------------------
def _annual_multipliers(region: RegionConfig, years, rng):
    sigma = region.interannual_sigma
    z = np.zeros(len(years))
    e = rng.normal(size=len(years))
    for i in range(len(years)):
        z[i] = (0.4 * z[i - 1] if i else 0.0) + e[i] * np.sqrt(1 - 0.16)
    m = np.exp(sigma * z - 0.5 * sigma**2)
    m *= np.array([1.0 + 0.004 * (y - 2001) for y in years])  # mild climate trend
    m *= np.array([region.anomaly_years.get(int(y), 1.0) for y in years])
    return m


def _location_pool(region: RegionConfig, rng, n=7000):
    s, w, nn, e = region.bbox
    lat = rng.uniform(s, nn, n)
    lon = rng.uniform(w, e, n)
    bio = biome_codes(lat, lon)
    cells4 = np.array([h3.latlng_to_cell(a, b, 4) for a, b in zip(lat, lon)], dtype=object)
    uniq, inv = np.unique(cells4, return_inverse=True)
    wcell = np.exp(1.0 * norm.ppf(np.clip([_hash01(c) for c in uniq], 1e-4, 1 - 1e-4)))
    return lat, lon, bio, wcell[inv]


def _make_events(region: RegionConfig, cfg: SimConfig, region_idx: int):
    rng = np.random.default_rng([cfg.seed, region_idx, 1])
    years = list(range(cfg.start_year - 1, cfg.end_year + 1))
    mult = _annual_multipliers(region, years, rng)
    plat, plon, pbio, pw = _location_pool(region, rng)

    rows = []
    for y, m in zip(years, mult):
        n = rng.poisson(region.base_events_per_year * cfg.event_scale * m)
        if n == 0:
            continue
        bio = rng.choice(3, size=n, p=np.array(region.biome_mix) / sum(region.biome_mix))
        jan1 = np.datetime64(f"{y}-01-01")
        for b in range(3):
            idx = np.where(bio == b)[0]
            if len(idx) == 0:
                continue
            centre, width = region.season[BIOMES[b]]
            offs = np.floor(rng.normal(centre, width, len(idx))).astype(int)
            cand = np.where(pbio == b)[0]
            if len(cand) == 0:
                cand = np.arange(len(plat))
            pr = pw[cand] / pw[cand].sum()
            pick = rng.choice(cand, size=len(idx), p=pr)
            rows.append(pd.DataFrame({
                "start": jan1 + offs.astype("timedelta64[D]"),
                "biome": b,
                "lat0": plat[pick] + rng.normal(0, 0.02, len(idx)),
                "lon0": plon[pick] + rng.normal(0, 0.02, len(idx)),
            }))
    ev = pd.concat(rows, ignore_index=True)
    nb = len(ev)
    p_med = np.array([BIOME_PARAMS[b]["p_median"] for b in ev["biome"]])
    p_sig = np.array([BIOME_PARAMS[b]["p_sigma"] for b in ev["biome"]])
    p_el = np.array([BIOME_PARAMS[b]["p_elem"] for b in ev["biome"]])
    ev["P"] = np.minimum(rng.lognormal(np.log(p_med), p_sig), 3000.0)
    dur = np.ones(nb, dtype=int)
    f = ev["biome"].to_numpy() == 0
    c = ev["biome"].to_numpy() == 1
    g = ev["biome"].to_numpy() == 2
    dur[f] = 1 + np.minimum(rng.gamma(1.4, 3.5, f.sum()).astype(int), 45)
    dur[c] = 1 + (rng.random(c.sum()) < 0.15)
    dur[g] = 1 + np.minimum(rng.poisson(0.9, g.sum()), 8)
    ev["dur"] = dur
    ev["k"] = np.clip(np.round(ev["P"].to_numpy() / p_el), 1, 250).astype(int)
    ev["static"] = False
    ev["static_kind"] = ""

    # persistent industrial heat sources (always on, no diurnal cycle)
    n_days = int((ARCHIVE_END - ARCHIVE_START).astype(int)) + 1
    st_rows = []
    for s in region.static_sources:
        st_rows.append({
            "start": ARCHIVE_START, "biome": 1, "lat0": s.lat, "lon0": s.lon, "P": s.power_mw,
            "dur": n_days, "k": 2, "static": True, "static_kind": s.kind,
        })
    if st_rows:
        ev = pd.concat([ev, pd.DataFrame(st_rows)], ignore_index=True)
    ev["eid"] = np.arange(len(ev))

    # element layout (offsets in km, normalised power weights)
    k = ev["k"].to_numpy()
    spread = np.where(ev["static"], 0.15, np.maximum(0.18 * np.sqrt(k), 0.10))
    total = int(k.sum())
    ptr = np.concatenate([[0], np.cumsum(k)[:-1]])
    owner = np.repeat(np.arange(nb + len(st_rows)), k)
    el_x = rng.normal(0, 1, total) * spread[owner]
    el_y = rng.normal(0, 1, total) * spread[owner]
    w = rng.lognormal(0, 0.6, total)
    w = w / np.bincount(owner, weights=w)[owner]
    ev = ev.reset_index(drop=True)
    elements = dict(ptr=ptr, x=el_x, y=el_y, w=w)
    return ev, elements


def _event_days(ev: pd.DataFrame) -> pd.DataFrame:
    dur = ev["dur"].to_numpy()
    eid = np.repeat(ev["eid"].to_numpy(), dur)
    cum = np.cumsum(dur) - dur
    d = np.arange(len(eid)) - np.repeat(cum, dur)
    start = np.repeat(ev["start"].to_numpy().astype("datetime64[D]"), dur)
    date = start + d.astype("timedelta64[D]")
    dur_r = np.repeat(dur, dur)
    stat = np.repeat(ev["static"].to_numpy(), dur)
    a = np.where(stat, 1.0, np.sin(np.pi * (d + 0.5) / dur_r) ** 0.8)
    keep = (date >= ARCHIVE_START) & (date <= ARCHIVE_END)
    return pd.DataFrame({"eid": eid[keep], "date": date[keep], "a": a[keep]})


# --------------------------------------------------------------------------
# sensor observation model
# --------------------------------------------------------------------------
def _observe(region: RegionConfig, ev, elements, edays, y, sat, spec, dn, rng, region_idx, sat_idx):
    instr = spec["instrument"]
    s_start = np.datetime64(spec["start"])
    s_end = np.datetime64(spec["end"]) if spec.get("end") else ARCHIVE_END
    y0, y1 = np.datetime64(f"{y}-01-01"), np.datetime64(f"{y + 1}-01-01")
    m = (edays["date"].to_numpy() >= max(y0, s_start)) & (edays["date"].to_numpy() < y1) & \
        (edays["date"].to_numpy() <= s_end)
    if not m.any():
        return None
    ed = edays[m]
    eid = ed["eid"].to_numpy()
    dates = ed["date"].to_numpy()
    di = dates.astype(int)

    # shared cloud obscuration (same for every sensor at the same event/day/pass)
    u = _uniform_hash(eid, di, np.full(len(eid), dn), salt=region_idx * 7919 + 17)
    clear = u >= region.p_cloud
    if not clear.any():
        return None
    eid, dates, a = eid[clear], dates[clear], ed["a"].to_numpy()[clear]
    n_obs = len(eid)

    lat0 = ev["lat0"].to_numpy()[eid]
    lon0 = ev["lon0"].to_numpy()[eid]
    static = ev["static"].to_numpy()[eid]
    yrs, doy = _date_parts(dates)
    year_frac = yrs + (doy - 1) / 365.25

    half = geo.half_swath_km(instr)
    x_ground = rng.uniform(-half, half, n_obs)
    theta = geo.ground_distance_to_scan_angle(x_ground, instr)
    scan, track = geo.pixel_size(instr, theta)
    scan = np.asarray(scan, dtype=float)
    track = np.asarray(track, dtype=float)

    lst_nom = overpass_lst(sat, year_frac, night=bool(dn))
    lst_obs = np.mod(lst_nom + x_ground / (geo.KM_PER_DEG_LON_EQ * np.cos(np.radians(lat0)) * 15.0), 24.0)
    d_obs = np.where(static, 1.0, diurnal_factor(lst_obs))

    k = ev["k"].to_numpy()[eid]
    ptr = elements["ptr"][eid]
    total_pow = ev["P"].to_numpy()[eid]
    row_obs = np.repeat(np.arange(n_obs), k)
    cum = np.cumsum(k) - k
    el_idx = ptr[row_obs] + (np.arange(len(row_obs)) - cum[row_obs])

    ox = rng.uniform(0, 1, n_obs) * scan
    oy = rng.uniform(0, 1, n_obs) * track
    ix = np.floor((elements["x"][el_idx] + ox[row_obs]) / scan[row_obs]).astype(np.int64)
    iy = np.floor((elements["y"][el_idx] + oy[row_obs]) / track[row_obs]).astype(np.int64)
    power = (
        total_pow[row_obs] * elements["w"][el_idx] * a[row_obs] * d_obs[row_obs]
        * rng.lognormal(0, 0.15, len(row_obs))
    )

    key = row_obs.astype(np.int64) * (1 << 20) + (ix + 512) * 1024 + (iy + 512)
    uniq, inv = np.unique(key, return_inverse=True)
    psum = np.bincount(inv, weights=power)
    o = uniq // (1 << 20)
    code = uniq % (1 << 20)
    pix = code // 1024 - 512
    piy = code % 1024 - 512

    area = scan[o] * track[o]
    th = THRESH[instr]
    thr = th["thr0"] * (area / th["area0"]) ** 0.55
    p_det = 1.0 / (1.0 + np.exp(-np.log(np.maximum(psum, 1e-9) / thr) / 0.25))
    det = rng.random(len(o)) < p_det
    if not det.any():
        return None
    o, pix, piy, psum = o[det], pix[det], piy[det], psum[det]

    x_c = (pix + 0.5) * scan[o] - ox[o]
    y_c = (piy + 0.5) * track[o] - oy[o]
    lat = lat0[o] + y_c / geo.KM_PER_DEG_LAT
    lon = lon0[o] + x_c / (geo.KM_PER_DEG_LON_EQ * np.cos(np.radians(lat0[o])))

    frp = psum * BIAS[instr] * rng.lognormal(0, NOISE_SIGMA[instr], len(o))
    a_pix = np.sqrt(scan[o] * track[o])
    t21 = 300.0 + 26.0 * np.log1p(frp / (a_pix * 1.2)) + rng.normal(0, 1.5, len(o))
    t31 = 288.0 + 5.0 * np.log1p(frp / 25.0) + rng.normal(0, 2.0, len(o))
    if instr == "MODIS":
        conf = np.clip(25 + 22 * np.log10(frp + 1) + rng.normal(0, 8, len(o)), 5, 100)
    else:
        conf = np.where((frp < 0.8) & (rng.random(len(o)) < 0.6), 30, np.where(frp > 20, 90, 65))

    lst_pix = lst_obs[o]
    utc_h = lst_to_utc_hours(lst_pix, lon, doy[o])
    ts = dates[o].astype("datetime64[s]") + np.round(utc_h * 3600).astype(np.int64).astype("timedelta64[s]")
    return pd.DataFrame({
        "latitude": lat,
        "longitude": lon,
        "ts": ts.astype("datetime64[ns]"),
        "satellite": sat,
        "instrument": instr,
        "scan": scan[o].astype(np.float32),
        "track": track[o].astype(np.float32),
        "frp": frp.astype(np.float32),
        "bright_t21": t21.astype(np.float32),
        "bright_t31": t31.astype(np.float32),
        "confidence": conf.astype(np.int16),
        "daynight": np.where((lst_pix >= 6) & (lst_pix < 18), "D", "N"),
        "truth_static": static[o],
        "truth_event": eid[o].astype(np.int32),
    })


def generate_region(region_id: str, cfg: SimConfig | None = None, progress=None) -> dict:
    cfg = cfg or SimConfig()
    region = REGIONS[region_id]
    ridx = list(REGIONS).index(region_id)
    ev, elements = _make_events(region, cfg, ridx)
    edays = _event_days(ev)

    main, cf = [], []
    for y in range(cfg.start_year, cfg.end_year + 1):
        for si, (sat, spec) in enumerate(SENSORS.items()):
            for dn in (0, 1):
                rng = np.random.default_rng([cfg.seed, ridx, y, si, dn])
                df = _observe(region, ev, elements, edays, y, sat, spec, dn, rng, ridx, si)
                if df is not None:
                    main.append(df)
        # counterfactual SNPP before it existed (scoring only)
        if y < 2012:
            for dn in (0, 1):
                rng = np.random.default_rng([cfg.seed, ridx, y, 99, dn])
                df = _observe(region, ev, elements, edays, y, "SNPP", CF_SENSOR, dn, rng, ridx, 2)
                if df is not None:
                    cf.append(df)
        if progress:
            progress(region_id, y)

    def _finish(parts):
        if not parts:
            return pd.DataFrame()
        d = pd.concat(parts, ignore_index=True)
        for c in ("satellite", "instrument", "daynight"):
            d[c] = d[c].astype("category")
        return d.sort_values("ts", kind="stable").reset_index(drop=True)

    ev_out = ev.assign(date_start=ev["start"]).drop(columns=["start"])
    return {"df": _finish(main), "cf": _finish(cf), "events": ev_out, "region": region_id,
            "config": cfg.__dict__.copy()}


# --------------------------------------------------------------------------
# cache
# --------------------------------------------------------------------------
def cache_path(region_id: str, cfg: SimConfig) -> Path:
    tag = f"{cfg.start_year}-{cfg.end_year}-s{cfg.event_scale:g}-r{cfg.seed}"
    return CACHE_DIR / f"benchmark_{CACHE_VERSION}_{region_id}_{tag}.pkl"


def load_or_build(region_id: str, cfg: SimConfig | None = None, rebuild: bool = False, progress=None) -> dict:
    cfg = cfg or SimConfig()
    path = cache_path(region_id, cfg)
    if path.exists() and not rebuild:
        with open(path, "rb") as fh:
            return pickle.load(fh)
    log.info("building benchmark archive for %s ...", region_id)
    data = generate_region(region_id, cfg, progress)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        pickle.dump(data, fh, protocol=pickle.HIGHEST_PROTOCOL)
    return data


def load_all(cfg: SimConfig | None = None, rebuild: bool = False, progress=None) -> dict:
    return {rid: load_or_build(rid, cfg, rebuild, progress) for rid in REGIONS}
