"""DeepSeek Chat Completions istemcisi (OpenAI-uyumlu)."""
from __future__ import annotations

import json
import os
from typing import Any

import httpx

SYSTEM_PROMPT = (
    "Sen Alaz / AETHER-FIRE yangın istihbarat asistanısın. "
    "Yalnızca kullanıcı mesajındaki JSON dayanak sayılarına dayanarak kısa, net Türkçe yorum yaz. "
    "Sayı uydurma. Dayanakta olanları yorumla; eksik bir alan varsa ayrı 'Eksikler' / 'Not' başlığı açma. "
    "tespit_sayisi / ham_tespit = FIRMS sıcak nokta (yangın tespit) adedi; "
    "siddet_frp / frp_* = radyatif güç (MW) yani şiddet göstergesi — bunlar dayanakta varsa "
    "'yangın sayısı veya şiddeti yok' deme; sayı ve FRP ile yorumla. "
    "İklimsel zarf için aylık zarf_p10/p50/p90 ve hedef yıl değerlerini kullan; "
    "anomali bölümünde dönem, z skoru ve varsa dönem siddet (FRP) özetine odaklan. "
    "Komutan dilinde 3–8 madde; markdown kullan. "
    "Grafik resmi görmüyorsun — yalnızca sayı özeti var."
)


def configured() -> bool:
    return bool((os.environ.get("DEEPSEEK_API_KEY") or "").strip())


def chat_interpret(
    used_numbers: dict[str, Any],
    *,
    focus: str,
    section: str = "overview",
    question: str | None = None,
) -> dict[str, Any]:
    """Dayanak JSON + odak/bölüm ile yorum üretir. Dönüş: {markdown, model}."""
    key = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("DeepSeek API anahtarı tanımlı değil (backend/.env → DEEPSEEK_API_KEY).")

    base = (os.environ.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com").rstrip("/")
    model = (os.environ.get("DEEPSEEK_MODEL") or "deepseek-chat").strip()
    q = (question or "").strip()[:500]
    sec = (section or "overview").strip()[:64]

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
            {"role": "system", "content": SYSTEM_PROMPT},
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
        raise RuntimeError("DeepSeek zaman aşımı (120 sn). Daha küçük ROI veya tekrar dene.") from exc
    except httpx.HTTPError as exc:
        raise RuntimeError(f"DeepSeek ağ hatası: {exc}") from exc

    if res.status_code == 401:
        raise RuntimeError("DeepSeek anahtarı geçersiz (401).")
    if res.status_code == 402:
        raise RuntimeError("DeepSeek kredi / kota yetersiz (402).")
    if res.status_code >= 400:
        detail = res.text[:300]
        raise RuntimeError(f"DeepSeek hata {res.status_code}: {detail}")

    data = res.json()
    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("DeepSeek yanıtı beklenen biçimde değil.") from exc

    return {"markdown": (text or "").strip(), "model": data.get("model") or model}
