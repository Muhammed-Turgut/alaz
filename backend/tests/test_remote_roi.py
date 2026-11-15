"""Remote ROI helpers: window clip, row-cap balance, partial semantics, SNPP products."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from src.data.remote_roi import (
    REMOTE_MAX_YEARS,
    REMOTE_PRODUCTS,
    REMOTE_ROW_CAP,
    clip_availability,
    load_cached_parts,
    _balanced_newest_first,
    _index_cached_csvs,
)


def test_clip_availability_intersects_window():
    avail = {
        "MODIS_SP": (date(2000, 11, 1), date(2024, 6, 1)),
        "VIIRS_NOAA20_NRT": (date(2024, 1, 1), date(2026, 11, 1)),
    }
    out = clip_availability(avail, date(2023, 1, 1), date(2025, 1, 1))
    assert out["MODIS_SP"] == (date(2023, 1, 1), date(2024, 6, 1))
    assert out["VIIRS_NOAA20_NRT"] == (date(2024, 1, 1), date(2025, 1, 1))


def test_clip_availability_empty_when_no_overlap():
    avail = {"MODIS_SP": (date(2000, 1, 1), date(2010, 1, 1))}
    assert clip_availability(avail, date(2020, 1, 1), date(2024, 1, 1)) == {}


def test_remote_max_years_cap():
    from src.data.remote_roi import REMOTE_DEFAULT_YEARS
    assert REMOTE_MAX_YEARS == 10
    assert 1 <= REMOTE_DEFAULT_YEARS <= REMOTE_MAX_YEARS


def test_remote_products_include_historical_snpp():
    assert "VIIRS_SNPP" in REMOTE_PRODUCTS
    assert REMOTE_PRODUCTS["VIIRS_SNPP"] == ("VIIRS_SNPP_SP", "VIIRS_SNPP_NRT")
    known = {p for pair in REMOTE_PRODUCTS.values() for p in pair}
    assert "VIIRS_SNPP_SP" in known
    assert "MODIS_SP" in known
    assert "VIIRS_NOAA21_NRT" in known


def _write_mini_csv(path: Path, n: int, source_tag: str = "T") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = ["latitude,longitude,brightness,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_t31,frp,daynight"]
    for i in range(n):
        rows.append(
            f"37.{i:04d},38.{i:04d},330.0,1.0,1.0,2024-06-01,1200,{source_tag},MODIS,80,6.1NRT,290.0,5.0,D"
        )
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def test_balanced_load_keeps_both_families_under_row_cap(tmp_path):
    """Alphabetical path order would load all MODIS first; balanced must keep VIIRS."""
    raw = tmp_path / "raw"
    # Many MODIS files (alphabetically first) + one VIIRS file
    for i, day in enumerate(["2020-01-01", "2020-01-06", "2020-01-11", "2021-01-01", "2022-01-01"]):
        _write_mini_csv(raw / "MODIS_SP" / day[:4] / f"{day}_5d.csv", 40)
    _write_mini_csv(
        raw / "VIIRS_NOAA20_SP" / "2023" / "2023-06-01_5d.csv",
        30,
        source_tag="N20",
    )
    parts, meta = load_cached_parts(raw, row_cap=80)
    assert meta["truncated"] is True
    assert meta["rows_loaded"] <= 80
    families = set(meta["product_families_loaded"])
    assert "MODIS" in families
    assert "VIIRS_NOAA20" in families
    assert meta["truncation_reason"]


def test_balanced_newest_first_not_alphabetical():
    indexed = [
        (date(2020, 1, 1), "MODIS_SP", Path("a")),
        (date(2023, 1, 1), "VIIRS_NOAA20_SP", Path("b")),
        (date(2021, 1, 1), "MODIS_SP", Path("c")),
    ]
    ordered = _balanced_newest_first(indexed)
    assert ordered[0][1] == "VIIRS_NOAA20_SP"
    assert ordered[0][0] == date(2023, 1, 1)


def test_partial_false_when_all_chunks_cached_success(tmp_path, monkeypatch):
    from src.data import remote_roi as rmod
    from src.data.remote_roi import RemoteRoiClient

    today = date(2026, 11, 14)
    monkeypatch.setattr(rmod, "_today", lambda: today)
    monkeypatch.setattr(rmod, "CACHE", tmp_path / "remote_roi")

    client = RemoteRoiClient(map_key="dummy")
    monkeypatch.setattr(client, "availability", lambda: {
        "MODIS_SP": (date(2026, 10, 1), date(2026, 10, 20)),
        "MODIS_NRT": (date(2026, 10, 21), date(2026, 11, 14)),
    })

    def fake_fetch_chunk(c, area_arg, dest, limiter):
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            _write_mini_csv(dest, 2)
        return dest

    monkeypatch.setattr(client, "_fetch_chunk", fake_fetch_chunk)
    df, meta = client.fetch(
        west=28.0, south=36.0, east=28.5, north=36.5,
        years=1, requested_years=15,
    )
    assert meta["requested_years"] == 15
    assert meta["effective_years"] == 1
    assert meta["history_years_cap"] == REMOTE_MAX_YEARS
    # Successful first download alone must NOT mark partial
    assert meta["failed_chunks"] == 0
    assert meta["partial"] is False
    # Second call: all from cache
    df2, meta2 = client.fetch(west=28.0, south=36.0, east=28.5, north=36.5, years=1)
    assert meta2["failed_chunks"] == 0
    assert meta2["chunks_fetched_now"] == 0
    assert meta2["chunks_from_cache"] >= 1
    assert meta2["partial"] is False
    assert meta2.get("truncated") is False


def test_partial_true_on_failed_chunk(tmp_path, monkeypatch):
    from src.data import remote_roi as rmod
    from src.data.remote_roi import RemoteRoiClient

    today = date(2026, 11, 14)
    monkeypatch.setattr(rmod, "_today", lambda: today)
    monkeypatch.setattr(rmod, "CACHE", tmp_path / "remote_roi")

    client = RemoteRoiClient(map_key="dummy")
    monkeypatch.setattr(client, "availability", lambda: {
        "MODIS_SP": (date(2026, 10, 1), date(2026, 10, 10)),
    })

    def boom(c, area_arg, dest, limiter):
        raise RuntimeError("network down")

    monkeypatch.setattr(client, "_fetch_chunk", boom)
    df, meta = client.fetch(west=10.0, south=10.0, east=10.5, north=10.5, years=1)
    assert meta["partial"] is True
    assert meta["failed_chunks"] >= 1
    assert meta["done_chunks"] + meta["failed_chunks"] == meta["total_chunks"]


def test_requested_15_effective_capped_at_10(tmp_path, monkeypatch):
    from src.data import remote_roi as rmod
    from src.data.remote_roi import RemoteRoiClient

    today = date(2026, 11, 14)
    monkeypatch.setattr(rmod, "_today", lambda: today)
    monkeypatch.setattr(rmod, "CACHE", tmp_path / "remote_roi")

    client = RemoteRoiClient(map_key="dummy")
    monkeypatch.setattr(client, "availability", lambda: {
        "MODIS_SP": (date(2015, 1, 1), date(2026, 11, 1)),
    })

    def fake_fetch_chunk(c, area_arg, dest, limiter):
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            _write_mini_csv(dest, 1)
        return dest

    monkeypatch.setattr(client, "_fetch_chunk", fake_fetch_chunk)
    _, meta = client.fetch(
        west=1.0, south=1.0, east=1.2, north=1.2,
        years=15, requested_years=15,
    )
    assert meta["requested_years"] == 15
    assert meta["effective_years"] == REMOTE_MAX_YEARS
    assert meta["history_years"] == REMOTE_MAX_YEARS


def test_snpp_in_chunk_plan_when_available(monkeypatch):
    from src.data.remote_roi import RemoteRoiClient
    from src.data.firms_archive import plan_chunks

    today = date(2026, 11, 14)
    avail = {
        "MODIS_SP": (date(2016, 1, 1), date(2026, 8, 31)),
        "VIIRS_SNPP_SP": (date(2012, 1, 20), date(2026, 7, 31)),
        "VIIRS_NOAA20_SP": (date(2018, 4, 1), date(2026, 7, 31)),
    }
    # Mirror remote filter
    known = {p for pair in REMOTE_PRODUCTS.values() for p in pair}
    clipped = {k: v for k, v in avail.items() if k in known}
    chunks = plan_chunks(clipped, today)
    sources = {c.source for c in chunks}
    assert "VIIRS_SNPP_SP" in sources
    assert "VIIRS_NOAA20_SP" in sources


def test_load_area_routes_outside_tr_to_remote(tmp_path, monkeypatch):
    """Outside local coverage → RemoteRoiClient.fetch, not ArchiveStore.query."""
    import src.data.archive_store as amod
    from src.data.archive_store import ArchiveStore
    from src.data.remote_roi import RemoteRoiClient
    from src.engine.service import AetherState

    monkeypatch.setattr(amod, "ARCHIVE_DIR", tmp_path)
    root = tmp_path / "tr"
    root.mkdir()
    (root / "area.json").write_text(
        '{"id":"tr","name":"Türkiye","bbox":[25.5,35.8,45.0,42.3],'
        '"bbox_order":"west,south,east,north"}'
    )
    (root / "progress.json").write_text(
        '{"state":"ready","done_chunks":10,"total_chunks":10}'
    )
    (root / "raw").mkdir()

    called = {"remote": 0, "local": 0}

    def fake_fetch(self, **kwargs):
        called["remote"] += 1
        assert kwargs["west"] < 25.5  # west of Turkey
        return pd.DataFrame(), {
            "empty_reason": "no_detections",
            "data_tier": "remote-firms-api",
            "rows": 0,
            "note": "test",
            "local_archive": False,
            "effective_years": 5,
            "requested_years": kwargs.get("requested_years", 5),
        }

    def fake_query(self, **kwargs):
        called["local"] += 1
        raise AssertionError("local query must not run for outside-TR ROI")

    monkeypatch.setattr(RemoteRoiClient, "fetch", fake_fetch)
    monkeypatch.setattr(ArchiveStore, "query", fake_query)

    st = AetherState.__new__(AetherState)
    st.archive = ArchiveStore("tr")
    st.remote = RemoteRoiClient(map_key="dummy")
    st._area_cache = None
    st.model = type("M", (), {"version": "test"})()
    st.catalog_cells = set()
    st.kappa = 1.0

    poly = [[37.7, -122.5], [37.8, -122.5], [37.8, -122.4], [37.7, -122.4]]
    df, daily, meta = AetherState._load_area(st, polygon=poly, history_years=5)
    assert called["remote"] == 1
    assert called["local"] == 0
    assert df.empty and daily.empty
    assert meta.get("data_tier") == "remote-firms-api"


def test_area_density_remote_years_15_effective_10(monkeypatch):
    """Requested 15y on remote ROI → response/legend use effective ≤10."""
    from src.engine.service import AetherState

    st = AetherState.__new__(AetherState)
    st._area_cache = None

    def fake_load(self, **kwargs):
        assert kwargs.get("history_years") == 15
        ts = pd.date_range("2016-01-01", periods=50, freq="30D")
        df = pd.DataFrame({
            "ts": ts,
            "latitude": 37.0,
            "longitude": -122.0,
            "instrument": "VIIRS",
            "satellite": "NOAA20",
            "frp": 5.0,
            "conf_ok": True,
            "is_static": False,
            "h3_7": 1,
        })
        meta = {
            "local_archive": False,
            "data_tier": "remote-firms-api",
            "effective_years": 10,
            "history_years": 10,
            "history_years_cap": 10,
            "requested_years": 15,
            "partial": False,
        }
        return df, pd.DataFrame(), meta

    monkeypatch.setattr(AetherState, "_load_area", fake_load)
    monkeypatch.setattr(
        "src.engine.geometry_h3.cell_center",
        lambda c: (37.0, -122.0),
    )
    monkeypatch.setattr(
        "src.engine.service._boundary",
        lambda c: [[37.0, -122.1], [37.1, -122.1], [37.1, -122.0], [37.0, -122.0]],
    )
    monkeypatch.setattr("src.engine.service._hexstr", lambda c: "abc")
    monkeypatch.setattr("src.engine.geometry_h3.h3_str", lambda c: "abc")
    monkeypatch.setattr("src.engine.geometry_h3.cell_area_km2", lambda r: 5.2)

    out = AetherState.area_density(st, polygon=[[37, -122], [37.1, -122], [37.1, -121.9]], years=15)
    assert out["requested_years"] == 15
    assert out["effective_years"] == 10
    assert out["years"] == 10
    assert "10" in out["legend"]["title_en"]
    assert "15" in (out.get("year_note") or out["legend"]["title_en"])
    assert out["render"] == "h3_polygon"
    assert out["points"] and "boundary" in out["points"][0]
