# Alaz — Sunum senaryosu & soru hazırlığı

Bu dosya **jüriye/PDF’e gösterilmez**. Siz konuşurken ve soru gelince kullanın.  
Akış: kısa slayt sunumu → canlı website.

---

## 1) Konuşma senaryosu (≈6–8 dk slayt + site)

### Açılış (Kapak)
> “Alaz, NASA Space Apps’te CONAE’nin challenge’ı için: iki farklı uydu sensörünün yangın sıcak noktalarını aynı dile çeviren bir araştırma prototipi.”

Tek cümle iddia:
> “Yangın tahmin etmiyoruz; sensör farkını görünür kılıp uzun dönemi adil okuyoruz.”

### NASA ne istedi?
Sade dil:
> “2000’lerden beri MODIS var, 2012’den beri VIIRS var. İkisi aynı yangına bakıyor ama farklı çözünürlükte. Ham sayıları birleştirince 2012’de sahte bir sıçrama oluşuyor. Challenge: bunu birleştirip web’de bölge seçip zamanı inceleyebileceğimiz bir araç.”

### Problem (diyagramlar)
> “MODIS pikseli kabaca 1 km, VIIRS 375 m. VIIRS daha ince görür → aynı yangında daha çok nokta. Bu, ‘yangın arttı’ demek değildir.”

### Biz ne yaptık?
> “NASA FIRMS verisini alıyoruz, güven filtresinden geçiriyoruz, MODIS’i VIIRS-eşdeğer ölçeğe çekiyoruz, sonra harita / kırılma / takvim / brifing olarak sunuyoruz.”

### Usecase
> “Örnek: Manavgat kıyısı. Orman/AFAD analisti ‘bu yaz her zamankinden mi yoğun?’ diye bakabilir. Araştırmacı 2012 sıçramasının sensör mü yoksa gerçek mi olduğunu görebilir. Operasyon için ön tarama — emir değil.”

### Ürün slaytları (kısa tutun — site gelecek)
- Harita: “Bölge çiziyoruz, tespitleri / yoğunluğu görüyoruz.”
- Kırılma: “2012’de ham seri zıplıyor; harmonize daha düzgün.”
- Takvim: “Challenge’ın istediği burning calendar — ay vs tarihsel medyan.”

### İstek vs Alaz + yorumlar
Vurgu: **dürüst sınırlar**
> “Hotspot yangın sayısı değildir. VIIRS mutlak yer-gerçeği değildir. Güven eşiğini grafiği güzelleştirmek için oynatmıyoruz.”

### Kanıt
> “Türkiye arşivinde hold-out aylarda harmonize MAE ≈ 4.9, ham ≈ 21.9. Bu inter-sensor anlaşma — olay doğrulaması değil; onu açıkça söylüyoruz.”

### Kapanış → site
> “Şimdi canlı sitede Manavgat demosunu açıyoruz.”

**Site sırası (2–3 dk):** Gerçek arşiv → Demo ROI → Harita → Kırılma → Takvim → (isterseniz) Model / Brifing.

---

## 2) Terimler — soru gelirse ne diyeceksiniz?

### Veri kaynağı nedir?
| Terim | Sade cevap |
|--------|------------|
| **NASA FIRMS** | Fire Information for Resource Management System. NASA’nın uyduyla tespit ettiği aktif yangın / sıcak nokta yayın sistemi. |
| **Hotspot / sıcak nokta** | Uydunun “burada anormal sıcaklık var” dediği **piksel tespiti**. Yangın sayısı veya yanmış alan değildir. Aynı yangın birden fazla geçişte tekrar sayılabilir. |
| **MODIS** | Terra/Aqua uydularındaki sensör. ~1 km piksel. 2000’den beri uzun tarih. |
| **VIIRS** | Daha yeni / ince sensör (~375 m). SNPP, NOAA-20, NOAA-21. 2012’den beri. |
| **SNPP** | Suomi NPP uydusu. Canlı NRT beslemesi 2026-11-02’de bitti; arşivde tarihsel olarak duruyor. Canlıda NOAA-20/21 kullanıyoruz. |
| **NRT vs Standard** | NRT = Near Real-Time (hızlı, geçici). Standard = bilim kalitesi, uzun dönem analiz için tercih. |
| **FRP** | Fire Radiative Power (MW). Tespit anındaki radyatif güç. Alan veya toplam enerji değildir. |
| **ROI** | Region of Interest — analiz için çizdiğiniz alan. |
| **H3** | Uber’in altıgen grid’i. Yoğunluğu hücre hücre göstermek için. Res-7 ≈ 5,2 km². |

### Harmonizasyon / model
| Terim | Sade cevap |
|--------|------------|
| **Harmonizasyon** | MODIS ile VIIRS sayılarını **karşılaştırılabilir** hale getirmek. “İki Excel’i yapıştırmak” değil; ölçekleri hizalamak. |
| **VIIRS-eşdeğer** | Modelin “bu MODIS tespiti VIIRS dünyasında kabaca neye denk gelir?” tahmini. |
| **ML (Machine Learning)** | Makine öğrenmesi. Bizde: tarihsel/simüle eşleşmelerden öğrenip MODIS → VIIRS ölçeği tahmin eden model (Gradient Boosting ailesi / HGB). Elle sabit çarpan yerine bağlama göre (biome, açı, FRP vb.) öğrenir. |
| **Sentetik benchmark** | Fiziksel olarak **simüle** edilmiş eğitim seti. Gerçek FIRMS değil; bilinen “doğru cevap” olduğu için modeli ölçebiliriz. |
| **Gerçek arşiv karşılaştırması** | Türkiye FIRMS’inde aylık MODIS sayısını VIIRS ile kıyaslamak. VIIRS yine **referans**, mutlak ground truth değil. |
| **Hibrit α** | Saf ML çıktısını sönümleyip / baseline ile karıştırarak daha dengeli seri üretmek. “Her zaman ML kazansın” demiyoruz. |
| **Platform normalizasyonu** | NOAA-21 eklenince ham toplamın yapay şişmesini azaltmak için çalışan platform sayısına göre ölçekleme. |

