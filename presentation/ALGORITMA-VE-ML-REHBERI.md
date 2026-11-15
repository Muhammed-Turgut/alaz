# Alaz — Algoritma & Makine Öğrenmesi Rehberi

**Kimin için:** Sunumda / soru-cevapta algoritma ve ML kısmından sorumlu arkadaş  
**Amaç:** Ne yaptığımızı, neden yaptığımızı, jüriye nasıl anlatacağımızı öğrenmek  
**Kaynak kod:** `backend/src/engine/`, `backend/src/analytics/`

> Alaz yangın **tahmin** etmez. MODIS ve VIIRS sıcak nokta kayıtlarını **karşılaştırılabilir** hale getirir.

---

## 0) Büyük resim (2 dakika)

İki paralel yol var; karıştırma:

| Yol | Dosya | Ne ölçeğinde? | ML mi? |
|-----|--------|----------------|--------|
| **A — Tespit düzeyi (ML)** | `harmonizer_ml.py` | Her MODIS noktası → VIIRS-eşdeğer `n` ve `frp` | Evet — HistGradientBoosting |
| **B — Hücre / ay düzeyi (hibrit α)** | `hybrid_harmonizer.py` | Aylık aktif H3 hücre sayısı | Hayır — OLS `Y = αX` |
| **C — Aylık post-kalibrasyon** | `reference_comparison.py` | Ham/harmonize seriyi VIIRS referansına yaklaştırma | Hayır — grid search (MAE) |
| **D — Yapısal kırılma** | `stats_diagnostics.py` | 2012 sensör geçişi testi | Hayır — Pettitt + Chow |

UI’da “harmonize / VIIRS-eşdeğer” çoğunlukla **A**; “Hibrit α / hibrit katmanlar” **B**; doğrulama tablosundaki MAE iyileşmesi çoğunlukla **A+C**.

---

## 1) Veri nedir? (önce bunu bil)

### FIRMS sıcak nokta (hotspot)
Uydunun “bu pikselle anormal sıcaklık var” dediği **tespit**.  
**≠ yangın sayısı ≠ yanmış alan.** Aynı yangın birden fazla geçişte tekrar sayılabilir.

### MODIS vs VIIRS
- **MODIS** (~1 km, 2000+): daha kaba → aynı yangında daha az nokta  
- **VIIRS** (~375 m, 2012+): daha ince → aynı yangında daha çok nokta  

Ham toplamları yapıştırmak **2012’de sahte sıçrama** üretir.

### H3 (Uber)
Dünyayı **altıgen hücrelere** bölen grid. Alaz’da **res-7 ≈ 5,2 km²**.  
Yoğunluk ve hibrit hesaplarda “nokta yağmuru” yerine “hücre aktivitesi” okumak için kullanılır.  
**H3 bir ML modeli değil** — mekânsal kutu sistemi.

---

## 2) Ön işlem (pipeline hijyeni)

Kod: `prepare.py`, `confidence_policies.py`, `static_filter.py`

1. Ortak şema (lat/lon/ts/FRP/confidence/instrument…)  
2. **Güven filtresi Policy A (üretim):**  
   - MODIS: `confidence ≥ 30`  
   - VIIRS: `nominal` + `high` (`low` elenir)  
3. Policy B/C sadece **duyarlılık tablosu** — ana sonucu “güzel görünsün” diye değiştirmiyoruz.  
4. Statik termal kaynak filtresi (fabrika vb. kalıcı ısıyı azaltmaya çalışır; gerçek yangını da kesebilir).  
5. Bow-tie / piksel alanı, diurnal (gün-içi) faktörler, biome etiketi — ML özelliklerine girdi.

**Jüri cümlesi:** “Güven eşiğini cherry-pick etmiyoruz; A sabit, B/C yanında raporlanıyor.”

---

## 3) Yol A — Makine öğrenmesi: Quantile Harmonizer

