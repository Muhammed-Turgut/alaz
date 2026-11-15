"""Ön tarama / analiz brifingi (Markdown + JSON) — araştırma prototipi.

Sayılar seçilen veriden gelir. Kural tabanlı metin **operasyonel talimat değildir**;
hava, rüzgâr, yakıt/nem ve yerel müdahale verisi yoktur. Dil: ctx['lang'] tr|en.
"""
from __future__ import annotations

import datetime as dt

import numpy as np

from src.engine.diurnal_normalizer import PARAMS, diurnal_factor, nominal_day_lst


def diurnal_outlook(lon: float, now_utc: dt.datetime | None = None, hours: int = 24, step: int = 3) -> list:
    """AOI boylamında nominal güneş döngüsü vekili (hava tahmini değil)."""
    now = now_utc or dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    out = []
    for h in range(0, hours + 1, step):
        t = now + dt.timedelta(hours=h)
        lst = (t.hour + t.minute / 60.0 + lon / 15.0) % 24.0
        out.append({"utc": t.strftime("%Y-%m-%d %H:%M"), "lst": round(lst, 1),
                    "relative_intensity": round(float(diurnal_factor(lst)), 2)})
    return out


def overpass_schedule(lon: float, year_frac: float = 2026.87) -> list:
    """Nominal geçiş yerel saatlerini (kayma düzeltilmiş) AOI boylamında UTC'ye çevirir."""
    rows = []
    for sat in ("Terra", "Aqua", "SNPP", "NOAA20", "NOAA21"):
        day = float(nominal_day_lst(sat, np.array([year_frac]))[0])
        for label, lst in (("gündüz", day), ("gece", (day + 12.0) % 24.0)):
            utc = (lst - lon / 15.0) % 24.0
            rows.append({"satellite": sat, "pass": label, "local_solar_time": f"{int(lst):02d}:{int((lst % 1) * 60):02d}",
                         "utc": f"{int(utc):02d}:{int((utc % 1) * 60):02d}"})
    return rows


def _alert(z: float | None, en: bool = False) -> tuple[str, str]:
    if z is None:
        return (
            ("UNRATED", "No day-of-year climatology for this scope — screening level not computed")
            if en
            else ("DERECELENDİRİLMEDİ", "Bu kapsam için iklimsel (yılın günü) karşılaştırma yok — tarama seviyesi hesaplanamadı")
        )
    if z >= 3:
        return (
            ("EXTREME", f"harmonized activity z = {z:+.1f} (≥ +3 vs 25-yr DOY climate)")
            if en
            else ("AŞIRI", f"harmonize etkinlik z = {z:+.1f} (≥ +3; 25 yıllık yılın-günü iklimine göre)")
        )
    if z >= 2:
        return (
            ("ELEVATED", f"harmonized activity z = {z:+.1f} (≥ +2)")
            if en
            else ("YÜKSEK", f"harmonize etkinlik z = {z:+.1f} (≥ +2)")
        )
    if z <= -2:
        return (
            ("SUPPRESSED", f"harmonized activity z = {z:+.1f} (≤ −2)")
            if en
            else ("BASKILANMIŞ", f"harmonize etkinlik z = {z:+.1f} (≤ −2)")
        )
    return (
        ("NORMAL", f"harmonized activity z = {z:+.1f} (within ±2)")
        if en
        else ("NORMAL", f"harmonize etkinlik z = {z:+.1f} (±2 içinde)")
    )


def _fmt(v, nd=0):
    return "yok" if v is None else f"{v:,.{nd}f}"


def _is_local_val(val: dict) -> bool:
    """ROI yerel doğrulama mı (hold-out bölgesel değil)?"""
    return bool(val) and "rmse_raw" in val and "holdout_2022_2026" not in val


