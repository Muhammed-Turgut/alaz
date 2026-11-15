import { useState } from "react";

import { GLOSSARY, useT } from "@/lib/i18n";
import styles from "./aether.module.css";

export function Glossary() {
  const t = useT();
  const [open, setOpen] = useState(false);
  const items = GLOSSARY[t.lang] ?? GLOSSARY.tr;

  return (
    <div>
      <button
        type="button"
        className={styles.glossaryToggle}
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        {t.glossaryToggle} {open ? "▾" : "▸"}
      </button>
      {open && (
        <div className={styles.glossaryBox}>
          <h4>{t.glossaryTitle}</h4>
          <dl>
            {items.map((g) => (
              <div key={g.term} className={styles.glossaryItem}>
                <dt>{g.term}</dt>
                <dd>{g.def}</dd>
              </div>
            ))}
          </dl>
        </div>
      )}
    </div>
  );
}