Kod: `harmonizer_ml.py` · model: `aether-fire-hgb-q-v2` · `HistGradientBoostingRegressor` (scikit-learn)

### 3.1 Ne öğreniyor?
Her **MODIS tespiti** için, aynı ateşin yakındaki VIIRS geçişindeki eşdeğerini tahmin eder:

- `n` → MODIS ayak izi içinde VIIRS-ölçekli hotspot sayısı  
- `frp` → VIIRS-ölçekli Fire Radiative Power (MW)

Hedefler formülle yazılmaz; **eşleştirilmiş ayak izlerinden** (`match_footprints`) gelir. İyi skor = “kural ezberledim” değil, “sensör çiftinden öğrendim” kanıtı.

### 3.2 Eğitim hedefi nasıl üretilir?
1. MODIS ve VIIRS tespitleri zaman+mekân penceresinde eşleşir (`max_dt_hours ≈ 4.5`).  
2. VIIRS pikselinin “kütlesi” kesişen MODIS ayak izlerine **oranla** bölüştürülür.  
3. Her MODIS satırına `n_eq`, `frp_eq` yazılır.

### 3.3 Hangi model?
**HistGradientBoosting** (histogram tabanlı gradyan artırma):

| Alt model | Loss | Çıktı |
|-----------|------|--------|
| Quantile P10 / P50 / P90 | `quantile` | `log1p(n)` ve `log1p(frp)` üzerinde |
| Ortalama | `poisson` | `n` / `frp` ortalaması |

Biome’a göre ayrı gruplar: Forest / Cropland-Stubble / Savanna-Grassland (+ yetersizse `all`).

**Hipermetreler (özet):** `max_iter=220`, `learning_rate=0.07`, `max_leaf_nodes=24`, `min_samples_leaf=40`, `l2_regularization=1.0`.

### 3.4 Özellikler (features)
Gruplar (`FEATURE_GROUPS`):

| Grup | Örnek özellikler | Sezgi |
|------|------------------|--------|
| Bow-tie ayak izi | `area_km2`, `scan`, `track`, `bowtie_ratio` | Kenarda piksel şişer |
| Diurnal / geçiş saati | `lst_sin/cos`, `diurnal_ratio`, `night` | Gece/gündüz ve yörünge saati |
| Yangın yoğunluğu | `log_frp`, `bright_t21`, `confidence` | Ne kadar “parlak/sıcak” |
| Küme bağlamı | `log_cell_n`, `log_cell_frp` | Aynı H3 hücredeki komşu tespitler |
| Mevsim / enlem | `doy_sin/cos`, `abs_lat` | Sezon ve coğrafya |

### 3.5 Belirsizlik: Conformalized Quantile Regression (CQR)
Ham P10–P90 bandı kalibrasyon setinde **CQR** (Romano et al. 2019) ile genişletilir → yaklaşık **%90 tahmin aralığı**.  
UI’daki P10/P50/P90 yangın sayısı garantisi **değildir** — model belirsizlik bandıdır.

### 3.6 Completeness kalibrasyonu
MODIS küçük ateşleri kaçırabilir → tespit başına tahminleri toplamak VIIRS toplamını **eksik** bırakır.  
Biome × day/night için log-lineer düzeltme, diurnal faktörle Terra/Aqua saat kaymasına da uyarlanır.

### 3.7 Veri bölünmesi (sentetik benchmark)
- Train ≈ 2012–2019  
- Calib ≈ 2020–2021  
- Hold-out ≈ 2022–2026  

**Önemli:** Bu benchmark **fiziksel simülasyon**; gerçek FIRMS ground truth değil. Gerçek arşivde ayrıca VIIRS’e karşı aylık karşılaştırma yapılır.

### 3.8 Jüriye anlatım
> “Tespit düzeyinde HistGradientBoosting quantile modeli kullanıyoruz. Hedef, eşleştirilmiş MODIS–VIIRS ayak izlerinden geliyor. Belirsizlik için CQR ile %90 bant üretiyoruz. Eğitim sentetik; gerçek arşivde VIIRS referansına bakıyoruz — VIIRS mutlak yer-gerçeği değil.”

