# Alaz — Sunum (NASA Space Apps)

Uzman olmayan dinleyiciye yönelik slayt sunumu.

## Sunum senaryosu (jüriye gösterme)

Konuşma akışı, terimler ve olası sorular:
[`SUNUM-SENARYOSU.md`](./SUNUM-SENARYOSU.md)

## Algoritma & ML rehberi (ekip içi)

ML/algoritma sorumlusunun çalışması için:
[`ALGORITMA-VE-ML-REHBERI.md`](./ALGORITMA-VE-ML-REHBERI.md) · PDF: `Alaz-Algoritma-ve-ML-Rehberi.pdf`

## PDF

Dosya: `Alaz-NASA-Space-Apps-Sunumu.pdf` (16 sayfa: 4 giriş + 12 ürün)

```bash
xdg-open presentation/Alaz-NASA-Space-Apps-Sunumu.pdf
```

Dosya yöneticisinde: `Masaüstü/alaz/presentation/` klasörünü aç → PDF’e çift tıkla.

## Açma (interaktif HTML)

```bash
# Proje kökünden — API + UI + sunum birlikte
npm run start:demo
# → UI http://127.0.0.1:5173/ · Sunum http://127.0.0.1:5500/

# Sadece sunum dosyası
xdg-open presentation/index.html
# veya
python3 -m http.server 5500 --directory presentation
```

## Kontroller

| Tuş | İşlev |
|-----|--------|
| `→` `↓` `Space` `Enter` | Sonraki slayt |
| `←` `↑` `Backspace` | Önceki slayt |
| `F` | Tam ekran |
| `Home` / `End` | İlk / son slayt |

Mobilde kaydırarak da geçilebilir.

## İçerik özeti

1. Kapak + gündem  
2. NASA/CONAE ne istedi · sensör problemi · 2012 sıçraması  
3. Alaz ne yaptı · usecase’ler  
4. Ürün ekranları (harita, kırılma, takvim)  
5. İstek vs Alaz matrisi · yorumlar · doğrulama kanıtı  
6. Jüri öne çıkanları · demo yolu · kapanış  

Görseller `assets/` altında (logo, diyagramlar, uygulama ekran görüntüleri).