### İstatistik / doğrulama
| Terim | Sade cevap |
|--------|------------|
| **MAE** | Mean Absolute Error — ortalama mutlak hata. “Ay bazında tahminimize VIIRS referansından ortalama kaç birim uzakız?” |
| **RMSE** | Karekök ortalama karesel hata. Büyük hataları daha çok cezalandırır. |
| **Hold-out** | Model/kalibrasyon fit edilirken **görmediği** sonraki aylar. Ezber mi, genelleme mi diye bakarız. |
| **p-değeri** | “Bu kırılma şans eseri mi?” istatistiği. Otokorelasyon abartabilir; **p&lt;0.05 kesin kırılma değildir**. Adım % ile birlikte okunur. |
| **Pettitt / Chow** | Zaman serisinde yapısal kırılma testleri (özellikle 2012 civarı). |
| **Anomali** | Seçilen ayın, aynı ayın çok yıllı medyanına göre farkı (%). |
| **P10 / P50 / P90** | Dağılımın yüzde 10 / 50 / 90 noktaları. Takvimde belirsizlik bandı — “yangın sayısı garantisi” değil. |
| **Confidence (güven)** | FIRMS’in tespitin ne kadar emin olduğu. MODIS 0–100; VIIRS low/nominal/high. **Aynı ölçek değiller.** Policy A: MODIS ≥30, VIIRS nominal+high. |

### Ürün sekmeleri (site turunda)
| Sekme | Bir cümle |
|--------|-----------|
| **Görev haritası** | Seçilen bölgedeki tespitler / yoğunluk. |
| **Yapısal kırılma** | 2012 sensör geçişi ham seriyi şişiriyor mu? |
| **Yanma takvimi** | Yıl × gün/ay ısı haritası + anomali. |
| **Sıcak nokta açıklayıcı** | Piksel ayak izi, bow-tie, tek tespit harmonizasyonu. |
| **Brifing** | Ön tarama metni + CSV. Emir değil. |
| **Model ve doğrulama** | Sentetik benchmark + gerçek arşiv metrikleri. |

---

## 3) Sık sorular — hazır cevaplar

**“Yangını tahmin ediyor musunuz?”**  
Hayır. Geçmiş/canlı tespitleri okunabilir kılıyoruz. Tahmin / dispatch iddiası yok.

**“Bunu itfaiye kullanabilir mi?”**  
Ön tarama / durum farkındalığı için evet; **müdahale emri** olarak hayır. Hava, yakıt, topoğrafya yok.

**“VIIRS doğru kabul ediliyor mu?”**  
Hayır. Karşılaştırma referansı. Bulut, örnekleme, geçiş zamanı farkı var. Ground truth için yanmış alan / yüksek çözünürlüklü görüntü gerekir — gelecek iş.

**“Neden Türkiye?”**  
Pilot arşiv: yerel diskte hızlı demo, gerçek FIRMS. Challenge küresel ROI de açıyor; coğrafi genelleme henüz doğrulanmadı.

**“ML ne öğrendi?”**  
Simüle MODIS↔VIIRS eşleşmelerinde, MODIS özelliklerinden VIIRS-eşdeğer yoğunluk/ölçek. Sonra gerçek arşivde aylık uyumu ölçüyoruz.

**“Başarı metriğiniz ne?”**  
Öncelikle: sensör farkını görünür kılmak + adil zaman serisi. Sayısal: hold-out’ta inter-sensor MAE’nin hamdan iyi olması. Olay düzeyinde doğrulama iddiası yok.

**“Hotspot ile yangın farkı?”**  
Hotspot = uydu pikseli. Yangın olayı = ayrı kimlik (biz üretmiyoruz). Yanmış alan = ayrı ürün.

**“Neden 2012 önemli?”**  
VIIRS başlıyor. Ham MODIS+VIIRS toplamı zıplar; bu çoğu zaman sensör, her zaman daha çok yangın değil.

**“NOAA-21 / SNPP ne oldu?”**  
SNPP canlı NRT bitti. Canlıda NOAA-20+21. Platform sayısı artınca toplamın şişmemesi için normalize ediyoruz.

**“Rakip takımlardan farkınız?”**  
Dürüst sınırlar UI’da; iki doğrulama rayı; güven eşiğini cherry-pick etmeme; canlı+arşiv tek üründe; kırılma istatistiğini p’ye indirgememe.

---

## 4) Söylemeyin / söyleyin

| Söylemeyin | Söyleyin |
|------------|----------|
| “Yangın sayısını buluyoruz” | “Sıcak nokta tespiti yoğunluğunu inceliyoruz” |
| “VIIRS gerçeği” | “VIIRS karşılaştırma referansı” |
| “Operasyonel sistem” | “Araştırma prototipi / ön tarama” |
| “%99 doğru” | “Hold-out’ta MAE düşüyor; olay doğrulaması yok” |
| “AI her şeyi çözüyor” | “ML ölçek tahminine yardım ediyor; sınırları açık” |

---

## 5) Demo ROI (site için)

- **Manavgat / Antalya kıyısı**  
- ≈ 36.55–37.05°N, 31.20–31.75°E  
- Kaynak: **Gerçek arşiv** → Demo ROI yükle  

Hazır cümle:
> “Bu kutu yaklaşık 2700 km²; arşivde 2001’den 2026’ya yedi binin üzerinde ham tespit var. Şimdi kırılma ve takvime bakalım.”
