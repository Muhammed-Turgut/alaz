# Alaz / AETHER-FIRE

**EN:** Research prototype for exploring NASA FIRMS MODIS + VIIRS active-fire hotspots on a shared VIIRS-equivalent scale, with a Turkey historical archive pilot and a live global map (≈24 h).

**TR:** NASA FIRMS MODIS + VIIRS sıcak noktalarını ortak bir VIIRS-eşdeğer ölçekte inceleyen araştırma prototipi. Tarihsel takvim: Türkiye arşivi (pilot). Canlı harita: küresel ≈24 saat.

> **Not / Note:** Alaz is **not** an operational firefighting command system. Outputs are **pre-screening / analysis support**, not dispatch orders. Harmonization was trained on a **physically simulated benchmark**; real-archive checks are monthly MODIS→VIIRS comparisons (VIIRS is a **comparison reference**, not absolute ground truth). Independent event-level validation is **future work**.

## Quick start

```bash
# İlk kurulum (bir kez)
npm run setup
# backend/.env içinde FIRMS_MAP_KEY (isteğe bağlı DEEPSEEK_API_KEY) ayarla

# Her şeyi tek komutla ayağa kaldır (API :8000 + UI :5173)
npm start

# Sunum dahil (API + UI + slides :5500)
npm run start:demo
```

Ctrl+C tüm süreçleri durdurur.

Ayrı ayrı (gerekirse): `npm run api` · `npm run dev`

Optional: `VITE_MAPBOX_TOKEN` for Mapbox basemap (OSM fallback without token).

## Data sources

| Mode | What it is | Not |
|------|------------|-----|
| **Live NASA FIRMS** | NRT MODIS + VIIRS **NOAA-20 + NOAA-21** (≈24 h), global | SNPP NRT (ended 2026-11-02); full historical planet archive |
| **Archive TR** | Real FIRMS history inside Turkey coverage (Standard + provisional NRT tail) | Synthetic “NASA observation” |
| **Benchmark (internal)** | Physically simulated 2001–2026 regions for model train/holdout | Real FIRMS |

Archive progress: `backend/data/archive/tr/progress.json`. Area metadata: `backend/data/archive/tr/area.json`.

### Products & attribution

- **Independent prototype** using publicly documented NASA FIRMS Area API / open CSV feeds — **not** a NASA-endorsed product.
- Live product IDs: `MODIS_NRT`, `VIIRS_NOAA20_NRT`, `VIIRS_NOAA21_NRT`.
- Archive product IDs: `*_SP` (Standard / science-quality) plus recent `*_NRT` tail (provisional; may later be replaced by Standard).
- Historical SNPP (`VIIRS_SNPP_SP` / archived NRT chunks) is **kept** for time series; it is **not** fetched as live NRT after 2026-11-02.
- FIRMS / Earthdata: https://firms.modaps.eosdis.nasa.gov/ — register a MAP_KEY for Area API.
- Access date for this README revision: **2026-11-15**.

### Standard vs NRT

NASA recommends Standard/science-quality products for long-term scientific analysis. NRT differs in latency, quality control, and sometimes day/night field semantics. The UI labels NRT tails as provisional and reports product-kind composition per ROI query. Product kind `unknown` is shown explicitly when provenance is missing (never silently assumed Standard or NRT).

## Method (short)

1. **Raw counts** — hotspot detections as reported (sensor of origin matters). Not fire-event counts.
2. **Confidence filter (production = policy A)** — before harmonization counts, low-confidence detections are dropped with a **fixed** main-result rule (not retuned for prettier plots):
   - **A (current / primary):** MODIS `confidence ≥ 30`; VIIRS **nominal/high** (mapped `low` excluded).
   - VIIRS `low` / `nominal` / `high` are **ordinal product classes**, not the same scale as MODIS 0–100. Mapped scores (low≈30 / nominal≈65 / high≈90) are a shared code-path convenience — **not** a common physical threshold across sensors.
3. **Harmonized** — model maps MODIS-scale activity toward a **VIIRS-equivalent** footprint/intensity scale so long time series are more comparable. Multi-platform totals are normalized by operating platform count (`n_operating`) so adding NOAA-21 does not create an artificial step. Harmonization uses policy **A** only for the main series.
4. **Burning calendar** — day-of-year × year heatmaps, month×year tables, climatology envelope (P10/P50/P90 = **model uncertainty band**, not fire-count assurance), anomalies vs multi-year baseline.
5. **Structural break** — tests around the 2012 VIIRS transition (sensor artifact vs real change). `p<0.05` is **not** a definitive break under autocorrelation.
6. **Briefing / CSV** — numeric summaries + optional audience tone; ROI hotspot CSV export (each row = one FIRMS hotspot detection; times UTC).

### Confidence-filter sensitivity (A / B / C)

Raising or lowering the confidence cut arbitrarily would trade false positives vs missed weak fires. Alaz **keeps policy A in the main result** and reports a side-by-side sensitivity comparison instead of switching to whichever threshold “looks best”:

| Policy | MODIS | VIIRS | Intent |
|--------|-------|-------|--------|
| **A — current (primary)** | `≥ 30` | nominal + high | Production filter |
| **B — stricter** | `≥ 80` | high only | Fewer low-confidence rows; higher risk of missing small/weak fires |
| **C — looser** | all valid 0–100 | all classes (low/nominal/high) | More detections kept; higher false-positive risk |

