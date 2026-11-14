"""NASA FIRMS tarihsel arşiv indiricisi: gerçek MODIS + VIIRS tespitleri, 2000 -> bugün.

Bir alan (sınır kutusu) için FIRMS alan API'sinden tüm geçmişi 5 günlük dilimler halinde çeker.

Ham veriyi koruma ilkesi
  * Her dilim NASA'dan geldiği gibi, bayt bayt değiştirilmeden diske yazılır
    (``data/archive/<alan>/raw/<ÜRÜN>/<YIL>/<başlangıç>_<gün>d.csv``).
  * Hiçbir satır silinmez, hiçbir değer yuvarlanmaz, hiçbir nokta üretilmez.
  * Her dosyanın satır sayısı ve SHA-256 özeti ``manifest.jsonl`` içine kaydedilir.

NASA kotası (ölçüldü, 2026-11-14): bir istek ``2 x gün`` işlem harcar ve bu, alanın büyüklüğünden
bağımsızdır; sınır 10 dakikada 5000 işlemdir. Bu yüzden bir alanın tüm geçmişi (yaklaşık 3600 dilim)
alan ne kadar küçük olursa olsun en az ~75 dakika sürer. İndirme yarıda kesilirse kaldığı yerden sürer.

Kullanım (``backend`` klasöründen)::

    .venv/bin/python -m src.data.firms_archive --area-id tr --name "Türkiye" --bbox 25.5,35.8,45.0,42.3
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

log = logging.getLogger("aether.archive")

ROOT = Path(__file__).resolve().parents[2]
ARCHIVE_DIR = ROOT / "data" / "archive"
BASE = "https://firms.modaps.eosdis.nasa.gov"

CHUNK_DAYS = 5        # alan API'sinin kabul ettiği en uzun aralık (10 gün reddediliyor)
TX_PER_DAY = 2        # istek maliyeti = 2 işlem x gün
TX_WINDOW_S = 600
TX_BUDGET = 4500      # NASA sınırı 5000 / 10 dk; canlı harita istekleri için pay bırakılır
HEADER = b"latitude,longitude"

# sensör -> (bilim kalitesinde arşiv ürünü, yakın-gerçek-zamanlı ürün). Arşiv ürünü birkaç ay geriden gelir;
# aradaki son aylar NRT ürününden tamamlanır. NOAA-21 motor tarafından henüz desteklenmediği için alınmaz.
PRODUCTS = {
    "MODIS": ("MODIS_SP", "MODIS_NRT"),
    "VIIRS_SNPP": ("VIIRS_SNPP_SP", "VIIRS_SNPP_NRT"),
    "VIIRS_NOAA20": ("VIIRS_NOAA20_SP", "VIIRS_NOAA20_NRT"),
}


@dataclass(frozen=True)
class Chunk:
    source: str
    start: date
    days: int
    partial: bool = False   # son günleri içerir: NASA veriyi hâlâ tamamlıyor olabilir, sonra yeniden çekilir

    @property
    def end(self) -> date:
        return self.start + timedelta(days=self.days - 1)

    @property
    def cost(self) -> int:
        return self.days * TX_PER_DAY

    def path(self, raw_dir: Path) -> Path:
        suffix = ".partial.csv" if self.partial else ".csv"
        return raw_dir / self.source / f"{self.start.year}" / f"{self.start.isoformat()}_{self.days}d{suffix}"


def _segment(source: str, first: date, last: date, today: date) -> list[Chunk]:
    out, d = [], first
    while d <= last:
        n = min(CHUNK_DAYS, (last - d).days + 1)
        end = d + timedelta(days=n - 1)
        out.append(Chunk(source, d, n, partial=end >= today - timedelta(days=1)))
        d += timedelta(days=n)
    return out


def plan_chunks(availability: dict[str, tuple[date, date]], today: date) -> list[Chunk]:
    """Her sensörün her gününü tam bir kez kapsayan dilim listesi (arşiv ürünü + NRT kuyruğu, çakışmasız)."""
    chunks: list[Chunk] = []
    for sp, nrt in PRODUCTS.values():
        nrt_first = None
        if sp in availability:
            sp_first, sp_last = availability[sp]
            chunks += _segment(sp, sp_first, sp_last, today)
            nrt_first = sp_last + timedelta(days=1)
        if nrt in availability:
            n_first, n_last = availability[nrt]
            first = max(n_first, nrt_first) if nrt_first else n_first
            if first <= n_last:
                chunks += _segment(nrt, first, n_last, today)
    return chunks


class TxLimiter:
    """Kayan 10 dakikalık pencerede harcanan NASA işlem sayısını bütçenin altında tutar."""

    def __init__(self, budget: int = TX_BUDGET, window_s: float = TX_WINDOW_S, already_used: int = 0):
        self.budget, self.window = budget, window_s
        self.lock = threading.Lock()
        self.events: deque[tuple[float, int]] = deque()
        self.used = 0
        if already_used:
            self.events.append((time.monotonic(), already_used))
            self.used = already_used

    def acquire(self, cost: int) -> None:
        while True:
            with self.lock:
                now = time.monotonic()
                while self.events and now - self.events[0][0] >= self.window:
                    self.used -= self.events.popleft()[1]
                if self.used + cost <= self.budget or not self.events:
                    self.events.append((now, cost))
                    self.used += cost
                    return
                wait = self.window - (now - self.events[0][0])
            time.sleep(min(max(wait, 0.05), 5.0))


class ArchiveDownloader:
    def __init__(self, area_id: str, bbox: tuple[float, float, float, float], name: str = "",
                 map_key: str | None = None, workers: int = 3, budget: int = TX_BUDGET):
        self.key = (map_key if map_key is not None else os.getenv("FIRMS_MAP_KEY", "")).strip()
        if not self.key:
            raise SystemExit("FIRMS_MAP_KEY tanımlı değil (backend/.env).")
        w, s, e, n = bbox
        if not (-180 <= w < e <= 180 and -90 <= s < n <= 90):
            raise SystemExit("bbox 'batı,güney,doğu,kuzey' sırasında ve geçerli aralıkta olmalı.")
        self.area_id, self.bbox, self.name = area_id, (w, s, e, n), name or area_id
        self.area_arg = f"{w:g},{s:g},{e:g},{n:g}"
        self.dir = ARCHIVE_DIR / area_id
        self.raw = self.dir / "raw"
        self.workers, self.budget = workers, budget
        self._local = threading.local()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ ağ
    def _session(self) -> requests.Session:
        s = getattr(self._local, "s", None)
        if s is None:
            s = requests.Session()
            s.headers["User-Agent"] = "AETHER-FIRE/1.0 (NASA Space Apps 2026)"
            self._local.s = s
        return s

    def _redact(self, text: str) -> str:
        return text.replace(self.key, "***")

    def server_transactions(self) -> int:
        try:
            r = self._session().get(f"{BASE}/mapserver/mapkey_status/", params={"MAP_KEY": self.key}, timeout=40)
            return int(r.json().get("current_transactions", 0))
        except Exception:  # noqa: BLE001 - yalnızca bilgi amaçlı
            return 0

    def availability(self) -> dict[str, tuple[date, date]]:
        r = self._session().get(f"{BASE}/api/data_availability/csv/{self.key}/all", timeout=60)
        r.raise_for_status()
        out = {}
        for line in r.text.strip().splitlines()[1:]:
            parts = line.split(",")
            if len(parts) == 3:
                out[parts[0]] = (date.fromisoformat(parts[1]), date.fromisoformat(parts[2]))
        if not out:
            raise RuntimeError(self._redact(f"veri kapsamı okunamadı: {r.text[:120]!r}"))
        return out

    def _fetch(self, c: Chunk, limiter: TxLimiter) -> bytes:
        url = f"{BASE}/api/area/csv/{self.key}/{c.source}/{self.area_arg}/{c.days}/{c.start.isoformat()}"
        err = ""
        for attempt in range(6):
            limiter.acquire(c.cost)
            try:
                r = self._session().get(url, timeout=180)
                if r.status_code == 200 and r.content.lstrip().startswith(HEADER):
                    return r.content
                err = self._redact(f"http {r.status_code}: {r.text[:140]!r}")
            except requests.RequestException as exc:
                err = self._redact(f"{type(exc).__name__}: {str(exc)[:140]}")
            # kota aşımı ya da geçici hata: bekleyip yeniden dene
            time.sleep(60 if "limit" in err.lower() or "exceed" in err.lower() else min(5 * 2 ** attempt, 60))
        raise RuntimeError(f"{c.source} {c.start} ({c.days} gün) alınamadı: {err}")

    # ------------------------------------------------------------------ disk
    def _store(self, c: Chunk, body: bytes) -> int:
        path = c.path(self.raw)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(body)           # NASA yanıtı olduğu gibi: tek bayt değiştirilmez
        tmp.replace(path)
        if not c.partial:               # dilim artık kesinleştiyse eski geçici kopyayı kaldır
            Chunk(c.source, c.start, c.days, True).path(self.raw).unlink(missing_ok=True)
        rows = max(len(body.strip().splitlines()) - 1, 0)
        rec = {"source": c.source, "start": c.start.isoformat(), "days": c.days, "rows": rows, "bytes": len(body),
               "sha256": hashlib.sha256(body).hexdigest(), "partial": c.partial,
               "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        with self._lock, open(self.dir / "manifest.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        return rows

    def _write_json(self, name: str, obj: dict) -> None:
        tmp = self.dir / (name + ".tmp")
        tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.dir / name)

    # ------------------------------------------------------------------ çalıştır
    def run(self) -> dict:
        self.dir.mkdir(parents=True, exist_ok=True)
        area_file = self.dir / "area.json"
        if area_file.exists():
            old = json.loads(area_file.read_text(encoding="utf-8"))
            if [round(v, 6) for v in old["bbox"]] != [round(v, 6) for v in self.bbox]:
                raise SystemExit(f"'{self.area_id}' başka bir kutuyla ({old['bbox']}) indirilmiş; farklı bir --area-id kullan.")
        today = datetime.now(timezone.utc).date()
        avail = self.availability()
        plan = plan_chunks(avail, today)
        self._write_json("area.json", {
            "id": self.area_id, "name": self.name, "bbox": list(self.bbox), "bbox_order": "west,south,east,north",
            "products": {k: [v[0].isoformat(), v[1].isoformat()] for k, v in avail.items()
                         if any(k in p for p in PRODUCTS.values())},
        })
        # kesinleşmiş dilimler bir kez indirilir; 'partial' (son günler) her çalıştırmada yenilenir
        pending = sorted((c for c in plan if c.partial or not c.path(self.raw).exists()),
                         key=lambda c: c.start, reverse=True)   # yeniden eskiye: son yıllar önce gelir
        total, done0 = len(plan), len(plan) - len(pending)
        limiter = TxLimiter(self.budget, already_used=self.server_transactions())
        t0 = time.time()
        state = {"done": done0, "rows": 0, "failed": []}

        def progress(status: str) -> dict:
            fetched = state["done"] - done0
            rate = fetched / max(time.time() - t0, 1e-6)
            left = total - state["done"] - len(state["failed"])
            p = {"area": self.area_id, "name": self.name, "state": status, "total_chunks": total,
                 "done_chunks": state["done"], "failed_chunks": len(state["failed"]), "rows_this_run": state["rows"],
                 "eta_minutes": round(left / rate / 60, 1) if fetched and left else 0,
                 "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "errors": state["failed"][-5:]}
            self._write_json("progress.json", p)
            return p

        log.info("%s: %d dilimden %d tanesi indirilecek (%d işlem, en az ~%d dk)", self.area_id, total, len(pending),
                 sum(c.cost for c in pending), sum(c.cost for c in pending) * TX_WINDOW_S // self.budget // 60)
        progress("running")

        def work(c: Chunk) -> int:
            return self._store(c, self._fetch(c, limiter))

        with ThreadPoolExecutor(self.workers) as ex:
            futures = {ex.submit(work, c): c for c in pending}
            for i, fut in enumerate(as_completed(futures), 1):
                c = futures[fut]
                try:
                    state["rows"] += fut.result()
                    state["done"] += 1
                except Exception as exc:  # noqa: BLE001 - tek dilim hatası indirmeyi durdurmaz; sonraki çalıştırmada denenir
                    state["failed"].append(str(exc)[:200])
                    log.warning("%s", exc)
                if i % 25 == 0 or i == len(pending):
                    p = progress("running")
                    log.info("%d / %d dilim (%.0f%%), bu çalıştırmada %s satır, kalan ~%s dk, en eski: %s",
                             p["done_chunks"], total, 100 * p["done_chunks"] / total, f"{state['rows']:,}",
                             p["eta_minutes"], c.start)
        return progress("done" if not state["failed"] else "incomplete")


def _load_env() -> None:
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def main() -> None:
    ap = argparse.ArgumentParser(description="NASA FIRMS tarihsel arşivini bir alan için indirir (kaldığı yerden sürer).")
    ap.add_argument("--area-id", required=True, help="klasör adı, örn. tr")
    ap.add_argument("--name", default="", help="görünen ad, örn. Türkiye")
    ap.add_argument("--bbox", required=True, help="batı,güney,doğu,kuzey (derece)")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--budget", type=int, default=TX_BUDGET, help="10 dakikada harcanacak en fazla NASA işlemi")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    _load_env()
    bbox = tuple(float(x) for x in args.bbox.split(","))
    if len(bbox) != 4:
        raise SystemExit("--bbox dört sayı olmalı: batı,güney,doğu,kuzey")
    p = ArchiveDownloader(args.area_id, bbox, args.name, workers=args.workers, budget=args.budget).run()  # type: ignore[arg-type]
    log.info("bitti: durum=%s, %d / %d dilim, hatalı %d", p["state"], p["done_chunks"], p["total_chunks"], p["failed_chunks"])


if __name__ == "__main__":
    main()