---

## 4) Yol B — Hibrit α (aktif hücre + OLS)

Kod: `hybrid_harmonizer.py`

### 4.1 Felsefe
Tespit saymak yerine: **günde kaç H3 hücresi “aktif”?**  
Aynı hücrede 10 nokta olsa da o gün o hücre **1** sayılır → tekrar sayımı azaltır.

### 4.2 Adımlar
1. Hijyen (Policy A + geçerli geo/FRP)  
2. Her tespiti H3-7 (veya 0.05°) hücreye yaz  
3. Günlük aktif hücre → aylık MODIS / VIIRS toplamı  
4. 2012+ örtüşmede **kesişimden geçen OLS:**  
   \[
   Y_{\text{VIIRS}} = \alpha \, X_{\text{MODIS}}
   \]
   (`LinearRegression(fit_intercept=False)`)  
5. Güvenlik: `n_months ≥ 20` ve `α ∈ [0.8, 10]`; değilse **fallback α = 2.5**  
6. Sürekli seri:  
   - 2012 öncesi → `round(MODIS × α)`  
   - 2012 sonrası → VIIRS hücreleri  

### 4.3 Haritada “Hibrit katmanlar”
- Renkli grid: harmonize edilmiş hücre aktivitesi  
- Noktalar: gerçek VIIRS tespitleri (uydurma koordinat yok)

### 4.4 ML değil — ne zaman kullanılır?
Takvimde **Hibrit α**, haritada **Hibrit katmanlar**, bölgesel α + R² kartı.  
Basit, yorumlanabilir, NASA Space Apps “aktif hücre + ölçek” tarzına yakın.

### 4.5 Jüriye anlatım
> “Hibrit yol ML değil. Bölgesel α ile aylık aktif hücreleri VIIRS ölçeğine çekiyoruz. Az veri veya saçma α gelirse 2.5’e düşüyoruz.”

---

## 5) Yol C — Aylık post-kalibrasyon (doğrulama motoru)

Kod: `reference_comparison.py`, `system_validation.py`

Gerçek Türkiye arşivinde aylık **MODIS sayımı** ile **VIIRS referansı** karşılaştırılır.  
Harmonize ML çıktısı bazen şişebilir; kalibrasyon aylarında öğrenilen düzeltmeler:

| Parametre | Ne yapar? |
|-----------|-----------|
| `platform_scale` | Ham seriyi platform sayısına göre ölçekler (NOAA-20/21 şişirmesin) |
| `harm_alpha` | `α * harmonize` MAE’yi VIIRS’e göre minimize eder (grid search) |
| `harm_cap_k` | Megafire şişmesini `min(harm, k * scaled)` ile keser |
| `blend_w` | `w * platform_scaled + (1-w) * harm_capped` karışımı |

Hepsi **sadece kalibrasyon aylarında** fit edilir; test aylarında skorlanır (leakage yok).

Örnek (Manavgat hold-out, inter-sensor): ham MAE ≈ 21.9 → harmonize blend MAE ≈ 4.9.

**Jüri uyarısı:** Bu **inter-sensor agreement**. Yanmış alan / olay doğrulaması değil.

---

## 6) Yol D — Yapısal kırılma istatistiği

Kod: `stats_diagnostics.py`

### Pettitt (1979)
Parametrik olmayan değişim noktası. “Seride bir anda rejim değişti mi?”  
p ≈ `2 exp(-6 K² / (n³+n²))`.

### Chow (1960)
**Bilinen** kırılma tarihinde (2012-01-20) log-ölçekte doğrusal trend kırılması F-testi.

### Adım %
Kırılma öncesi/sonrası 5 yıllık ortalama farkı — **etki büyüklüğü**.

### Kritik uyarı (ezberle)
Aylık yangın serileri **otokorelasyonlu**. p-değerler anti-konservatif olabilir.  
**p&lt;0.05 = kesin kırılma değil.** p + adım % + ham vs harmonize birlikte okunur.

