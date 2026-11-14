"""Taktik olay komutanı brifingi (Markdown + JSON) — Türkçe.

Her sayı analiz edilen veriden gelir; kural tabanlı içerik yalnızca uyarı seviyesi
ve önerilen eylem listesidir (eşikler metinde açıkça yazılır).
"""
from __future__ import annotations

import datetime as dt

import numpy as np

from src.engine.diurnal_normalizer import PARAMS, diurnal_factor, nominal_day_lst


def diurnal_outlook(lon: float, now_utc: dt.datetime | None = None, hours: int = 24, step: int = 3) -> list:
    """AOI boylamında önümüzdeki ``hours`` saat için göreli yangın yoğunluğu."""
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
    for sat in ("Terra", "Aqua", "SNPP", "NOAA20"):
        day = float(nominal_day_lst(sat, np.array([year_frac]))[0])
        for label, lst in (("gündüz", day), ("gece", (day + 12.0) % 24.0)):
            utc = (lst - lon / 15.0) % 24.0
            rows.append({"satellite": sat, "pass": label, "local_solar_time": f"{int(lst):02d}:{int((lst % 1) * 60):02d}",
                         "utc": f"{int(utc):02d}:{int((utc % 1) * 60):02d}"})
    return rows


def _alert(z: float | None) -> tuple[str, str]:
    if z is None:
        return "DERECELENDİRİLMEDİ", "Bu kapsam için iklimsel (yılın günü) karşılaştırma yok — uyarı seviyesi hesaplanamadı"
    if z >= 3:
        return "AŞIRI", f"harmonize etkinlik z = {z:+.1f} (≥ +3; 25 yıllık yılın-günü iklimine göre)"
    if z >= 2:
        return "YÜKSEK", f"harmonize etkinlik z = {z:+.1f} (≥ +2)"
    if z <= -2:
        return "BASKILANMIŞ", f"harmonize etkinlik z = {z:+.1f} (≤ −2)"
    return "NORMAL", f"harmonize etkinlik z = {z:+.1f} (±2 içinde)"


def _fmt(v, nd=0):
    return "yok" if v is None else f"{v:,.{nd}f}"


def _is_local_val(val: dict) -> bool:
    """ROI yerel doğrulama mı (hold-out bölgesel değil)?"""
    return bool(val) and "rmse_raw" in val and "holdout_2022_2026" not in val


_AUDIENCE = {
    "acil": {
        "title": "ACİL MÜDAHALE BRİFİNGİ",
        "who": "Acil müdahale / olay komutanı",
        "focus": "operasyonel öncelik ve kaynak",
    },
    "erken_uyari": {
        "title": "ERKEN UYARI BRİFİNGİ",
        "who": "Erken uyarı / izleme",
        "focus": "eşikler, z-skoru ve kritik pencereler",
    },
    "arazi": {
        "title": "ARAZİ YÖNETİMİ BRİFİNGİ",
        "who": "Arazi / yangın rejimi yönetimi",
        "focus": "sezon, medyan ve çok yıllı karşılaştırma",
    },
    "bilim": {
        "title": "BİLİMSEL ÖZET BRİFİNGİ",
        "who": "Bilim / analitik",
        "focus": "metrikler, harmonizasyon ve doğrulama",
    },
    "itfaiye": {
        "title": "İTFAİYE SAHA BRİFİNGİ",
        "who": "İtfaiye / saha ekipleri",
        "focus": "kısa öncelik listesi ve konum",
    },
}


