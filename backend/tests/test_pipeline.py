"""Core scientific/pipeline checks for AETHER-FIRE (fast, no network)."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.engine import diurnal_normalizer as dn  # noqa: E402
from src.engine import geometry_h3 as g  # noqa: E402
from src.engine import stats_diagnostics as sd  # noqa: E402


# ---------------------------------------------------------------- geometry
def test_modis_bowtie_grows_towards_swath_edge():
    s0, t0 = g.modis_pixel_size(0.0)
    s1, t1 = g.modis_pixel_size(55.0)
    assert s0 == pytest.approx(1.0, abs=0.05)
    assert s1 * t1 > 4.0 * s0 * t0  # >4x area at the edge (bow-tie)


def test_viirs_growth_is_small():
    s0, _ = g.viirs_pixel_size(0.0)
    s1, _ = g.viirs_pixel_size(56.0)
    assert s0 == pytest.approx(0.375, abs=0.01)
    assert s1 < 0.9


def test_h3_roundtrip_and_resolution_areas():
    cells = g.h3_index(np.array([37.0]), np.array([35.0]), 7)
    assert len(cells) == 1
    assert g.cell_area_km2(7) == pytest.approx(5.16, rel=0.05)
    assert g.cell_area_km2(8) < g.cell_area_km2(7)


# ---------------------------------------------------------------- diurnal
def test_lst_matches_longitude_offset():
    ts = pd.Series(pd.to_datetime(["2020-06-21 12:00:00"]))
    lst = dn.utc_to_lst(ts, np.array([30.0]), apparent=False)
    assert lst[0] == pytest.approx(14.0, abs=1e-6)


def test_diurnal_scale_is_bounded_and_identity_at_reference():
    lst = np.array([13.5, 10.0, 22.0, 3.0])
    s = dn.diurnal_scale(lst)
    assert s[0] == pytest.approx(1.0, abs=1e-9)
    assert np.all(s >= dn.PARAMS.clip[0]) and np.all(s <= dn.PARAMS.clip[1])


def test_normalize_frp_preserves_nonnegativity():
    out = dn.normalize_frp(np.array([0.0, 5.0, 120.0]), np.array([9.0, 10.5, 16.0]))
    assert np.all(out >= 0)


# ------------------------------------------------------------------ stats
def test_pettitt_detects_step():
    rng = np.random.default_rng(0)
    x = np.r_[rng.normal(0, 1, 80), rng.normal(4, 1, 80)]
    r = sd.pettitt_test(x)
    assert r["significant"] and abs(r["change_index"] - 80) <= 3


def test_pettitt_no_change_is_not_significant():
    rng = np.random.default_rng(1)
    assert not sd.pettitt_test(rng.normal(0, 1, 200))["significant"]


def test_chow_detects_level_shift_and_not_noise():
    rng = np.random.default_rng(2)
    y = np.exp(np.r_[rng.normal(2, 0.1, 60), rng.normal(3, 0.1, 60)])
    assert sd.chow_test(y, 60)["significant"]
    flat = np.exp(rng.normal(2, 0.1, 120))
    assert not sd.chow_test(flat, 60)["significant"]


def test_zscore_anomaly_scale_floor_prevents_explosions():
    idx = pd.date_range("2015-01-01", periods=365 * 6, freq="D")
    s = pd.Series(100.0, index=idx)  # perfectly flat -> MAD = 0
    s.iloc[-3:] = 130.0
    ep = sd.zscore_anomalies(s, window=7, z_thresh=2.0)
    if len(ep):
        zcol = [c for c in ep.columns if "z" in c.lower()][0]
        assert ep[zcol].abs().max() < 50  # was +600 before the 0.35 floor


# --------------------------------------------------------------- api (opt)
def test_api_health_and_regions():
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from src.api.main import app

    with TestClient(app) as c:
        h = c.get("/api/health").json()
        assert "ready" in h
        if h.get("ready"):
            regs = c.get("/api/regions").json()
            assert len(regs) >= 1
