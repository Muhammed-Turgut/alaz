"""DeepSeek Chat Completions istemcisi (OpenAI-uyumlu)."""
from __future__ import annotations

import json
import os
from typing import Any

import httpx

SYSTEM_PROMPT_TR = (
    "Sen Alaz / AETHER-FIRE araştırma asistanısın (ön tarama / analiz desteği; operasyonel talimat DEĞİL). "
    "Yalnızca kullanıcı mesajındaki JSON dayanak sayılarına dayanarak kısa, net Türkçe yorum yaz. "
    "Sayı uydurma. Dayanakta olanları yorumla; eksik bir alan varsa ayrı 'Eksikler' / 'Not' başlığı açma. "
    "tespit_sayisi / ham_tespit = FIRMS sıcak nokta tespiti (yangın sayısı veya yanmış alan DEĞİL); "
    "siddet_frp / frp_* = radyatif güç (MW) — yanmış alan veya toplam enerji değil. "
    "Harmonizasyon sentetik benchmark’ta eğitildi; VIIRS karşılaştırma referansıdır (mutlak yer-gerçeği değil). "
    "İklimsel zarf için aylık zarf_p10/p50/p90 ve hedef yıl değerlerini kullan; "
    "anomali bölümünde dönem, z skoru ve varsa dönem FRP özetine odaklan. "
    "p-değerlerini 'kesin kırılma' diye sunma; otokorelasyon uyarısı ekle. "
    "Ekip/kaynak sevkı, müdahale emri veya kesin yayılım tahmini verme. "
    "Analitik dilde 3–8 madde; markdown kullan. "
    "Grafik resmi görmüyorsun — yalnızca sayı özeti var."
)

SYSTEM_PROMPT_EN = (
    "You are the Alaz / AETHER-FIRE research assistant (pre-screening / analysis support; NOT operational orders). "
    "Write a short, clear English commentary using ONLY the JSON evidence numbers in the user message. "
    "Do not invent numbers. Interpret what is present; do not open a separate 'Missing' section for absent fields. "
    "tespit_sayisi / ham_tespit = FIRMS hotspot detections (NOT fire count or burned area); "
    "siddet_frp / frp_* = radiative power (MW) — not burned area or total energy. "
    "Harmonization was trained on a synthetic benchmark; VIIRS is a comparison reference (not absolute ground truth). "
    "For the climatology envelope use monthly zarf_p10/p50/p90 and the target-year values; "
    "for anomalies focus on period, z-score, and any period FRP summary. "
    "Do not present p-values as a definitive break; mention autocorrelation. "
    "Do not issue dispatch orders or confident spread forecasts. "
    "3–8 analytical bullets; use markdown. "
    "You cannot see charts — only the numeric summary."
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
    """Dayanak JSON + odak/bölüm ile yorum üretir. Dönüş: {markdown, model}."""
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
    sec = (section or "overview").strip()[:64]
    system = SYSTEM_PROMPT_EN if en else SYSTEM_PROMPT_TR

    if en:
        user_parts = [
            f"Tab: {focus}",
            f"Section: {sec}",
            "Interpret only this section’s evidence numbers; do not drift to other tables.",
            "Evidence numbers (JSON):",
            json.dumps(used_numbers, ensure_ascii=False, indent=2),
        ]
        if q:
            user_parts.append(f"User question: {q}")
        else:
            user_parts.append(
                f"Write a short English AI insight for the «{sec}» table/chart: "
                "what it shows, what to watch, artifact vs real signal."
            )
    else:
        user_parts = [
            f"Sekme: {focus}",
            f"Tablo/bölüm: {sec}",
            "Yalnızca bu bölümün dayanak sayılarını yorumla; diğer tablolara kayma.",
            "Dayanak sayıları (JSON):",
            json.dumps(used_numbers, ensure_ascii=False, indent=2),
        ]
        if q:
            user_parts.append(f"Kullanıcı sorusu: {q}")
        else:
            user_parts.append(
                f"«{sec}» tablosu / grafiği için kısa Türkçe AI bakışı yaz: "
                "ne anlatıyor, neye dikkat, artefakt mı gerçek sinyal mi."
            )

    url = f"{base}/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": "\n\n".join(user_parts)},
        ],
        "temperature": 0.3,
        "max_tokens": 1200,
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
