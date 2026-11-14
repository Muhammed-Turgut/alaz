import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

import logo from "@/assets/images/logo.png";
import { siteConfig } from "@/config/site";
import { useDataSource } from "@/features/aether/sourceContext";
import { setPrefs, usePrefs, type CalRamp, type MapStyle } from "@/lib/preferences";
import styles from "./Header.module.css";

const MAP_STYLES: { v: MapStyle; label: string }[] = [
  { v: "auto", label: "Otomatik" },
  { v: "light", label: "Açık" },
  { v: "dark", label: "Koyu" },
  { v: "satellite", label: "Uydu" },
];

const CAL_RAMPS: { v: CalRamp; label: string }[] = [
  { v: "default", label: "Klasik" },
  { v: "colorblind", label: "Renk körü" },
];

export function Header() {
  const prefs = usePrefs();
  const dataSource = useDataSource();
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false); };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDown); document.removeEventListener("keydown", onKey); };
  }, [open]);

  return (
    <header className={styles.header}>
      <Link to="/" className={styles.brand} aria-label={siteConfig.name}>
        <img src={logo} alt={siteConfig.name} className={styles.logo} />
      </Link>

      <div className={styles.actions} ref={box}>
        {dataSource && (
          <div className={styles.sourceSwitch} role="group" aria-label="Veri kaynağı">
            <button
              type="button"
              className={`${styles.sourceBtn} ${dataSource.source === "live" ? styles.sourceOn : ""}`}
              onClick={() => dataSource.onSource("live")}
            >
              Canlı NASA FIRMS
            </button>
            <button
              type="button"
              className={`${styles.sourceBtn} ${dataSource.source === "archive" ? styles.sourceOn : ""}`}
              onClick={() => dataSource.onSource("archive")}
            >
              Gerçek arşiv (TR)
            </button>
          </div>
        )}
        <button
          type="button"
          className={styles.btn}
          onClick={() => setPrefs({ theme: prefs.theme === "dark" ? "light" : "dark" })}
          aria-label="Temayı değiştir"
        >
          Tema: {prefs.theme === "dark" ? "Koyu" : "Açık"}
        </button>
        <button
          type="button"
          className={`${styles.btn} ${open ? styles.btnOn : ""}`}
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          aria-haspopup="true"
        >
          Ayarlar
        </button>

        {open && (
          <div className={styles.panel} role="dialog" aria-label="Ayarlar">
            <div className={styles.panelTitle}>Harita stili</div>
            <div className={styles.opts}>
              {MAP_STYLES.map((o) => (
                <button
                  key={o.v}
                  type="button"
                  className={`${styles.opt} ${prefs.mapStyle === o.v ? styles.optOn : ""}`}
                  onClick={() => setPrefs({ mapStyle: o.v })}
                >
                  {o.label}
                </button>
              ))}
            </div>
            <div className={styles.panelTitle} style={{ marginTop: "var(--space-3)" }}>Takvim renkleri</div>
            <div className={styles.opts}>
              {CAL_RAMPS.map((o) => (
                <button
                  key={o.v}
                  type="button"
                  className={`${styles.opt} ${prefs.calRamp === o.v ? styles.optOn : ""}`}
                  onClick={() => setPrefs({ calRamp: o.v })}
                >
                  {o.label}
                </button>
              ))}
            </div>
            <p className={styles.panelHint}>
              Otomatik harita: temaya göre. Renk körü rampa kırmızı-yeşil bağımsızdır. Seçimler bu tarayıcıda saklanır.
            </p>
          </div>
        )}
      </div>
    </header>
  );
}