---

## 7) Yanma takvimi & anomali

Kod: `burning_calendar.py` (+ hibrit/harmonize/ham seri seçimi)

- Yıl × gün-of-year veya ay × yıl ısı haritası  
- Seçilen ay vs çok yıllı **medyan** → anomali %  
- P10–P90 climatology bandı = belirsizlik / dağılım, yangın garantisi değil  

---

## 8) Platform / sensör takvimi

Kod: `sensors.py`

- VIIRS SNPP ~2012  
- NOAA-20, NOAA-21 eklenince ham toplam artabilir  
- `n_operating` ile **platform normalizasyonu** — yeni uydu = yapay adım olmasın  

SNPP canlı NRT 2026-11-02’de bitti; canlıda NOAA-20/21.

---

## 9) Hangi metrik ne demek?

| Metrik | Anlam |
|--------|--------|
| **MAE** | Ortalama mutlak hata (aylık sayım vs VIIRS referansı) |
| **RMSE** | Büyük hataları daha çok cezalandırır |
| **R² (hibrit α)** | `Y=αX` doğrusal uyum kalitesi (aktif hücre) |
| **Bias %** | Sistematik fazla/eksik sayma |
| **Hold-out** | Fit edilmeyen sonraki aylar |
| **Pinball loss** | Quantile tahmin kalitesi |

---

## 10) Sık sorular — hazır cevaplar

**“Hangi ML’i kullanıyorsunuz?”**  
HistGradientBoosting quantile + Poisson (scikit-learn). Biome-stratified. Hedef: matched-footprint `n_eq` / `frp_eq`.

**“Deep learning / neural net var mı?”**  
Ana harmonizer’da yok. (Brifing için isteğe bağlı LLM metin katmanı ayrı konu.)

**“Hibrit α ML mi?”**  
Hayır. OLS üzerinden bölgesel ölçek.

**“Neden iki yol?”**  
ML tespit düzeyinde zengin; hibrit α sade ve yorumlanabilir. UI ikisini de gösterir; “her zaman ML kazanır” demiyoruz.

**“Overfit riski?”**  
Zaman bloklu train/calib/test; CQR ayrı calib; gerçek arşivde ayrı referans; coğrafi genelleme henüz iddia edilmiyor.

**“Ground truth?”**  
Sentetikte simülasyon gerçeği var. Gerçekte VIIRS **referans**. Olay/perimeter doğrulama = gelecek iş.

**“α = 1.21 ne demek?”**  
O ROI’de aylık MODIS aktif hücre × 1.21 ≈ VIIRS aktif hücre (örtüşme döneminde).

---

## 11) Öğrenme yolu (arkadaş için checklist)

1. README’deki “Method (short)” + limitations  
2. Bu rehber §0–§4  
3. Kod: `harmonizer_ml.py` docstring + `FEATURES`  
4. Kod: `hybrid_harmonizer.py` `fit_alpha` + `build_harmonized_monthly`  
5. UI’da Manavgat: Model sekmesi + Hibrit katman + Kırılma  
6. `system_validation.json` özet MAE’leri oku  
7. Birine 2 dakikada anlat (kayıt tutmadan)

---

## 12) Dosya haritası

```
backend/src/engine/
  harmonizer_ml.py      ← ML quantile harmonizer
  hybrid_harmonizer.py  ← aktif hücre + α
  geometry_h3.py        ← H3, ayak izi eşleştirme
  confidence_policies.py
  stats_diagnostics.py  ← Pettitt / Chow
  diurnal_normalizer.py
  prepare.py / pipeline.py / service.py

backend/src/analytics/
  reference_comparison.py  ← aylık post-cal
  system_validation.py
  burning_calendar.py
  confidence_sensitivity.py
```

---

*Alaz / AETHER-FIRE · NASA Space Apps 2026 · bağımsız araştırma prototipi*
