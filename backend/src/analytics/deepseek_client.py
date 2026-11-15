"""DeepSeek Chat Completions istemcisi (OpenAI-uyumlu)."""
from __future__ import annotations

import json
import os
from typing import Any

import httpx

PROJECT_CONTEXT_TR = (
    "Proje: Alaz / AETHER-FIRE — NASA FIRMS MODIS + VIIRS sıcak noktalarını "
    "ortak bir VIIRS-eşdeğer ölçekte inceleyen araştırma prototipi. "
    "Amaç: 2012 sensör geçişi ve sensör farklarını görünür kılmak; "
    "uzun zaman serisini karşılaştırılabilir hale getirmek (ön tarama / analiz desteği). "
    "Operasyonel yangın söndürme emri veya yayılım tahmini DEĞİL. "
    "MODIS ve VIIRS ham sayımları farklı çözünürlük/geçiş zamanı nedeniyle doğrudan "
    "birleştirilmez; hibrit α ve/veya harmonizasyon ile VIIRS-eşdeğer ölçeğe çekilir. "
    "VIIRS karşılaştırma referansıdır (mutlak yer-gerçeği / yanmış alan değil)."
)

PROJECT_CONTEXT_EN = (
    "Project: Alaz / AETHER-FIRE — research prototype that explores NASA FIRMS "
    "MODIS + VIIRS hotspots on a shared VIIRS-equivalent scale. "
    "Goal: make the 2012 sensor transition and inter-sensor differences visible; "
    "make long time series more comparable (pre-screening / analysis support). "
    "NOT operational firefighting orders or spread forecasts. "
    "Raw MODIS and VIIRS counts are not freely merged (resolution / overpass differ); "
    "hybrid α and/or harmonization map toward a VIIRS-equivalent scale. "
    "VIIRS is a comparison reference (not absolute ground truth / burned area)."
)

SYSTEM_PROMPT_TR = (
    "Sen Alaz / AETHER-FIRE araştırma asistanısın. "
    "TEMEL HEDEF: kullanıcının sorusuna doğrudan cevap vermek. "
    "Kaynak: yalnızca mesajdaki JSON dayanak sayıları + proje bağlamı. "
    "Soru merkezli yaz; tabloyu madde madde yeniden anlatma — soruyu yanıtlamak için "
    "gerekli sayıları seçip kullan. "
    "gorunen_tablo_yillar / matris = ekrandaki tablo; roi_arsiv_tarih = tüm arşiv (tablo aralığı değil). "
    "Sayı uydurma; dayanakta yoksa bunu açıkça söyle. "
    "tespit_sayisi / ham_tespit = FIRMS sıcak nokta tespiti (yangın sayısı veya yanmış alan DEĞİL); "
    "siddet_frp / frp_* = radyatif güç (MW). "
    "Harmonizasyon sentetik benchmark’ta eğitildi; VIIRS karşılaştırma referansıdır. "
    "p-değerlerini 'kesin kırılma' diye sunma; otokorelasyon uyarısı ekle. "
    "Sevk / müdahale emri veya kesin yayılım tahmini verme. "
    "Kısa markdown (3–8 madde veya kısa paragraflar). Grafik resmi görmüyorsun."
)

SYSTEM_PROMPT_EN = (
    "You are the Alaz / AETHER-FIRE research assistant. "
    "PRIMARY GOAL: answer the user's question directly. "
    "Source: ONLY the JSON evidence numbers + project context in the message. "
    "Be question-first; do not re-narrate the whole table — pick numbers that answer the question. "
    "Do not invent numbers; if evidence lacks something, say so clearly. "
    "tespit_sayisi / ham_tespit = FIRMS hotspot detections (NOT fire count or burned area); "
    "siddet_frp / frp_* = radiative power (MW). "
    "Harmonization was trained on a synthetic benchmark; VIIRS is a comparison reference. "
    "Do not present p-values as a definitive break; mention autocorrelation. "
    "No dispatch orders or confident spread forecasts. "
    "Short markdown (3–8 bullets or short paragraphs). You cannot see charts."
)


def configured() -> bool:
    return bool((os.environ.get("DEEPSEEK_API_KEY") or "").strip())


