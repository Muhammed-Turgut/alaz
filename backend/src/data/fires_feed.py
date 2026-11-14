"""Gerçek zamanlı NASA FIRMS besleme ucu: tarayıcıya ham-benzeri CSV (``/api/fires``).

Sözleşme (arayüzdeki ``fetcher``/``processor`` ile aynı):
  * CSV başlığı ``latitude,longitude,...`` ile başlar
  * VIIRS: ``bright_ti4`` (K), ``confidence`` (l/n/h), MODIS: ``brightness`` (K), ``confidence`` (0-100)
  * ``frp``, ``acq_date``, ``acq_time`` (HHMM), ``source_satellite``

Veri her zaman gerçek FIRMS'ten gelir (anahtar / açık global CSV / önbellek / paketli gerçek örnek);
sentetik veri ASLA üretilmez. Hangi katmandan geldiği ``meta`` ile döner.
"""
from __future__ import annotations

import threading
import time

import pandas as pd

from src.data.firms_client import FirmsClient

INSTRUMENTS = ("VIIRS", "MODIS")
MEMO_TTL_S = 600  # 10 dk: FIRMS NRT zaten ~3 saatte bir güncellenir


class FiresFeed:
    def __init__(self, client: FirmsClient | None = None):
        self.client = client or FirmsClient()
        self._memo: dict | None = None
        self._lock = threading.Lock()

    def _load(self, force: bool):
        with self._lock:
            m = self._memo
            if m and not force and time.time() - m["t"] < MEMO_TTL_S:
                return m["df"], m["meta"]
            df, meta = self.client.fetch_live(days=1, bbox=None, force=force)
            self._memo = {"t": time.time(), "df": df, "meta": meta}
            return df, meta

    def csv(self, instrument: str, force: bool = False) -> tuple[str, dict]:
        inst = instrument.upper()
        if inst not in INSTRUMENTS:
            raise ValueError(f"instrument must be one of {INSTRUMENTS}")
        df, meta = self._load(force)
        if df is None or len(df) == 0:
            raise LookupError("NASA FIRMS verisine ulaşılamadı (ağ yok ve önbellek/örnek bulunamadı).")
        d = df[df["instrument"] == inst]
        ts = pd.to_datetime(d["ts"])
        # Ham koruma: lat/lon/parlaklık/FRP yuvarlanmaz (orijinal sayı korunur).
        out = pd.DataFrame({
            "latitude": d["latitude"].to_numpy(),
            "longitude": d["longitude"].to_numpy(),
        })
        conf = d["confidence"].to_numpy()
        if inst == "VIIRS":
            out["bright_ti4"] = d["bright_t21"].to_numpy()
            out["confidence"] = pd.Series(conf).map(lambda c: "h" if c >= 80 else ("l" if c <= 30 else "n")).to_numpy()
        else:
            out["brightness"] = d["bright_t21"].to_numpy()
            out["confidence"] = pd.Series(conf).round().astype(int).to_numpy()
        out["frp"] = d["frp"].to_numpy()
        out["acq_date"] = ts.dt.strftime("%Y-%m-%d").to_numpy()
        out["acq_time"] = ts.dt.strftime("%H%M").to_numpy()
        out["source_satellite"] = d["satellite"].to_numpy()
        # tiers yalnızca bu cihazın ürünleri
        keys = [k for k in meta["sources"] if (k.startswith("MODIS") if inst == "MODIS" else k.startswith("VIIRS"))]
        tiers = sorted({meta["sources"][k]["tier"] for k in keys})
        live = all(meta["sources"][k]["live"] for k in keys if meta["sources"][k]["rows"] > 0) and len(out) > 0
        info = {
            "live": bool(live),
            "tiers": tiers,
            "rows": int(len(out)),
            "newest": str(ts.max()) if len(ts) else "",
            "oldest": str(ts.min()) if len(ts) else "",
            "key": bool(meta["key_configured"]),
        }
        return out.to_csv(index=False), info
