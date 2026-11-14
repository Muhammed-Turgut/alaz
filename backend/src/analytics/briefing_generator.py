"""Automated Tactical Incident Commander Briefing (Markdown + JSON).

Every number in the briefing is computed from the analysed data; the only
rule-based content is the alert level and the recommended-actions checklist,
which are explicit threshold rules shown in the text.
"""
from __future__ import annotations

import datetime as dt

import numpy as np

from src.engine.diurnal_normalizer import PARAMS, diurnal_factor, nominal_day_lst


def diurnal_outlook(lon: float, now_utc: dt.datetime | None = None, hours: int = 24, step: int = 3) -> list:
    """Expected relative fire intensity for the next ``hours`` at an AOI longitude."""
    now = now_utc or dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    out = []
    for h in range(0, hours + 1, step):
        t = now + dt.timedelta(hours=h)
        lst = (t.hour + t.minute / 60.0 + lon / 15.0) % 24.0
        out.append({"utc": t.strftime("%Y-%m-%d %H:%M"), "lst": round(lst, 1),
                    "relative_intensity": round(float(diurnal_factor(lst)), 2)})
    return out


def overpass_schedule(lon: float, year_frac: float = 2026.87) -> list:
    """Nominal overpass local times (drift-aware) converted to UTC at the AOI longitude."""
    rows = []
    for sat in ("Terra", "Aqua", "SNPP", "NOAA20"):
        day = float(nominal_day_lst(sat, np.array([year_frac]))[0])
        for label, lst in (("day", day), ("night", (day + 12.0) % 24.0)):
            utc = (lst - lon / 15.0) % 24.0
            rows.append({"satellite": sat, "pass": label, "local_solar_time": f"{int(lst):02d}:{int((lst % 1) * 60):02d}",
                         "utc": f"{int(utc):02d}:{int((utc % 1) * 60):02d}"})
    return rows


def _alert(z: float | None) -> tuple[str, str]:
    if z is None:
        return "UNRATED", "no climatology available for this scope"
    if z >= 3:
        return "EXTREME", f"harmonized activity z = {z:+.1f} (>= +3 vs 25-yr day-of-year climatology)"
    if z >= 2:
        return "ELEVATED", f"harmonized activity z = {z:+.1f} (>= +2)"
    if z <= -2:
        return "SUPPRESSED", f"harmonized activity z = {z:+.1f} (<= -2)"
    return "NORMAL", f"harmonized activity z = {z:+.1f} (within +/-2)"


def _fmt(v, nd=0):
    return "n/a" if v is None else f"{v:,.{nd}f}"