def generate(ctx: dict) -> dict:
    """ctx: scope, window, totals, top_cells, climate, drivers, quality, validation, centre, audience."""
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
    historical = bool(q.get("historical"))
    aud_key = (ctx.get("audience") or "acil").strip()
    if aud_key not in _AUDIENCE:
        aud_key = "acil"
    aud = _AUDIENCE[aud_key]

    est, lo, hi = totals.get("harmonized"), totals.get("harmonized_lo90"), totals.get("harmonized_hi90")
    raw = totals.get("raw_total")
    static_n = totals.get("static_removed", 0)
    low_n = totals.get("lowconf_removed", 0)

    actions = []
    if aud_key == "erken_uyari":
        actions.append(f"Uyarı seviyesi **{level}** — {level_reason}.")
        if z is not None:
            actions.append(f"Son 7 gün iklimsel z = **{z:+.1f}**; |z|≥2 ise izleme yoğunlaştır.")
        actions.append(
            f"Tepe yayılım ~{peak['utc']} UTC (göreli {peak['relative_intensity']:.2f}); "
            f"çukur ~{trough['utc']} UTC."
        )
    elif aud_key == "arazi":
        actions.append(
            f"Ham tespit **{_fmt(raw)}** · harmonize VIIRS-eşdeğeri **{_fmt(est, 1)}** — "
            "sezon ve çok yıllı medyan ile karşılaştır."
        )
        if clim:
            actions.append(f"İklimsel z (son 7 gün): **{clim.get('recent_z')}**.")
        actions.append("Yanma takvimi sekmesinde ay×yıl ve bu ay vs medyan kartlarını incele.")
    elif aud_key == "bilim":
        actions.append(
            f"Ham {_fmt(raw)} / harmonize {_fmt(est, 1)}; FRP ort. ham {_fmt(totals.get('frp_raw_mean'), 1)} MW → "
            f"harmonize {_fmt(totals.get('frp_harm_mean'), 1)} MW."
        )
        if _is_local_val(val):
            actions.append(
                f"Yerel doğrulama RMSE ham→harmonize {_fmt(val.get('rmse_raw'), 1)} → {_fmt(val.get('rmse_harmonized'), 1)} "
                f"(n={_fmt(val.get('n'))} ay)."
            )
        actions.append("Model ve doğrulama + yapısal kırılma sekmelerinde sensör geçişini kontrol et.")
    elif aud_key == "itfaiye":
        if top:
            actions.append(
                f"**Öncelik 1:** hex `{top[0]['h3']}` (~{top[0]['harmonized']:.1f} VIIRS-eşd. nokta, "
                f"FRP ~{top[0]['frp_harm_mean']:.1f} MW)."
            )
        if len(top) > 1:
            actions.append(
                f"**Öncelik 2–3:** " + ", ".join(f"`{c['h3']}`" for c in top[1:3]) + "."
            )
        actions.append(
            f"Yoğunluk tepe ~{peak['utc']} UTC; hat için çukur ~{trough['utc']} UTC."
        )
        if level in ("AŞIRI", "YÜKSEK"):
            actions.append("Seviye yüksek — takviye ve su ikmali için erken plan yap.")
    else:  # acil
        if level in ("AŞIRI", "YÜKSEK", "EXTREME", "ELEVATED"):
            actions.append("Personel / kaynakları öne al: etkinlik tarihsel zarfın üzerinde.")
        if top:
            actions.append(
                f"İlk bakış görüntüsü için hex `{top[0]['h3']}` (~{top[0]['harmonized']:.1f} VIIRS-ölçekli sıcak nokta) önceliklendir."
            )
        actions.append(
            f"Yoğunluk çukuru (~{trough['utc']} UTC, göreli {trough['relative_intensity']:.2f}) için hat planla; "
            f"tepe yayılım ~{peak['utc']} UTC (göreli {peak['relative_intensity']:.2f}) beklenir."
        )

    if static_n and aud_key in ("acil", "itfaiye", "erken_uyari"):
        actions.append(
            f"**{static_n:,}** kalıcı kaynak tespitine (flare / tesis / volkan) müdahale etme — zaten dışlandı."
        )
    if q.get("non_live") and not historical:
        actions.append("Rakamlar **canlı değil**: FIRMS bağlantısı düzelince yenile.")
    if historical and aud_key in ("acil", "itfaiye", "erken_uyari"):
        actions.append("Bu brifing **tarihsel arşiv** özetidir; anlık operasyon için Canlı NASA FIRMS modunu kullan.")

    md = []
    md.append(f"# {aud['title']} — AETHER-FIRE / Alaz")
    md.append(
        f"**Hedef kitle:** {aud['who']}  \n"
        f"**Vurgu:** {aud['focus']}  \n"
        f"**Kapsam:** {ctx.get('scope', 'yok')}  \n"
        f"**Pencere:** {ctx.get('window', 'yok')}  \n"
        f"**Üretim:** {dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}  \n"
        f"**Veri dayanağı:** {q.get('basis', 'yok')}"
    )
    md.append(f"\n## 1. Uyarı seviyesi: **{level}**\n{level_reason}.")

    md.append("\n## 2. Durum (sensör-harmonize)")
    md.append(f"- Ham tespit (MODIS + VIIRS, raporlandığı gibi): **{_fmt(raw)}**")
    md.append(f"- Kalıcı termal kaynak çıkarılan: **{_fmt(static_n)}**; düşük güven çıkarılan: **{_fmt(low_n)}**")
    if lo is not None and hi is not None and (hi - lo) > 1e-6:
        ci_txt = f"%90 GA {_fmt(lo, 1)} – {_fmt(hi, 1)}"
    else:
        ci_txt = "VIIRS-yerel referans: çapraz-sensör belirsizlik yok"
    md.append(f"- Harmonize, VIIRS-eşdeğeri sıcak nokta: **{_fmt(est, 1)}** ({ci_txt})")
    if totals.get("frp_harm_mean") is not None:
        md.append(
            f"- Ortalama harmonize FRP / tespit: **{_fmt(totals.get('frp_harm_mean'), 1)} MW** "
            f"(ham ortalama {_fmt(totals.get('frp_raw_mean'), 1)} MW)"
        )
    if raw and est:
        md.append(
            f"- Ham / harmonize oranı: {raw / max(est, 1e-9):.2f}× "
            "(ham sayım tüm platform geçişlerini toplar — en fazla 4 uydu — artı kalıcı ve düşük güven pikseller; "
            "harmonize tek VIIRS-eşdeğeri platforma göredir)"
        )

    md.append("\n## 3. Öncelikli altıgenler (H3 res-7, ~5,2 km²)")
    if top:
        md.append("| H3 hücre | Ham | Harmonize (P50) | %90 GA | Ort. FRP (MW) |\n|---|---:|---:|---:|---:|")
        for c in top[:8]:
            md.append(
                f"| `{c['h3']}` | {c['raw_total']} | {c['harmonized']:.1f} | "
                f"{c['lo90']:.1f}–{c['hi90']:.1f} | {c['frp_harm_mean']:.1f} |"
            )
    else:
        md.append("_Bu kapsamda aktif altıgen yok._")

    md.append("\n## 4. Davranış ve zamanlama")
    md.append(
        f"- Yangın yoğunluğu yerel güneş saatinde {PARAMS.peak_hour:.1f} saatte tepe yapan Gauss gün döngüsünü izler. "
        f"Bu boylamda önümüzdeki 24 saatte beklenen maksimum **{peak['utc']} UTC**, minimum **{trough['utc']} UTC**."
    )
    md.append(
        "- AOI’de nominal gündüz geçişleri (UTC): "
        + "; ".join(f"{s['satellite']} {s['pass']} {s['utc']}" for s in sched if s["pass"] == "gündüz")
        + "."
    )
    md.append(
        "- Terra ve Aqua 10:30 / 13:30 yörüngelerinden kayıyor; FRP’leri karşılaştırmadan önce SNPP referans saatine ölçeklenir."
    )

    sec = 5
    if drivers:
        md.append(f"\n## {sec}. MODIS sıcak noktaları neden yeniden ölçeklendi")
        for d in drivers[:4]:
            md.append(
                f"- **{d['driver']}**: harmonize FRP üzerinde {d['mean_effect_pct']:+.0f}% "
                f"(tipik aralık {d['p10']:+.0f}% … {d['p90']:+.0f}%)"
            )
        sec += 1

    if clim:
        md.append(f"\n## {sec}. İklimsel bağlam")
        md.append(
            f"- Son 7 günlük harmonize ortalama vs bu günlerin 25 yıllık medyanı: z = {clim.get('recent_z')}."
        )
        for e in (clim.get("episodes") or [])[:3]:
            md.append(
                f"- Anomalik dönem {e['start']} → {e['end']} ({e['days']} gün, tepe z {e['peak_z']:+.1f})"
            )
        sec += 1

    md.append(f"\n## {sec}. Önerilen eylemler (kural tabanlı)")
    md.extend(f"{i + 1}. {a}" for i, a in enumerate(actions))
    sec += 1

    md.append(f"\n## {sec}. Güven ve uyarılar")
    if _is_local_val(val):
        red = val.get("rmse_reduction_pct", float("nan"))
        note = (
            "harmonize bu ROI’de VIIRS’e yaklaştırıyor"
            if isinstance(red, (int, float)) and red > 0
            else "harmonize bu ROI’de VIIRS’e yaklaştırmıyor — Ham seriyi tercih et"
        )
        md.append(
            f"- Bu ROI’de yerel doğrulama (MODIS → VIIRS, örtüşen aylar): "
            f"RMSE ham {_fmt(val.get('rmse_raw'), 1)} → harmonize {_fmt(val.get('rmse_harmonized'), 1)} "
            f"(değişim {red:+.0f}%); göreli sapma ham "
            f"{val.get('relative_bias_raw_pct', float('nan')):+.0f}% → harmonize "
            f"{val.get('relative_bias_harmonized_pct', float('nan')):+.1f}% "
            f"(n={_fmt(val.get('n'))} ay). {note}."
        )
    elif val:
        h = val.get("holdout_2022_2026") or val
        if "rmse_reduction_pct" in h:
            md.append(
                f"- Hold-out doğrulama (MODIS-türev vs VIIRS yerel): RMSE azalması "
                f"{h.get('rmse_reduction_pct', float('nan')):.0f}%, göreli sapma "
                f"{h.get('relative_bias_harmonized_pct', float('nan')):+.1f}% "
                f"(ham {h.get('relative_bias_raw_pct', float('nan')):+.0f}%), "
                f"%90 GA kapsama {h.get('ci90_coverage', float('nan')):.0%}."
            )
    if q.get("synthetic"):
        md.append("- Benchmark arşivi **fiziksel olarak simüle** edilmiş kayıttır (bilinen yer gerçeği), NASA gözlemi değildir.")
    for n in q.get("notes", []):
        md.append(f"- {n}")
    md.append(
        "- Biyom sınıfları MCD12Q1 arazi örtüsü için enlem/bölge vekilidir; "
        "FRP harmonizasyonu yalnızca MODIS tespit ayak izlerine uygulanır."
    )
    markdown = "\n".join(md)

    payload = {
        "alert": {"level": level, "reason": level_reason},
        "audience": aud_key,
        "scope": ctx.get("scope"), "window": ctx.get("window"), "totals": totals,
        "priority_cells": top[:8], "diurnal_outlook": outlook, "overpass_schedule": sched,
        "drivers": drivers, "climate": clim, "recommended_actions": actions, "quality": q,
        "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    return {"markdown": markdown, "json": payload}