def chat_interpret(
    used_numbers: dict[str, Any],
    *,
    focus: str,
    section: str = "overview",
    question: str | None = None,
    lang: str = "tr",
) -> dict[str, Any]:
    """Kullanıcı sorusuna, tablo dayanak JSON’u ile cevap üretir. Dönüş: {markdown, model}."""
    key = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    if not key:
        raise RuntimeError(
            "DeepSeek API key not set (backend/.env → DEEPSEEK_API_KEY)."
            if (lang or "tr").lower() == "en"
            else "DeepSeek API anahtarı tanımlı değil (backend/.env → DEEPSEEK_API_KEY)."
        )

    en = (lang or "tr").strip().lower() == "en"
    base = (os.environ.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com").rstrip("/")
    model = (os.environ.get("DEEPSEEK_MODEL") or "deepseek-chat").strip()
    q = (question or "").strip()[:500]
    if not q:
        q = (
            "What does this section show for MODIS vs VIIRS in this ROI, and what should I watch for?"
            if en
            else "Bu bölümde MODIS ile VIIRS açısından ne görünüyor ve nelere dikkat etmeliyim?"
        )
    sec = (section or "overview").strip()[:64]
    system = SYSTEM_PROMPT_EN if en else SYSTEM_PROMPT_TR
    project = PROJECT_CONTEXT_EN if en else PROJECT_CONTEXT_TR

    if en:
        user_parts = [
            f"USER QUESTION (answer this first):\n{q}",
            project,
            f"UI tab: {focus} · section/table: {sec}",
            "Evidence from Alaz tables for this ROI (JSON — use as source only):",
            json.dumps(used_numbers, ensure_ascii=False, indent=2),
            "Instructions: Answer the user question using the evidence. "
            "For the visible table use gorunen_tablo_yillar / matrix / yillik_ozet; "
            "roi_arsiv_tarih is the full archive span, NOT the on-screen table range. "
            "Mention MODIS/VIIRS and sensor_baglam when relevant. Do not invent numbers.",
        ]
    else:
        user_parts = [
            f"KULLANICI SORUSU (önce bunu cevapla):\n{q}",
            project,
            f"UI sekmesi: {focus} · tablo/bölüm: {sec}",
            "Alaz tablolarından ROI dayanak sayıları (JSON — yalnızca kaynak):",
            json.dumps(used_numbers, ensure_ascii=False, indent=2),
            "Talimat: Kullanıcı sorusunu dayanak sayılarla cevapla. "
            "Görünen tablo için 'gorunen_tablo_yillar' / matris / yillik_ozet kullan; "
            "'roi_arsiv_tarih' tüm arşivdir, ekrandaki tablo aralığı değildir. "
            "Gerektiğinde MODIS/VIIRS ve sensor_baglam alanını belirt. Sayı uydurma.",
        ]

    url = f"{base}/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": "\n\n".join(user_parts)},
        ],
        "temperature": 0.35,
        "max_tokens": 1400,
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    try:
        with httpx.Client(timeout=120.0) as client:
            res = client.post(url, headers=headers, json=payload)
    except httpx.TimeoutException as exc:
        msg = (
            "DeepSeek timeout (120 s). Try a smaller ROI or retry."
            if en else "DeepSeek zaman aşımı (120 sn). Daha küçük ROI veya tekrar dene."
        )
        raise RuntimeError(msg) from exc
    except httpx.HTTPError as exc:
        raise RuntimeError(f"DeepSeek network error: {exc}" if en else f"DeepSeek ağ hatası: {exc}") from exc

    if res.status_code == 401:
        raise RuntimeError("DeepSeek key invalid (401)." if en else "DeepSeek anahtarı geçersiz (401).")
    if res.status_code == 402:
        raise RuntimeError("DeepSeek credits/quota insufficient (402)." if en else "DeepSeek kredi / kota yetersiz (402).")
    if res.status_code >= 400:
        detail = res.text[:300]
        raise RuntimeError(f"DeepSeek error {res.status_code}: {detail}")

    data = res.json()
    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(
            "Unexpected DeepSeek response shape." if en else "DeepSeek yanıtı beklenen biçimde değil."
        ) from exc

    return {"markdown": (text or "").strip(), "model": data.get("model") or model}