_AUDIENCE = {
    "acil": {
        "title": "ACİL DURUM ÖN TARAMA ÖZETİ",
        "title_en": "EMERGENCY PRE-SCREEN SUMMARY",
        "who": "Acil müdahale / olay komutanı (analiz desteği)",
        "who_en": "Emergency / incident command (analysis support only)",
        "focus": "sıcak nokta yoğunluğu ve belirsizlik — talimat değil",
        "focus_en": "hotspot intensity & uncertainty — not dispatch orders",
    },
    "erken_uyari": {
        "title": "ERKEN UYARI ÖN TARAMA ÖZETİ",
        "title_en": "EARLY-WARNING PRE-SCREEN SUMMARY",
        "who": "Erken uyarı / izleme",
        "who_en": "Early warning / monitoring",
        "focus": "eşikler, z-skoru ve nominal pencereler",
        "focus_en": "thresholds, z-score, nominal windows",
    },
    "arazi": {
        "title": "ARAZİ YÖNETİMİ ANALİZ ÖZETİ",
        "title_en": "LAND MANAGEMENT ANALYSIS SUMMARY",
        "who": "Arazi / yangın rejimi yönetimi",
        "who_en": "Land / fire-regime management",
        "focus": "sezon, medyan ve çok yıllı karşılaştırma",
        "focus_en": "season, median, multi-year comparison",
    },
    "bilim": {
        "title": "BİLİMSEL ÖZET",
        "title_en": "SCIENTIFIC SUMMARY",
        "who": "Bilim / analitik",
        "who_en": "Science / analytics",
        "focus": "metrikler, harmonizasyon ve doğrulama",
        "focus_en": "metrics, harmonization, validation",
    },
    "itfaiye": {
        "title": "İTFAİYE ÖN TARAMA ÖZETİ",
        "title_en": "FIRE SERVICE PRE-SCREEN SUMMARY",
        "who": "İtfaiye / saha (yalnızca uydu özeti)",
        "who_en": "Fire service / field (satellite summary only)",
        "focus": "yüksek yoğunluklu H3 hücreleri — saha kararı için yeterli değil",
        "focus_en": "high-density H3 cells — not sufficient for field decisions",
    },
}


