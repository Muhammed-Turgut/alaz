"""Ay × yıl matrisi ve bu ay vs tarihsel medyan."""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.analytics.burning_calendar import current_month_vs_median, month_year_matrix


def test_month_year_and_median():
    idx = pd.date_range("2020-01-01", "2023-12-31", freq="D")
    # Her ağustos'ta artan etkinlik
    vals = np.where(idx.month == 8, idx.year - 2019, 0.0)
    s = pd.Series(vals, index=idx)
    my = month_year_matrix(s, 2020, 2023)
    assert my["years"] == [2020, 2021, 2022, 2023]
    assert my["matrix"][0][7] == 31.0   # 2020 ağustos: 1 * 31 gün
    assert my["matrix"][3][7] == 124.0  # 2023: 4 * 31
    vs = current_month_vs_median(s, year=2023, month=8)
    assert vs["available"] is True
    assert vs["month_total"] == 124.0
    assert vs["n_years"] == 3
    assert vs["historical_median"] == 62.0  # 31, 62, 93 → medyan 62
    assert vs["ratio"] == round(124 / 62, 3)
