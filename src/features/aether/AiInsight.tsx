import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { api } from "./api";
import { Markdown } from "./Markdown";
import type { LatLng } from "./types";
import styles from "./aether.module.css";

export type InsightFocus = "break" | "calendar" | "model" | "briefing";

interface Props {
  focus: InsightFocus;
  /** Tablo / kart kimliği — her blok ayrı yorumlanır */
  section: string;
  /** UI’da görünen kısa etiket */
  label: string;
  roi: LatLng[] | null;
  disabledReason?: string;
  /** Kompakt: kart içi / altı */
  compact?: boolean;
}

/** src/assets/icons/ai_icon.svg — currentColor ile temaya uyar */
function AiIcon({ size = 28 }: { size?: number }) {
  return (
    <svg
      className={styles.aiIcon}
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      width={size}
      height={size}
      aria-hidden="true"
    >
      <path
        fill="currentColor"
        d="m9.96 9.137l.886-3.099c.332-1.16 1.976-1.16 2.308 0l.885 3.099a1.2 1.2 0 0 0 .824.824l3.099.885c1.16.332 1.16 1.976 0 2.308l-3.099.885a1.2 1.2 0 0 0-.824.824l-.885 3.099c-.332 1.16-1.976 1.16-2.308 0l-.885-3.099a1.2 1.2 0 0 0-.824-.824l-3.099-.885c-1.16-.332-1.16-1.976 0-2.308l3.099-.885a1.2 1.2 0 0 0 .824-.824m8.143 7.37c.289-.843 1.504-.844 1.792 0l.026.087l.296 1.188l1.188.297c.96.24.96 1.602 0 1.842l-1.188.297l-.296 1.188c-.24.959-1.603.959-1.843 0l-.297-1.188l-1.188-.297c-.96-.24-.96-1.603 0-1.842l1.188-.297l.297-1.188zm.896 2.29a1 1 0 0 1-.203.203a1 1 0 0 1 .203.203a1 1 0 0 1 .203-.203a1 1 0 0 1-.203-.204M4.104 2.506c.298-.871 1.585-.842 1.818.087l.296 1.188l1.188.297c.96.24.96 1.602 0 1.842l-1.188.297l-.296 1.188c-.24.959-1.603.959-1.843 0l-.297-1.188l-1.188-.297c-.96-.24-.96-1.603 0-1.842l1.188-.297l.297-1.188zM5 4.797a1 1 0 0 1-.203.202A1 1 0 0 1 5 5.203a1 1 0 0 1 .203-.204A1 1 0 0 1 5 4.796"
      />
    </svg>
  );
}

export function AiInsight({ focus, section, label, roi, disabledReason, compact }: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [markdown, setMarkdown] = useState("");
  const [model, setModel] = useState("");
  const [used, setUsed] = useState<Record<string, unknown> | null>(null);
  const [showUsed, setShowUsed] = useState(false);
  const [question, setQuestion] = useState("");
  const [open, setOpen] = useState(!compact);

  const ready = Boolean(roi && roi.length >= 3) && !disabledReason;

  const run = async () => {
    if (!roi || roi.length < 3) {
      setError("Önce haritada bir poligon çiz.");
      return;
    }
    setBusy(true);
    setError("");
    setOpen(true);
    try {
      const r = await api.interpret({
        polygon: roi,
        focus,
        section,
        question: question.trim() || undefined,
      });
      setUsed(r.used_numbers ?? null);
      if (r.empty) {
        setMarkdown("");
        setError(r.message || "Seçilen alanda veri yok.");
        return;
      }
      if (r.error) {
        setMarkdown("");
        setError(r.error);
        return;
      }
      if (!r.available) {
        setMarkdown("");
        setError(r.message || "DeepSeek anahtarı tanımlı değil.");
        return;
      }
      setMarkdown(r.markdown || "");
      setModel(r.model || "");
      if (!r.markdown) setError(r.message || "Boş yanıt.");
    } catch (e) {
      setMarkdown("");
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section
      className={compact ? `${styles.aiPanel} ${styles.aiPanelCompact}` : styles.aiPanel}
      aria-label={`AI bakışı: ${label}`}
    >
      <div className={styles.aiHead}>
        <AiIcon size={compact ? 22 : 28} />
        <div className={styles.aiHeadText}>
          <h3 className={styles.aiTitle}>AI bakışı — {label}</h3>
          {!compact && (
            <p className={styles.aiHint}>
              Yalnızca bu tablo / grafiğin Alaz sayılarına dayanır (grafik resmi gönderilmez).
            </p>
          )}
        </div>
        {compact && ready && (
          <Button className={styles.aiQuickBtn} onClick={() => void run()} disabled={busy}>
            {busy ? "..." : "Cevap üret"}
          </Button>
        )}
      </div>

      {disabledReason ? (
        <p className={styles.hint}>{disabledReason}</p>
      ) : !roi || roi.length < 3 ? (
        <p className={styles.hint}>Cevap için haritada poligon çiz.</p>
      ) : (
        <>
          {!compact && (
            <label className={styles.aiQuestion}>
              İsteğe bağlı soru
              <input
                type="text"
                value={question}
                maxLength={500}
                placeholder={`Örn. Bu «${label}» ne anlatıyor?`}
                onChange={(e) => setQuestion(e.target.value)}
                disabled={busy}
              />
            </label>
          )}
          <div className={styles.aiActions}>
            {!compact && (
              <Button onClick={() => void run()} disabled={busy || !ready}>
                {busy ? "Üretiliyor..." : "Cevap üret"}
              </Button>
            )}
            {compact && (
              <label className={styles.aiQuestionCompact}>
                <input
                  type="text"
                  value={question}
                  maxLength={500}
                  placeholder="İsteğe bağlı soru..."
                  onChange={(e) => setQuestion(e.target.value)}
                  disabled={busy}
                />
              </label>
            )}
            {used && (
              <Button variant="secondary" type="button" onClick={() => setShowUsed((v) => !v)}>
                {showUsed ? "Dayanağı gizle" : "Dayanak"}
              </Button>
            )}
            {(markdown || error) && compact && (
              <Button variant="secondary" type="button" onClick={() => setOpen((v) => !v)}>
                {open ? "Gizle" : "Göster"}
              </Button>
            )}
          </div>
        </>
      )}

      {open && (
        <>
          {error && <p className={styles.errText}>{error}</p>}
          {model && markdown && <p className={styles.hint}>Model: {model}</p>}
          {showUsed && used && (
            <pre className={styles.aiUsed}>{JSON.stringify(used, null, 2)}</pre>
          )}
          {markdown && (
            <div className={styles.aiBody}>
              <Markdown source={markdown} />
            </div>
          )}
        </>
      )}
    </section>
  );
}