**How usefulness is judged:** for each policy, monthly filtered MODIS detection counts are compared to monthly VIIRS counts under the **same** policy (RMSE / bias / correlation). Closer to VIIRS is diagnostic evidence — **not** a license to auto-select that policy for the UI. The closest-by-RMSE label in `local_validation.confidence_sensitivity` is explicit about this. Main calendar / break / density / briefing paths stay on **A**.

Implementation: `backend/src/engine/confidence_policies.py` → `prepare.confidence_ok` (A) + `confidence_filter_sensitivity` (A/B/C report attached to ROI `local_validation`).

## Model evaluation (two tracks)

| Track | Data | Meaning |
|-------|------|---------|
| **Synthetic benchmark** | Simulated matchups; train 2012–2019, calib 2020–2021, hold-out 2022–2026 | Known simulation truth — **not** real NASA FIRMS |
| **Real FIRMS archive** | Turkey ROI monthly MODIS → VIIRS | **Reference comparison** only; not ground truth; not event-level validation |

Geographic generalization beyond the Turkey pilot is **not validated** (future work).

## Limitations (must state in demos)

- A **hotspot detection** ≠ fire count ≠ burned area. Same event can be re-detected across overpasses.
- Map coordinates are usually **detection pixel centers**; pixel size grows toward scan edge.
- **FRP (MW)** is radiative power of the detection, not burned area or total fire energy.
- **p-values** on autocorrelated time series can look “too significant”; use with effect size (step %) and context.
- Pre-VIIRS **reference** series is unavailable (shown as missing — not zero activity).
- Briefing diurnal peaks are a **nominal solar cycle proxy**, not weather/fuel/wind forecasts.
- No weather, fuel moisture, topography, or local response data → do **not** claim reliable tactical priority.
- Static-source filters may reduce false positives but can also exclude real fires — compare raw vs filtered counts.
- Confidence thresholds are **not** cross-sensor calibrated; do not treat VIIRS class labels as MODIS percentages. Do not retune A→B/C just to improve a chart — use the sensitivity table.

## Intended claim for judges

> Alaz aims to make sensor-driven differences between MODIS and VIIRS visible and to explore fire activity over time on a VIIRS-equivalent scale, using a real FIRMS Turkey archive pilot—not to predict fires or issue operational dispatch orders.

## Quick English demo path (≈2–3 min)

1. Header → **EN** + **Real archive (TR)** (prefer archive over live for jury demos).
2. Open **Start / demo guide** → optionally **Load demo ROI** (Manavgat / Antalya coast; coords shown) or draw your own polygon (≤ ~120 000 km²).
3. **Map** — FIRMS hotspot detections; read pixel-center / cluster notes.
4. **Structural break** — raw jump at 2012 vs harmonized; read p-value caveats; timeline markers for NOAA-20/21.
5. **Burn calendar** — VIIRS-equivalent series; hotspot ≠ fire count ≠ burned area; incomplete years labeled.
6. **Model & validation** — section 1 synthetic benchmark vs section 2 real-archive VIIRS **reference** metrics (`n` months shown; if RMSE worsens, prefer Raw).
7. **Briefing** — pre-screen summary with disclaimer; export Hotspot CSV (row = detection; columns documented in file header).

Expected screens: archive data pill, ROI outline with km², break chart with transition markers, calendar heatmap, validation table with `n` months — **no fabricated success metrics**.

UI chrome (tabs, source, disclaimer, glossary, demo guide) follows TR/EN. Briefings honor the selected language. HTML `lang` updates with the language toggle.

## Future work (not in this prototype claim)

- Event/area validation with high-resolution imagery or official fire perimeters / burned-area products (different target than hotspot counts).
- Optional adaptation of the synthetic model to real VIIRS reference after bias reporting (no automatic silent switch).
- Geographic hold-out beyond Turkey.
- Re-evaluation of observation counts and harmonization impact after NOAA-21 inclusion (platform calendar already updated).

## License / attribution

NASA FIRMS / Earthdata. Challenge context: NASA Space Apps — Harmonization of MODIS and VIIRS Hot Spots (CONAE-associated challenge language). This repository is an independent research prototype.
d area; incomplete years labeled.
6. **Model & validation** — section 1 synthetic benchmark vs section 2 real-archive VIIRS **reference** metrics (`n` months shown; if RMSE worsens, prefer Raw).
7. **Briefing** — pre-screen summary with disclaimer; export Hotspot CSV (row = detection; columns documented in file header).

Expected screens: archive data pill, ROI outline with km², break chart with transition markers, calendar heatmap, validation table with `n` months — **no fabricated success metrics**.

UI chrome (tabs, source, disclaimer, glossary, demo guide) follows TR/EN. Briefings honor the selected language. HTML `lang` updates with the language toggle.

## Future work (not in this prototype claim)

- Event/area validation with high-resolution imagery or official fire perimeters / burned-area products (different target than hotspot counts).
- Optional adaptation of the synthetic model to real VIIRS reference after bias reporting (no automatic silent switch).
- Geographic hold-out beyond Turkey.
- Re-evaluation of observation counts and harmonization impact after NOAA-21 inclusion (platform calendar already updated).

## License / attribution

NASA FIRMS / Earthdata. Challenge context: NASA Space Apps — Harmonization of MODIS and VIIRS Hot Spots (CONAE-associated challenge language). This repository is an independent research prototype.
