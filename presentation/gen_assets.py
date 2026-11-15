#!/usr/bin/env python3
"""Generate clean presentation chart assets from the local Alaz API."""
from __future__ import annotations

import json
import urllib.request
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "assets"
POLY = [[36.55, 31.20], [36.55, 31.75], [37.05, 31.75], [37.05, 31.20], [36.55, 31.20]]


def post(path: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload or {"polygon": POLY, "source": "archive"}).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:8000{path}",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def parse_date(v) -> datetime:
    if isinstance(v, (int, float)):
        return datetime.utcfromtimestamp(v / 1000 if v > 1e12 else v)
    return datetime.strptime(str(v)[:10], "%Y-%m-%d")


def getf(row: dict, *names):
    for n in names:
        if n in row and row[n] is not None:
            return row[n]
    return None


def style(ax):
    ax.set_facecolor("#fff9f2")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    h = post("/api/area-hybrid")
    c = post("/api/area-calendar", {"polygon": POLY, "source": "archive", "series": "hybrid"})

    tc = h.get("transition_chart") or []
    dates, raw, harm = [], [], []
    for row in tc:
        if not isinstance(row, dict):
            continue
        d = getf(row, "date", "t", "month", "period")
        r = getf(row, "raw", "raw_count", "raw_total", "count_raw", "ham")
        hm = getf(
            row,
            "harmonized_blend",
            "harmonized",
            "harm",
            "harmonized_count",
            "hybrid",
            "blend",
        )
        if d is None:
            continue
        dates.append(parse_date(d))
        raw.append(float(r) if r is not None else np.nan)
        harm.append(float(hm) if hm is not None else np.nan)

    fig, ax = plt.subplots(figsize=(11, 5.2), dpi=160, facecolor="#fff9f2")
    style(ax)
    if dates:
        ax.plot(dates, raw, color="#000000", lw=1.6, label="Ham toplam")
        if not all(np.isnan(harm)):
            ax.plot(dates, harm, color="#0b60b0", lw=2.0, label="Harmonize / hibrit")
        ax.axvline(datetime(2012, 1, 20), color="#d6322a", ls="--", lw=1.2, label="2012 VIIRS")
    ax.set_title(
        "Manavgat ROI · yapısal kırılma (hotspot tespiti)",
        loc="left",
        fontsize=13,
        fontweight="bold",
    )
    ax.set_ylabel("Aylık tespit sayısı")
    ax.legend(frameon=False, loc="upper left")
    ax.xaxis.set_major_locator(mdates.YearLocator(2))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    fig.tight_layout()
    fig.savefig(OUT / "03-structural-break.png", bbox_inches="tight")
    plt.close()
    print("saved 03-structural-break.png", "n=", len(dates))

    my = c.get("month_year")
    matrix = years = xlabels = None
    if isinstance(my, dict) and "values" in my:
        matrix = np.array(my["values"], dtype=float)
        years = my.get("years") or my.get("y")
        xlabels = my.get("months") or my.get("x")
    elif isinstance(my, list) and my and isinstance(my[0], dict):
        ys = sorted({int(r.get("year") or r.get("y")) for r in my})
        M = np.full((len(ys), 12), np.nan)
        yi = {y: i for i, y in enumerate(ys)}
        for r in my:
            y = int(r.get("year") or r.get("y"))
            m = int(r.get("month") or r.get("m"))
            v = r.get("value") or r.get("v") or r.get("count") or r.get("density")
            if v is not None and 1 <= m <= 12:
                M[yi[y], m - 1] = float(v)
        matrix, years, xlabels = M, ys, list("OŞMNHTAEKKAA")[:12]
        xlabels = ["O", "Ş", "M", "N", "M", "H", "T", "A", "E", "E", "K", "A"]

    cal = c.get("calendar")
    if matrix is None and isinstance(cal, dict):
        z = cal.get("z") or cal.get("values") or cal.get("grid")
        years = cal.get("years") or cal.get("y")
        if z is not None:
            matrix = np.array(z, dtype=float)

    if matrix is not None:
        data = np.array(matrix, dtype=float)
        vmax = np.nanpercentile(data, 95) if np.isfinite(data).any() else 1.0
        fig, ax = plt.subplots(figsize=(11, 5.4), dpi=160, facecolor="#fff9f2")
        style(ax)
        im = ax.imshow(
            data,
            aspect="auto",
            cmap="YlOrRd",
            vmin=0,
            vmax=max(float(vmax), 1.0),
            interpolation="nearest",
        )
        if years is not None:
            step = max(1, len(years) // 12)
            ticks = list(range(0, len(years), step))
            ax.set_yticks(ticks)
            ax.set_yticklabels([str(years[i]) for i in ticks], fontsize=8)
        if xlabels is not None:
            ax.set_xticks(range(min(len(xlabels), data.shape[1])))
            ax.set_xticklabels(list(xlabels)[: data.shape[1]], fontsize=8)
        elif data.shape[1] == 12:
            ax.set_xticks(range(12))
            ax.set_xticklabels(["O", "Ş", "M", "N", "M", "H", "T", "A", "E", "E", "K", "A"])
        ax.set_title(
            "Manavgat ROI · yanma takvimi (tespit yoğunluğu)",
            loc="left",
            fontsize=13,
            fontweight="bold",
        )
        cbar = fig.colorbar(im, ax=ax, fraction=0.025, pad=0.02)
        cbar.set_label("Yoğunluk")
        ax.set_xlabel("Ay" if data.shape[1] == 12 else "Dönem")
        ax.set_ylabel("Yıl")
        fig.tight_layout()
        fig.savefig(OUT / "04-calendar-stats.png", bbox_inches="tight")
        plt.close()
        print("saved 04-calendar-stats.png", data.shape)
    else:
        dump = {k: (type(v).__name__) for k, v in c.items()}
        print("calendar matrix missing", dump)
        (OUT / "cal_debug.json").write_text(json.dumps(c, ensure_ascii=False)[:12000])

    monthly = h.get("monthly") or []
    fig, ax = plt.subplots(figsize=(11, 5.2), dpi=160, facecolor="#fff9f2")
    style(ax)
    ds, vs = [], []
    for row in monthly:
        if not isinstance(row, dict):
            continue
        d = getf(row, "date", "month", "t")
        v = getf(row, "harmonized_blend", "harmonized", "hybrid", "value", "count")
        if d is None or v is None:
            continue
        ds.append(parse_date(d))
        vs.append(float(v))
    if ds:
        ax.fill_between(ds, vs, color="#0b60b0", alpha=0.35)
        ax.plot(ds, vs, color="#0b60b0", lw=1.5)
        ax.axvline(datetime(2012, 1, 20), color="#d6322a", ls="--", lw=1.1)
        ax.text(datetime(2012, 3, 1), max(vs) * 0.92, "VIIRS", color="#d6322a", fontsize=9)
    ax.set_title(
        "Manavgat / Antalya kıyısı · harmonize aylık aktivite",
        loc="left",
        fontsize=13,
        fontweight="bold",
    )
    ax.set_ylabel("VIIRS-eşdeğer / hibrit sayım")
    meta = h.get("meta") or {}
    area = meta.get("area_km2") or 2728.8
    ax.text(
        0.98,
        0.95,
        f"ROI ~{float(area):,.0f} km²\nGerçek FIRMS arşivi\nDemo: Manavgat kıyısı",
        transform=ax.transAxes,
        ha="right",
        va="top",
        fontsize=10,
        color="#55606b",
        bbox=dict(boxstyle="square,pad=0.4", fc="#f0f0f0", ec="#e7e2dc"),
    )
    fig.tight_layout()
    fig.savefig(OUT / "02-map-roi.png", bbox_inches="tight")
    plt.close()
    print("saved 02-map-roi.png")

    fig, ax = plt.subplots(figsize=(11, 5.0), dpi=160, facecolor="#fff9f2")
    style(ax)
    labels = ["Ham", "Platform\nbaseline", "Harmonize\n(hold-out)"]
    vals = [21.9, 24.9, 4.9]
    colors = ["#000000", "#55606b", "#0b60b0"]
    bars = ax.bar(labels, vals, color=colors, width=0.55)
    ax.set_ylabel("MAE (aylık, inter-sensor)")
    ax.set_title(
        "Doğrulama · Manavgat temporal hold-out (79 ay)",
        loc="left",
        fontsize=13,
        fontweight="bold",
    )
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v + 0.4, f"{v}", ha="center", fontsize=12, fontweight="bold")
    ax.set_ylim(0, 30)
    ax.text(
        0.98,
        0.95,
        "VIIRS = karşılaştırma referansı\n(ground truth değil)",
        transform=ax.transAxes,
        ha="right",
        va="top",
        fontsize=9,
        color="#55606b",
    )
    fig.tight_layout()
    fig.savefig(OUT / "05-model-validation.png", bbox_inches="tight")
    plt.close()
    print("saved 05-model-validation.png")
    print("DONE")


if __name__ == "__main__":
    main()
