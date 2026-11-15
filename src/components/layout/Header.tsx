import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

import logo from "@/assets/images/logo.png";
import { siteConfig } from "@/config/site";
import { useDataSource } from "@/features/aether/sourceContext";
import { useT } from "@/lib/i18n";
import { setPrefs, usePrefs, type CalRamp, type Lang, type MapStyle } from "@/lib/preferences";
import styles from "./Header.module.css";

const MAP_STYLES: { v: MapStyle; label: string }[] = [
  { v: "auto", label: "Auto / Otomatik" },
  { v: "light", label: "Light" },
  { v: "dark", label: "Dark" },
  { v: "satellite", label: "Satellite" },
];

const CAL_RAMPS: { v: CalRamp; labelKey: "calClassic" | "calCb" }[] = [
  { v: "default", labelKey: "calClassic" },
  { v: "colorblind", labelKey: "calCb" },
];

export function Header() {
  const prefs = usePrefs();
  const t = useT();
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

  const setLang = (lang: Lang) => setPrefs({ lang });

  return (
    <header className={styles.header}>
      <Link to="/" className={styles.brand} aria-label={siteConfig.name}>
        <img src={logo} alt={siteConfig.name} className={styles.logo} />
      </Link>

      <div className={styles.actions} ref={box}>
        {dataSource && (
          <div className={styles.sourceSwitch} role="group" aria-label="Data source">
            <button
              type="button"
              className={`${styles.sourceBtn} ${dataSource.source === "live" ? styles.sourceOn : ""}`}
              onClick={() => dataSource.onSource("live")}
            >
              {t.sourceLive}
            </button>
            <button
              type="button"
              className={`${styles.sourceBtn} ${dataSource.source === "archive" ? styles.sourceOn : ""}`}
              onClick={() => dataSource.onSource("archive")}
            >
              {t.sourceArchive}
            </button>
          </div>
        )}
        <div className={styles.langSwitch} role="group" aria-label={t.langLabel}>
          <button type="button" className={`${styles.langBtn} ${prefs.lang === "tr" ? styles.langOn : ""}`} onClick={() => setLang("tr")}>TR</button>
          <button type="button" className={`${styles.langBtn} ${prefs.lang === "en" ? styles.langOn : ""}`} onClick={() => setLang("en")}>EN</button>
        </div>
        <button
          type="button"
          className={styles.btn}
          onClick={() => setPrefs({ theme: prefs.theme === "dark" ? "light" : "dark" })}
          aria-label={t.theme}
        >
          {t.theme}: {prefs.theme === "dark" ? t.themeDark : t.themeLight}
        </button>
        <button
          type="button"
          className={`${styles.btn} ${open ? styles.btnOn : ""}`}
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          aria-haspopup="true"
        >
          {t.settings}
        </button>

        {open && (
          <div className={styles.panel} role="dialog" aria-label={t.settings}>
            <div className={styles.panelTitle}>{t.mapStyle}</div>
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
            <div className={styles.panelTitle} style={{ marginTop: "var(--space-3)" }}>{t.calColors}</div>
            <div className={styles.opts}>
              {CAL_RAMPS.map((o) => (
                <button
                  key={o.v}
                  type="button"
                  className={`${styles.opt} ${prefs.calRamp === o.v ? styles.optOn : ""}`}
                  onClick={() => setPrefs({ calRamp: o.v })}
                >
                  {t[o.labelKey]}
                </button>
              ))}
            </div>
            <p className={styles.panelHint}>{t.settingsHint}</p>
          </div>
        )}
      </div>
    </header>
  );
}
