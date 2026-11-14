import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

import logo from "@/assets/images/logo.png";
import { siteConfig } from "@/config/site";
import { setPrefs, usePrefs, type MapStyle } from "@/lib/preferences";
import styles from "./Header.module.css";

const MAP_STYLES: { v: MapStyle; label: string }[] = [
  { v: "auto", label: "Otomatik" },
  { v: "light", label: "Açık" },
  { v: "dark", label: "Koyu" },
  { v: "satellite", label: "Uydu" },
];

export function Header() {
  const prefs = usePrefs();
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
            <p className={styles.panelHint}>
              Otomatik: açık temada açık, koyu temada koyu harita. Seçimin bu tarayıcıda saklanır.
            </p>
          </div>
        )}
      </div>
    </header>
  );
}
