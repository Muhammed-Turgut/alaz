import { usePrefs } from "./preferences";

export type Lang = "tr" | "en";

const tr = {
  langLabel: "Dil",
  sourceLive: "Canlı NASA FIRMS",
  sourceArchive: "Gerçek arşiv (küresel)",
  theme: "Tema",
  themeDark: "Koyu",
  themeLight: "Açık",
  settings: "Ayarlar",
  mapStyle: "Harita stili",
  calColors: "Takvim renkleri",
  calClassic: "Klasik",
  calCb: "Renk körü",
  settingsHint: "Otomatik harita: temaya göre. Renk körü rampa kırmızı-yeşil bağımsızdır.",
  pillPilot: "Arşiv: küresel ROI",
  pillLive: "Kapsam: canlı global (24 sa)",
  disclaimer:
    "Araştırma prototipi — operasyonel müdahale talimatı değil. Harmonizasyon sentetik benchmark’ta eğitildi; VIIRS karşılaştırma referansıdır (mutlak yer-gerçeği değil). Sıcak nokta ≠ yangın sayısı ≠ yanmış alan.",
  disclaimerBreak:
    "p<0,05 kesin kırılma değildir (otokorelasyon abartabilir). VIIRS sütunu karşılaştırma referansıdır; VIIRS öncesi dönem yer-gerçeği değildir. Harmonizasyon sentetik benchmark’ta eğitildi.",
  disclaimerCalendar:
    "Takvim hücreleri sıcak nokta tespit yoğunluğudur — yangın sayısı veya yanmış alan değildir. Aynı olay birden fazla geçişte tekrar sayılabilir. FRP radyatif güçtür, alan değildir.",
  disclaimerModel:
    "Harmonizasyon sentetik (simüle) benchmark’ta eğitildi. Tablo gerçek arşivde aylık MODIS→VIIRS karşılaştırmasıdır; VIIRS mutlak yer-gerçeği değildir. Olay düzeyinde bağımsız doğrulama yoktur.",
  disclaimerBriefing:
    "Ön tarama / analiz desteği — operasyonel talimat değil. Sıcak nokta ≠ yangın sayısı ≠ yanmış alan. Harmonizasyon sentetik benchmark’ta eğitildi; VIIRS karşılaştırma referansıdır.",
  disclaimerExplain:
    "Sıcak nokta = termal anomali tespiti (yangın sayısı / yanmış alan değil). FRP (MW) = radyatif güç, alan veya toplam enerji değil. Harmonize çıktı VIIRS-eşdeğer ölçek tahminidir.",
  mapHotspotNote:
    "Haritadaki noktalar FIRMS sıcak nokta tespitleridir (piksel merkezi ≈ ateş konumu değil; kenarda piksel büyür). Küme sayısı olay değil tespit kümesidir. Yangın sayısı / yanmış alan ölçülmez.",
  glossaryTitle: "Kısa sözlük",
  glossaryToggle: "Sözlük",
  tabs: {
    map: "Görev haritası",
    break: "Yapısal kırılma",
    calendar: "Yanma takvimi",
    explain: "Sıcak nokta açıklayıcı",
    briefing: "Brifing",
    model: "Model ve doğrulama",
  },
} as const;

const en = {
  langLabel: "Lang",
  sourceLive: "Live NASA FIRMS",
  sourceArchive: "Real archive (global)",
  theme: "Theme",
  themeDark: "Dark",
  themeLight: "Light",
  settings: "Settings",
  mapStyle: "Map style",
  calColors: "Calendar colors",
  calClassic: "Classic",
  calCb: "Colorblind",
  settingsHint: "Auto map follows theme. Colorblind ramp avoids red–green dependence.",
  pillPilot: "Archive: global ROI",
  pillLive: "Scope: live global (24h)",
  disclaimer:
    "Research prototype — not operational dispatch orders. Harmonization was trained on a synthetic benchmark; VIIRS is a comparison reference (not absolute ground truth). Hotspot ≠ fire count ≠ burned area.",
  disclaimerBreak:
    "p<0.05 is not a definitive break (autocorrelation can inflate significance). The VIIRS column is a comparison reference; pre-VIIRS era is not ground truth. Harmonization was trained on a synthetic benchmark.",
  disclaimerCalendar:
    "Calendar cells show hotspot detection intensity — not fire count or burned area. The same event can be re-counted across overpasses. FRP is radiative power, not area.",
  disclaimerModel:
    "The harmonization model was trained on a synthetic (simulated) benchmark. This table is monthly MODIS→VIIRS comparison on the real archive; VIIRS is not absolute ground truth. No event-level independent validation yet.",
  disclaimerBriefing:
    "Pre-screening / analysis support — not operational orders. Hotspot ≠ fire count ≠ burned area. Harmonization trained on a synthetic benchmark; VIIRS is a comparison reference.",
  disclaimerExplain:
    "Hotspot = thermal anomaly detection (not fire count / burned area). FRP (MW) = radiative power, not area or total energy. Harmonized output is a VIIRS-equivalent scale estimate.",
  mapHotspotNote:
    "Map points are FIRMS hotspot detections (pixel center ≈ not exact fire location; pixels grow toward scan edge). Cluster count is detections, not events. Fire count / burned area are not measured.",
  glossaryTitle: "Glossary",
  glossaryToggle: "Glossary",
  tabs: {
    map: "Mission map",
    break: "Structural break",
    calendar: "Burn calendar",
    explain: "Hotspot explainer",
    briefing: "Briefing",
    model: "Model & validation",
  },
} as const;