def generate(ctx: dict) -> dict:
    """ctx: scope, window, totals, top_cells, climate, drivers, quality, validation, centre, audience, lang."""
    en = (ctx.get("lang") or "tr").strip().lower() == "en"
    totals = ctx.get("totals", {}) or {}
    clim = ctx.get("climate") or {}
    z = clim.get("recent_z")
    level, level_reason = _alert(z, en=en)
    lon = (ctx.get("centre") or [0, 0])[1]
    outlook = diurnal_outlook(lon, ctx.get("now_utc"))
    peak = max(outlook, key=lambda r: r["relative_intensity"])
    trough = min(outlook, key=lambda r: r["relative_intensity"])
    sched = overpass_schedule(lon)
    top = ctx.get("top_cells") or []
    drivers = (ctx.get("drivers") or {}).get("frp") or []
    q = ctx.get("quality") or {}
    val = ctx.get("validation") or {}
    historical = bool(q.get("historical"))
    aud_key = (ctx.get("audience") or "acil").strip()
    if aud_key not in _AUDIENCE:
        aud_key = "acil"
    aud = _AUDIENCE[aud_key]
    title = aud["title_en" if en else "title"]
    who = aud["who_en" if en else "who"]
    focus = aud["focus_en" if en else "focus"]

    est, lo, hi = totals.get("harmonized"), totals.get("harmonized_lo90"), totals.get("harmonized_hi90")
    raw = totals.get("raw_total")
    static_n = totals.get("static_removed", 0)
    low_n = totals.get("lowconf_removed", 0)

    actions = []
    if aud_key == "erken_uyari":
        actions.append(
            f"{'Screening level' if en else 'Tarama seviyesi'} **{level}** — {level_reason}."
        )
        if z is not None:
            actions.append(
                f"{'Last 7-day climate z' if en else 'Son 7 gün iklimsel z'} = **{z:+.1f}**; "
                f"{'|z|≥2 → consider denser monitoring (not an alert order).' if en else '|z|≥2 → izlemeyi sıklaştır (uyarı emri değil).'}"
            )
        actions.append(
            f"{'Nominal solar-cycle peak' if en else 'Nominal güneş-döngüsü tepe'} ~{peak['utc']} UTC "
            f"(rel. {peak['relative_intensity']:.2f}); "
            f"{'trough' if en else 'çukur'} ~{trough['utc']} UTC — "
            f"{'not a weather/fuel forecast.' if en else 'hava/yakıt tahmini değil.'}"
        )
    elif aud_key == "arazi":
        actions.append(
            f"{'Raw detections' if en else 'Ham tespit'} **{_fmt(raw)}** · "
            f"{'VIIRS-eq harmonized' if en else 'harmonize VIIRS-eşdeğer'} **{_fmt(est, 1)}** — "
            f"{'compare to season & multi-year median.' if en else 'sezon ve çok yıllı medyan ile karşılaştır.'}"
        )
        if clim:
            actions.append(f"{'Climate z (7d)' if en else 'İklimsel z (son 7 gün)'}: **{clim.get('recent_z')}**.")
        actions.append(
            "Open Burn calendar for month×year and current-month vs median."
            if en
            else "Yanma takvimi sekmesinde ay×yıl ve bu ay vs medyan kartlarını incele."
        )
    elif aud_key == "bilim":
        actions.append(
            f"Raw {_fmt(raw)} / harm {_fmt(est, 1)}; FRP mean raw {_fmt(totals.get('frp_raw_mean'), 1)} MW → "
            f"harm {_fmt(totals.get('frp_harm_mean'), 1)} MW."
        )
        if _is_local_val(val):
            actions.append(
                f"Local MODIS→VIIRS RMSE raw→harm {_fmt(val.get('rmse_raw'), 1)} → {_fmt(val.get('rmse_harmonized'), 1)} "
                f"(n={_fmt(val.get('n'))} mo). VIIRS = reference, not ground truth."
            )
        actions.append(
            "Check Model & Structural break tabs for sensor-transition caveats."
            if en
            else "Model ve doğrulama + yapısal kırılma sekmelerinde sensör geçişi uyarılarını kontrol et."
        )
    elif aud_key == "itfaiye":
        if top:
            actions.append(
                f"**{'Candidate cell (screening)' if en else 'Aday hücre (ön tarama)'}: ** "
                f"hex `{top[0]['h3']}` (~{top[0]['harmonized']:.1f} VIIRS-eq hotspots, "
                f"FRP ~{top[0]['frp_harm_mean']:.1f} MW) — "
                f"{'not a dispatch priority.' if en else 'sevk önceliği değil.'}"
            )
        if len(top) > 1:
            actions.append(
                f"**{'Also elevated (screening)' if en else 'Ayrıca yüksek (ön tarama)'}: ** "
                + ", ".join(f"`{c['h3']}`" for c in top[1:3]) + "."
            )
        actions.append(
            f"{'Nominal intensity peak' if en else 'Nominal yoğunluk tepe'} ~{peak['utc']} UTC; "
            f"{'trough' if en else 'çukur'} ~{trough['utc']} UTC "
            f"({('proxy only' if en else 'yalnızca vekil')})."
        )
        if level in ("AŞIRI", "YÜKSEK", "EXTREME", "ELEVATED"):
            actions.append(
                "Screening level elevated vs climate envelope — verify with weather/fuel/local intel before any resource move."
                if en
                else "Tarama seviyesi iklim zarfının üzerinde — kaynak kaydırmadan önce hava/yakıt/yerel istihbaratla doğrula."
            )
    else:  # acil
        if level in ("AŞIRI", "YÜKSEK", "EXTREME", "ELEVATED"):
            actions.append(
                "Activity above historical envelope on satellite metrics only — not an order to pre-position crews."
                if en
                else "Etkinlik yalnızca uydu metriklerinde tarihsel zarfın üzerinde — ekip öne alma emri değildir."
            )
        if top:
            actions.append(
                f"{'Highest-density H3 for further review' if en else 'İnceleme için en yoğun H3'}: "
                f"`{top[0]['h3']}` (~{top[0]['harmonized']:.1f} VIIRS-eq hotspots)."
            )
        actions.append(
            f"{'Nominal solar trough' if en else 'Nominal güneş çukuru'} ~{trough['utc']} UTC "
            f"(rel. {trough['relative_intensity']:.2f}); "
            f"{'peak' if en else 'tepe'} ~{peak['utc']} UTC "
            f"(rel. {peak['relative_intensity']:.2f}) — "
            f"{'not spread prediction.' if en else 'yayılım tahmini değil.'}"
        )

    if static_n and aud_key in ("acil", "itfaiye", "erken_uyari"):
        actions.append(
            f"**{static_n:,}** {'persistent thermal sources already excluded (flare/facility/volcano).' if en else 'kalıcı kaynak tespiti zaten dışlandı (flare / tesis / volkan).'}"
        )
    if q.get("non_live") and not historical:
        actions.append(
            "Figures are **not live** — refresh when FIRMS connectivity returns."
            if en
            else "Rakamlar **canlı değil**: FIRMS bağlantısı düzelince yenile."
        )
    if historical and aud_key in ("acil", "itfaiye", "erken_uyari"):
        actions.append(
            "This summary is **historical archive** — for near-real-time use Live NASA FIRMS mode."
            if en
            else "Bu özet **tarihsel arşivdir**; anlık izleme için Canlı NASA FIRMS modunu kullan."
        )

    md = []
    md.append(f"# {title} — AETHER-FIRE / Alaz")
    md.append(
        (
            f"**Audience:** {who}  \n"
            f"**Emphasis:** {focus}  \n"
            f"**Scope:** {ctx.get('scope', 'n/a')}  \n"
            f"**Window:** {ctx.get('window', 'n/a')}  \n"
            f"**Generated:** {dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}  \n"
            f"**Data basis:** {q.get('basis', 'n/a')}"
        )
        if en
        else (
            f"**Hedef kitle:** {who}  \n"
            f"**Vurgu:** {focus}  \n"
            f"**Kapsam:** {ctx.get('scope', 'yok')}  \n"
            f"**Pencere:** {ctx.get('window', 'yok')}  \n"
            f"**Üretim:** {dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}  \n"
            f"**Veri dayanağı:** {q.get('basis', 'yok')}"
        )
    )
    md.append(
        "\n> **DISCLAIMER:** Research prototype. Pre-screening / analysis support only — "
        "**not** operational dispatch. Hotspot ≠ fire count ≠ burned area. "
        "FRP is radiative power, not burned area. Harmonization trained on a **synthetic** benchmark; "
        "VIIRS is a **comparison reference**, not absolute ground truth. "
        "No weather, wind, fuel moisture, or local response data."
        if en
        else
        "\n> **UYARI:** Araştırma prototipi. Yalnızca **ön tarama / analiz desteği** — "
        "**operasyonel talimat değildir**. Sıcak nokta ≠ yangın sayısı ≠ yanmış alan. "
        "FRP radyatif güçtür, yanmış alan değildir. Harmonizasyon **sentetik** benchmark’ta eğitildi; "
        "VIIRS **karşılaştırma referansıdır**, mutlak yer-gerçeği değil. "
        "Hava, rüzgâr, yakıt/nem veya yerel müdahale verisi yoktur."
    )
    md.append(
        f"\n## 1. {'Screening level' if en else 'Tarama seviyesi'}: **{level}**\n{level_reason}."
    )

    md.append(f"\n## 2. {'Situation (sensor-harmonized)' if en else 'Durum (sensör-harmonize)'}")
    md.append(
        f"- {'Raw detections' if en else 'Ham tespit'} (MODIS + VIIRS as reported): **{_fmt(raw)}**"
    )
    md.append(
        f"- {'Persistent thermal removed' if en else 'Kalıcı termal kaynak çıkarılan'}: **{_fmt(static_n)}**; "
        f"{'low-confidence removed' if en else 'düşük güven çıkarılan'}: **{_fmt(low_n)}**"
    )
    if lo is not None and hi is not None and (hi - lo) > 1e-6:
        ci_txt = f"{'90% CI' if en else '%90 GA'} {_fmt(lo, 1)} – {_fmt(hi, 1)}"
    else:
        ci_txt = (
            "VIIRS-local reference: no cross-sensor uncertainty band"
            if en
            else "VIIRS-yerel referans: çapraz-sensör belirsizlik yok"
        )
    md.append(
        f"- {'Harmonized VIIRS-equivalent hotspots' if en else 'Harmonize, VIIRS-eşdeğeri sıcak nokta'}: "
        f"**{_fmt(est, 1)}** ({ci_txt})"
    )
    if totals.get("frp_harm_mean") is not None:
        md.append(
            f"- {'Mean harmonized FRP / detection' if en else 'Ortalama harmonize FRP / tespit'}: "
            f"**{_fmt(totals.get('frp_harm_mean'), 1)} MW** "
            f"({'raw mean' if en else 'ham ortalama'} {_fmt(totals.get('frp_raw_mean'), 1)} MW) — "
            f"{'not burned area' if en else 'yanmış alan değildir'}"
        )
    if raw and est:
        md.append(
            f"- Raw / harm ratio: {raw / max(est, 1e-9):.2f}× "
            "(raw sums multi-platform overpasses; harm is single VIIRS-eq scale)"
        )

    md.append(
        f"\n## 3. {'High-density hexes for review' if en else 'İnceleme için yüksek yoğunluklu altıgenler'} "
        f"(H3 res-7, ~5.2 km²)"
    )
    if top:
        md.append("| H3 | Raw | Harm (P50) | 90% CI | Mean FRP (MW) |\n|---|---:|---:|---:|---:|")
        for c in top[:8]:
            md.append(
                f"| `{c['h3']}` | {c['raw_total']} | {c['harmonized']:.1f} | "
                f"{c['lo90']:.1f}–{c['hi90']:.1f} | {c['frp_harm_mean']:.1f} |"
            )
    else:
        md.append("_No active hexes in this scope._" if en else "_Bu kapsamda aktif altıgen yok._")

    md.append(f"\n## 4. {'Nominal timing (proxy)' if en else 'Nominal zamanlama (vekil)'}")
    md.append(
        f"- {'Relative intensity follows a Gaussian solar-cycle proxy peaking at' if en else 'Göreli yoğunluk yerel güneş saatinde'} "
        f"{PARAMS.peak_hour:.1f}h LST. "
        f"{'Next 24h nominal max' if en else 'Önümüzdeki 24 saatte nominal maksimum'} **{peak['utc']} UTC**, "
        f"{'min' if en else 'minimum'} **{trough['utc']} UTC** — "
        f"{'not fire-behavior prediction.' if en else 'yangın davranışı tahmini değil.'}"
    )
    md.append(
        "- Nominal daytime overpasses (UTC): "
        + "; ".join(f"{s['satellite']} {s['pass']} {s['utc']}" for s in sched if s["pass"] == "gündüz")
        + "."
    )

    sec = 5
    if drivers:
        md.append(f"\n## {sec}. {'Why MODIS hotspots were rescaled' if en else 'MODIS sıcak noktaları neden yeniden ölçeklendi'}")
        for d in drivers[:4]:
            md.append(
                f"- **{d['driver']}**: {d['mean_effect_pct']:+.0f}% on harm FRP "
                f"(typical {d['p10']:+.0f}% … {d['p90']:+.0f}%)"
            )
        sec += 1

    if clim:
        md.append(f"\n## {sec}. {'Climate context' if en else 'İklimsel bağlam'}")
        md.append(
            f"- Last 7d harm mean vs 25-yr DOY median: z = {clim.get('recent_z')}."
        )
        for e in (clim.get("episodes") or [])[:3]:
            md.append(
                f"- Anomaly {e['start']} → {e['end']} ({e['days']} d, peak z {e['peak_z']:+.1f})"
            )
        sec += 1

    md.append(
        f"\n## {sec}. {'Pre-screen notes (rule-based, not orders)' if en else 'Ön tarama notları (kural tabanlı, talimat değil)'}"
    )
    md.extend(f"{i + 1}. {a}" for i, a in enumerate(actions))
    sec += 1

    md.append(f"\n## {sec}. {'Confidence & limitations' if en else 'Güven ve sınırlamalar'}")
    if _is_local_val(val):
        red = val.get("rmse_reduction_pct", float("nan"))
        note = (
            ("harm moves toward VIIRS in this ROI" if en else "harmonize bu ROI’de VIIRS’e yaklaştırıyor")
            if isinstance(red, (int, float)) and red > 0
            else ("harm does not improve vs VIIRS here — prefer Raw" if en else "harmonize bu ROI’de VIIRS’e yaklaştırmıyor — Ham seriyi tercih et")
        )
        md.append(
            f"- Local validation (MODIS → VIIRS reference, overlapping months): "
            f"RMSE raw {_fmt(val.get('rmse_raw'), 1)} → harm {_fmt(val.get('rmse_harmonized'), 1)} "
            f"({red:+.0f}%); relative bias raw "
            f"{val.get('relative_bias_raw_pct', float('nan')):+.0f}% → harm "
            f"{val.get('relative_bias_harmonized_pct', float('nan')):+.1f}% "
            f"(n={_fmt(val.get('n'))} mo). {note}. VIIRS ≠ absolute ground truth."
        )
    elif val:
        h = val.get("holdout_2022_2026") or val
        if "rmse_reduction_pct" in h:
            md.append(
                f"- Hold-out (MODIS-derived vs VIIRS reference): RMSE reduction "
                f"{h.get('rmse_reduction_pct', float('nan')):.0f}%, relative bias harm "
                f"{h.get('relative_bias_harmonized_pct', float('nan')):+.1f}% "
                f"(raw {h.get('relative_bias_raw_pct', float('nan')):+.0f}%), "
                f"90% CI coverage {h.get('ci90_coverage', float('nan')):.0%}."
            )
    if q.get("synthetic"):
        md.append(
            "- Benchmark archive is **physically simulated** (known truth), not a NASA observation."
            if en
            else "- Benchmark arşivi **fiziksel olarak simüle** edilmiş kayıttır (bilinen yer gerçeği), NASA gözlemi değildir."
        )
    for n in q.get("notes", []):
        md.append(f"- {n}")
    md.append(
        "- Hotspot ≠ fire count ≠ burned area; FRP ≠ burned area. "
        "p-values on autocorrelated series can look overly significant. "
        "Do not treat this briefing as an operational order."
        if en
        else
        "- Sıcak nokta ≠ yangın sayısı ≠ yanmış alan; FRP ≠ yanmış alan. "
        "Otokorelasyonlu serilerde p-değerleri abartılı görünebilir. "
        "Bu brifingi operasyonel talimat olarak kullanmayın."
    )
    markdown = "\n".join(md)

    payload = {
        "alert": {"level": level, "reason": level_reason},
        "audience": aud_key,
        "lang": "en" if en else "tr",
        "disclaimer": "pre_screen_only",
        "scope": ctx.get("scope"), "window": ctx.get("window"), "totals": totals,
        "priority_cells": top[:8], "diurnal_outlook": outlook, "overpass_schedule": sched,
        "drivers": drivers, "climate": clim, "recommended_actions": actions, "quality": q,
        "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    return {"markdown": markdown, "json": payload}
