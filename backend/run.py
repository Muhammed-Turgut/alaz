#!/usr/bin/env python3
"""AETHER-FIRE single-command launcher.

    python run.py                 # verify / build archives + model, start the server
    python run.py --retrain       # force model retraining
    python run.py --check         # build / verify artifacts only (no server)
    python run.py --port 9000 --host 0.0.0.0

Steps
  1. generate the 2001-2026 benchmark archives if their cache is missing (~5 s)
  2. train the biome-stratified quantile harmonizer if the saved model is missing,
     unreadable or from another version (~30 s), otherwise verify it by scoring a sample
  3. start uvicorn serving the API (the UI is the React app in the repo root: `npm run dev`)
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def load_env() -> None:
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def ensure_artifacts(retrain: bool = False) -> None:
    """Build archives and model if needed, then verify the model end to end."""
    import numpy as np

    from src.data import benchmark_archives as arch
    from src.engine import pipeline as pl
    from src.engine.harmonizer_ml import MODEL_PATH, MODEL_VERSION, HarmonizerModel, add_cell_context

    t0 = time.time()
    print("[aether] checking benchmark archives ...")
    data = arch.load_all(arch.SimConfig(), progress=lambda rid, y: None)
    n = sum(len(d["df"]) for d in data.values())
    print(f"[aether] archives ready: {n:,} detections in {len(data)} regions ({time.time() - t0:.1f}s)")

    model = None
    if MODEL_PATH.exists() and not retrain:
        try:
            model = HarmonizerModel.load(MODEL_PATH)
            if getattr(model, "version", None) != MODEL_VERSION:
                print(f"[aether] model version {model.version} != {MODEL_VERSION}; retraining")
                model = None
        except Exception as exc:  # noqa: BLE001
            print(f"[aether] saved model unusable ({exc}); retraining")

    prepared = None
    if model is None:
        print("[aether] training biome-stratified quantile harmonizer (P10/P50/P90) ...")
        prepared = {rid: pl.prepare_region(d)[0] for rid, d in data.items()}
        model = pl.train_harmonizer(prepared, progress=lambda *a: print("   ", *a))
        MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        model.save(MODEL_PATH)
        print(f"[aether] model saved -> {MODEL_PATH}")

    # verification: the model must score real rows with ordered, finite quantiles
    if prepared is None:
        rid = next(iter(data))
        prepared = {rid: pl.prepare_region(data[rid])[0]}
    df = next(iter(prepared.values()))
    sample = df[(df["instrument"] == "MODIS") & df["conf_ok"] & ~df["is_static"]].sample(2000, random_state=0)
    pred = model.predict(add_cell_context(sample) if "log_cell_n" not in sample.columns else sample)
    ok = np.isfinite(pred.to_numpy(dtype=float)).all() and (pred["frp_p10"] <= pred["frp_p50"] + 1e-9).all() \
        and (pred["frp_p50"] <= pred["frp_p90"] + 1e-9).all()
    if not ok:
        raise SystemExit("[aether] model verification FAILED (non-finite or crossing quantiles)")
    print(f"[aether] model {model.version} verified on 2,000 held scoring rows ({time.time() - t0:.1f}s total)")


def main() -> None:
    ap = argparse.ArgumentParser(description="AETHER-FIRE launcher")
    ap.add_argument("--host", default=None)
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--retrain", action="store_true", help="force model retraining")
    ap.add_argument("--check", action="store_true", help="build/verify artifacts, do not start the server")
    ap.add_argument("--reload", action="store_true", help="uvicorn auto-reload (development)")
    args = ap.parse_args()

    load_env()
    ensure_artifacts(retrain=args.retrain)
    if args.check:
        return

    host = args.host or os.environ.get("HOST", "127.0.0.1")
    port = args.port or int(os.environ.get("PORT", "8000"))
    key = "configured" if os.environ.get("FIRMS_MAP_KEY") else "not set (open FIRMS feeds + cache fallback)"
    print(f"[aether] FIRMS_MAP_KEY: {key}")
    print(f"[aether] API -> http://{host}:{port}/api   (docs: /docs)   UI: npm run dev")

    import uvicorn

    uvicorn.run("src.api.main:app", host=host, port=port, reload=args.reload, log_level="info")


if __name__ == "__main__":
    main()