export type Dict = {
  [K in keyof typeof tr]: (typeof tr)[K] extends string
    ? string
    : { [P in keyof (typeof tr)[K]]: string };
};

const dicts: Record<Lang, Dict> = { tr, en };

export function t(lang: Lang): Dict {
  return dicts[lang] ?? tr;
}

export function useT(): Dict & { lang: Lang } {
  const { lang } = usePrefs();
  return { ...t(lang), lang };
}

export const GLOSSARY: Record<Lang, { term: string; def: string }[]> = {
  tr: [
    { term: "FIRMS sıcak nokta tespiti", def: "Uydunun termal anomali pikseli. Koordinat çoğu zaman ateşin gerçek konumu değil, tespit pikselinin merkezidir; bir piksede birden fazla anomali olabilir." },
    { term: "Yangın olayı", def: "Ayrı bir yangın olayı kimliği bu uygulamada üretilmez. Aynı olay farklı uydu/geçişlerde yeniden tespit edilebilir — ham MODIS+VIIRS toplamı yangın sayısı değildir." },
    { term: "VIIRS-eşdeğer hotspot tahmini", def: "Harmonize modelin MODIS tespitini VIIRS ölçeğine çeviren tahmini. Sentetik benchmark’ta eğitildi." },
    { term: "FRP (MW)", def: "Fire Radiative Power — tespit anındaki radyatif güç. Yanmış alan veya toplam yangın enerjisi değildir." },
    { term: "Yanmış alan", def: "Bu uygulamada ölçülmez. FIRMS hotspot sayısından türetilmez; ayrı burned-area ürünü gerekir." },
    { term: "Ham seri", def: "MODIS + VIIRS tespitleri raporlandığı gibi. Sensör farkı sayıya yansır." },
    { term: "VIIRS karşılaştırma referansı", def: "Aylık kontrollerde kullanılan karşılaştırma sensörü. Mutlak yer-gerçeği / ground truth değildir." },
    { term: "Standard vs NRT", def: "Standard/science-quality uzun dönem analize daha uygundur. NRT güncel uç dönemdir ve sonra Standard ile değişebilir. daynight alanları birebir değiştirilmemelidir." },
    { term: "H3", def: "Altıgen grid. Res-7 hücre ≈ 5,2 km²." },
    { term: "p-değeri", def: "İstatistiksel test anlamlılığı. Otokorelasyon abartabilir; p<0,05 kesin kırılma değildir. Adım % ile okuyun." },
    { term: "P10/P50/P90 · %90 GA", def: "Model belirsizlik / tahmin aralığı bandı — yangın sayısı için istatistiksel güvence değildir." },
    { term: "Static source filtresi", def: "Kalıcı termal kaynakları azaltabilir; gerçek yangınları yanlışlıkla dışlama garantisi yoktur. Ham/filtreli karşılaştırın." },
    { term: "ROI", def: "Region of Interest — analiz alanı." },
  ],
  en: [
    { term: "FIRMS hotspot detection", def: "A satellite thermal-anomaly pixel. Coordinates are usually the detection pixel center, not the exact fire location; one pixel may contain one or more anomalies." },
    { term: "Fire event", def: "This app does not assign fire-event IDs. The same event can be re-detected across platforms/overpasses — raw MODIS+VIIRS totals are not fire counts." },
    { term: "VIIRS-equivalent hotspot estimate", def: "Harmonizer estimate mapping a MODIS detection toward VIIRS scale. Trained on a synthetic benchmark." },
    { term: "FRP (MW)", def: "Fire Radiative Power at detection time. Not burned area or total fire energy." },
    { term: "Burned area", def: "Not measured here. Do not derive from FIRMS hotspot counts; use a dedicated burned-area product." },
    { term: "Raw series", def: "MODIS + VIIRS detections as reported. Sensor differences affect counts." },
    { term: "VIIRS comparison reference", def: "Comparison sensor used in monthly checks — not absolute ground truth." },
    { term: "Standard vs NRT", def: "Standard/science-quality is preferred for long-term analysis. NRT is a provisional recent tail that may later be replaced. Do not treat daynight fields as interchangeable across products." },
    { term: "H3", def: "Hexagonal grid. Res-7 cell ≈ 5.2 km²." },
    { term: "p-value", def: "Statistical significance. Autocorrelation can inflate it; p<0.05 is not a definitive break. Read with step %." },
    { term: "P10/P50/P90 · 90% band", def: "Model uncertainty / prediction interval — not statistical assurance of fire counts." },
    { term: "Static source filter", def: "May reduce persistent thermal sources; no guarantee against excluding real fires. Compare raw vs filtered." },
    { term: "ROI", def: "Region of Interest — analysis area." },
  ],
};
