import { useEffect, useState } from "react";

import { useT } from "@/lib/i18n";
import type { LatLng } from "./types";
import styles from "./aether.module.css";

/** Jury demo ROI — Antalya / Manavgat coast box (name + coords shown; not auto-selected). */
export const DEMO_ROI: { name: string; nameEn: string; polygon: LatLng[]; note: string; noteEn: string } = {
  name: "Demo ROI — Manavgat / Antalya kıyısı",
  nameEn: "Demo ROI — Manavgat / Antalya coast",
  polygon: [
    [36.55, 31.20],
    [36.55, 31.75],
    [37.05, 31.75],
    [37.05, 31.20],
    [36.55, 31.20],
  ],
  note: "≈ 36.55–37.05°N, 31.20–31.75°E · yerel TR disk arşivi (hızlı demo)",
  noteEn: "≈ 36.55–37.05°N, 31.20–31.75°E · local TR disk archive (fast demo)",
};

const STEPS_TR = [
  "Kaynak: Gerçek FIRMS arşivi — dünya geneli ROI (TR = yerel disk; dışı on-demand API).",
  "Haritada herhangi bir yerde poligon çiz veya aşağıdaki demo ROI’yi yükle (gizli otomatik seçim yok).",
  "Akış: Harita → Kırılma → Takvim → Model doğrulama. Her adımda tarih aralığı ve model sınırını oku.",
  "Hotspot ≠ yangın sayısı ≠ yanmış alan. Harmonizasyon sentetik benchmark’ta eğitildi.",
];

const STEPS_EN = [
  "Source: Real FIRMS archive — global ROI (TR = local disk; elsewhere = on-demand API).",
  "Draw a polygon anywhere or load the demo ROI below (never auto-selected silently).",
  "Flow: Map → Break → Calendar → Model validation. Read the date range and model limits at each step.",
  "Hotspot ≠ fire count ≠ burned area. Harmonization was trained on a synthetic benchmark.",
];

interface Props {
  onLoadDemoRoi: (pts: LatLng[]) => void;
  sourceIsArchive: boolean;
}

export function DemoGuide({ onLoadDemoRoi, sourceIsArchive }: Props) {
  const t = useT();
  const en = t.lang === "en";
  const key = "alaz.demoGuide.dismissed";
  const [open, setOpen] = useState(false);

  useEffect(() => {
    try {
      if (localStorage.getItem(key) !== "1") setOpen(true);
    } catch {
      setOpen(true);
    }
  }, []);

  if (!open) {
    return (
      <button
        type="button"
        className={styles.glossaryToggle}
        onClick={() => setOpen(true)}
      >
        {en ? "Demo guide" : "Başlangıç / demo"}
      </button>
    );
  }

  const steps = en ? STEPS_EN : STEPS_TR;

  return (
    <div className={styles.glossaryBox} role="region" aria-label={en ? "Demo guide" : "Demo rehberi"}>
      <h4>{en ? "Start / jury demo (≈2–3 min)" : "Başlangıç / jüri demosu (≈2–3 dk)"}</h4>
      <ol className={styles.demoList}>
        {steps.map((s) => (
          <li key={s}>{s}</li>
        ))}
      </ol>
      {!sourceIsArchive && (
        <p className={styles.hint}>
          {en
            ? "Switch the header source to Real archive (global) before loading the demo ROI."
            : "Demo ROI öncesi üst bardan Gerçek arşiv (küresel) seç."}
        </p>
      )}
      <p className={styles.hint}>
        <strong>{en ? DEMO_ROI.nameEn : DEMO_ROI.name}</strong>
        <br />
        {en ? DEMO_ROI.noteEn : DEMO_ROI.note}
      </p>
      <div className={styles.demoActions}>
        <button
          type="button"
          className={styles.glossaryToggle}
          disabled={!sourceIsArchive}
          title={!sourceIsArchive ? (en ? "Select Real archive (global) first" : "Önce Gerçek arşiv (küresel) seç") : undefined}
          onClick={() => onLoadDemoRoi(DEMO_ROI.polygon)}
        >
          {en ? "Load demo ROI" : "Demo ROI yükle"}
        </button>
        <button
          type="button"
          className={styles.glossaryToggle}
          onClick={() => {
            try { localStorage.setItem(key, "1"); } catch { /* ignore */ }
            setOpen(false);
          }}
        >
          {en ? "Dismiss" : "Kapat"}
        </button>
      </div>
    </div>
  );
}
