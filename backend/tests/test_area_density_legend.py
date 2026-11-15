"""Archive density legend + year window helpers (no full archive required)."""
from src.engine.service import AetherState


def test_density_legend_bins_scale_with_nmax():
    leg = AetherState._density_legend(100, 500, 5, "2020-01-01", "2024-12-31")
    assert leg["n_max_per_cell"] == 100
    assert leg["n_total"] == 500
    assert len(leg["bins"]) >= 4
    assert "yanmış" in leg["not_tr"].lower() or "yanmış alan" in leg["not_tr"]
    assert "burned" in leg["not_en"].lower()
    assert leg["render"] == "h3_polygon"
    # highest bin reaches n_max
    assert leg["bins"][-1]["max_n"] == 100
    assert leg["bins"][-1]["approx_n_per_cell"] == 100
    assert "sıfır" in leg["not_tr"].lower() or "zero" in leg["not_en"].lower()


def test_density_legend_empty():
    leg = AetherState._density_legend(0, 0, 3, None, None)
    assert leg["bins"][0]["level"] == "none"


def test_density_legend_remote_cap_note():
    leg = AetherState._density_legend(
        10, 50, 10, "2016-01-01", "2026-01-01",
        remote=True, requested_years=15,
    )
    assert "10" in leg["title_en"]
    assert "15" in leg["title_en"]
    assert "remote" in leg["title_en"].lower() or "cap" in leg["title_en"].lower()