def generate(ctx: dict) -> dict:
    """ctx keys: scope, window, totals, top_cells, climate (optional), drivers, diurnal, quality, validation, centre."""
    totals = ctx.get("totals", {}) or {}
    clim = ctx.get("climate") or {}
    z = clim.get("recent_z")
    level, level_reason = _alert(z)
    lon = (ctx.get("centre") or [0, 0])[1]
    outlook = diurnal_outlook(lon, ctx.get("now_utc"))
    peak = max(outlook, key=lambda r: r["relative_intensity"])
    trough = min(outlook, key=lambda r: r["relative_intensity"])
    sched = overpass_schedule(lon)
    top = ctx.get("top_cells") or []
    drivers = (ctx.get("drivers") or {}).get("frp") or []
    q = ctx.get("quality") or {}
    val = ctx.get("validation") or {}

    est, lo, hi = totals.get("harmonized"), totals.get("harmonized_lo90"), totals.get("harmonized_hi90")
    raw = totals.get("raw_total")
    static_n = totals.get("static_removed", 0)
    low_n = totals.get("lowconf_removed", 0)

    actions = []
    if level in ("EXTREME", "ELEVATED"):
        actions.append("Raise staffing / pre-position resources: activity is above the historical envelope.")
    if static_n:
        actions.append(f"Do **not** dispatch to the {static_n:,} persistent-source detections (flares / plants / volcanoes) - already excluded.")
    if top:
        actions.append(f"Prioritise hex `{top[0]['h3']}` (~{top[0]['harmonized']:.1f} VIIRS-scale hotspots) for first-look imagery.")
    actions.append(f"Plan fireline work for the intensity trough (~{trough['utc']} UTC, relative intensity {trough['relative_intensity']:.2f}); "
                   f"expect peak spread near {peak['utc']} UTC (relative {peak['relative_intensity']:.2f}).")
    if q.get("non_live"):
        actions.append("Treat figures as **non-live**: refresh when FIRMS connectivity is restored.")

    md = []
    md.append(f"# TACTICAL INCIDENT COMMANDER BRIEFING - AETHER-FIRE")
    md.append(f"**Scope:** {ctx.get('scope', 'n/a')}  \n**Window:** {ctx.get('window', 'n/a')}  \n"
              f"**Generated:** {dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}  \n"
              f"**Data basis:** {q.get('basis', 'n/a')}")
    md.append(f"\n## 1. Alert level: **{level}**\n{level_reason}.")
    md.append("\n## 2. Situation (sensor-harmonized)")
    md.append(f"- Raw detections as reported (MODIS + VIIRS): **{_fmt(raw)}**")
    md.append(f"- Persistent thermal sources removed: **{_fmt(static_n)}**; low-confidence removed: **{_fmt(low_n)}**")
    if lo is not None and hi is not None and (hi - lo) > 1e-6:
        ci_txt = f"90% CI {_fmt(lo, 1)} - {_fmt(hi, 1)}"
    else:
        ci_txt = "VIIRS-native reference period: no cross-sensor estimation uncertainty"
    md.append(f"- Harmonized, VIIRS-equivalent hotspot count: **{_fmt(est, 1)}** ({ci_txt})")
    if totals.get("frp_harm_mean") is not None:
        md.append(f"- Mean harmonized FRP per detection: **{_fmt(totals.get('frp_harm_mean'), 1)} MW** "
                  f"(raw mean {_fmt(totals.get('frp_raw_mean'), 1)} MW)")
    if raw and est:
        md.append(f"- Raw-to-harmonized ratio: {raw / max(est, 1e-9):.2f}x "
                  "(raw counts add up every platform's overpasses of the same fires - up to 4 satellites - plus persistent "
                  "and low-confidence pixels; harmonized counts are per single VIIRS-equivalent platform)")
    md.append("\n## 3. Priority hexagons (H3 res-7, ~5.2 km2)")
    if top:
        md.append("| H3 cell | Raw | Harmonized (P50) | 90% CI | Mean FRP (MW) |\n|---|---:|---:|---:|---:|")
        for c in top[:8]:
            md.append(f"| `{c['h3']}` | {c['raw_total']} | {c['harmonized']:.1f} | {c['lo90']:.1f}-{c['hi90']:.1f} | {c['frp_harm_mean']:.1f} |")
    else:
        md.append("_No active hexagons in this scope._")
    md.append("\n## 4. Behaviour & timing")
    md.append(f"- Fire intensity follows a Gaussian diurnal cycle peaking {PARAMS.peak_hour:.1f} h local solar time. "
              f"Over the next 24 h at this longitude the expected maximum is **{peak['utc']} UTC** and the minimum **{trough['utc']} UTC**.")
    md.append("- Nominal overpasses at the AOI (UTC): " + "; ".join(
        f"{s['satellite']} {s['pass']} {s['utc']}" for s in sched if s["pass"] == "day") + ".")
    md.append("- Terra and Aqua are drifting from their 10:30 / 13:30 orbits; their FRP is rescaled to the SNPP reference time before comparison.")
    if drivers:
        md.append("\n## 5. Why MODIS hotspots were rescaled")
        for d in drivers[:4]:
            md.append(f"- **{d['driver']}**: {d['mean_effect_pct']:+.0f}% on harmonized FRP (typical range {d['p10']:+.0f}% to {d['p90']:+.0f}%)")
    if clim:
        md.append("\n## 6. Climatological context")
        md.append(f"- Latest 7-day harmonized mean vs 25-yr median for these days: z = {clim.get('recent_z')}.")
        for e in (clim.get("episodes") or [])[:3]:
            md.append(f"- Anomalous episode {e['start']} to {e['end']} ({e['days']} d, peak z {e['peak_z']:+.1f})")
    md.append("\n## 7. Recommended actions (rule-based)")
    md.extend(f"{i + 1}. {a}" for i, a in enumerate(actions))
    md.append("\n## 8. Confidence & caveats")
    if val:
        h = val.get("holdout_2022_2026") or {}
        md.append(f"- Hold-out validation (MODIS-derived vs VIIRS native): RMSE reduction {h.get('rmse_reduction_pct', float('nan')):.0f}%, "
                  f"relative bias {h.get('relative_bias_harmonized_pct', float('nan')):+.1f}% (raw {h.get('relative_bias_raw_pct', float('nan')):+.0f}%), "
                  f"90% CI coverage {h.get('ci90_coverage', float('nan')):.0%}.")
    if q.get("synthetic"):
        md.append("- The benchmark archive is a **physically simulated** record (known ground truth), not NASA observations.")
    for n in q.get("notes", []):
        md.append(f"- {n}")
    md.append("- Biome classes are a latitude/region proxy for MCD12Q1 land cover; FRP harmonization applies to MODIS-detected footprints only.")
    markdown = "\n".join(md)

    payload = {
        "alert": {"level": level, "reason": level_reason},
        "scope": ctx.get("scope"), "window": ctx.get("window"), "totals": totals,
        "priority_cells": top[:8], "diurnal_outlook": outlook, "overpass_schedule": sched,
        "drivers": drivers, "climate": clim, "recommended_actions": actions, "quality": q,
        "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    return {"markdown": markdown, "json": payload}
