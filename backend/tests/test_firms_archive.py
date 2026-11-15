"""Arşiv indirme planı: her sensörün her günü tam bir kez kapsanmalı (eksik gün = kayıp tespit, çift gün = çift sayım)."""
from collections import Counter
from datetime import date, timedelta

from src.data.firms_archive import CHUNK_DAYS, PRODUCTS, Chunk, TxLimiter, plan_chunks

TODAY = date(2026, 11, 14)
AVAIL = {
    "MODIS_SP": (date(2000, 11, 1), date(2026, 8, 31)), "MODIS_NRT": (date(2026, 9, 1), date(2026, 11, 14)),
    "VIIRS_SNPP_SP": (date(2012, 1, 20), date(2026, 7, 31)), "VIIRS_SNPP_NRT": (date(2026, 8, 1), date(2026, 11, 2)),
    "VIIRS_NOAA20_SP": (date(2018, 4, 1), date(2026, 7, 31)), "VIIRS_NOAA20_NRT": (date(2026, 8, 1), date(2026, 11, 14)),
    "VIIRS_NOAA21_SP": (date(2024, 1, 1), date(2026, 7, 31)), "VIIRS_NOAA21_NRT": (date(2026, 8, 1), date(2026, 11, 14)),
    "LANDSAT_NRT": (date(2022, 6, 20), date(2026, 11, 14)),   # plana girmemeli
}


def _days(chunks):
    return Counter((sensor, c.start + timedelta(days=i))
                   for sensor, prods in PRODUCTS.items() for c in chunks if c.source in prods for i in range(c.days))


def test_every_day_covered_exactly_once():
    seen = _days(plan_chunks(AVAIL, TODAY))
    assert max(seen.values()) == 1
    for sensor, (sp, nrt) in PRODUCTS.items():
        first, last = AVAIL[sp][0], AVAIL[nrt][1]
        expected = {first + timedelta(days=i) for i in range((last - first).days + 1)}
        assert {d for (s, d) in seen if s == sensor} == expected


def test_chunks_respect_api_limit_and_known_products():
    chunks = plan_chunks(AVAIL, TODAY)
    known = {p for pair in PRODUCTS.values() for p in pair}
    assert all(1 <= c.days <= CHUNK_DAYS and c.source in known for c in chunks)
    assert all(AVAIL[c.source][0] <= c.start and c.end <= AVAIL[c.source][1] for c in chunks)


def test_overlapping_nrt_is_trimmed_and_only_recent_days_are_partial():
    avail = dict(AVAIL, MODIS_NRT=(date(2026, 8, 20), date(2026, 11, 14)))   # NRT arşivle çakışıyor
    chunks = plan_chunks(avail, TODAY)
    assert max(_days(chunks).values()) == 1
    assert min(c.start for c in chunks if c.source == "MODIS_NRT") == date(2026, 9, 1)
    partial = [c for c in chunks if c.partial]
    assert partial and all(c.end >= TODAY - timedelta(days=1) for c in partial)
    assert not any(c.partial for c in chunks if c.end < TODAY - timedelta(days=1))


def test_partial_and_final_files_have_different_names(tmp_path):
    a, b = Chunk("MODIS_NRT", date(2026, 11, 10), 5, True), Chunk("MODIS_NRT", date(2026, 11, 10), 5, False)
    assert a.path(tmp_path) != b.path(tmp_path) and a.path(tmp_path).name.endswith(".partial.csv")


def test_limiter_never_exceeds_budget():
    lim = TxLimiter(budget=30, window_s=0.2)
    for _ in range(9):
        lim.acquire(10)
        assert lim.used <= 30
