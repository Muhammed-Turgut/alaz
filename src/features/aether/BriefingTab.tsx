import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/Button";
import { api } from "./api";
import { BriefingLoading } from "./BriefingLoading";
import { Markdown } from "./Markdown";
import type { BriefingResult, Scope } from "./types";
import { AiInsight } from "./AiInsight";
import { Card, EmptyRoi, Hint } from "./ui";
import styles from "./aether.module.css";

export function BriefingTab({
  scope,
  tick,
  onDraw,
}: {
  scope: Scope;
  tick: number;
  onDraw?: () => void;
}) {
  const [res, setRes] = useState<BriefingResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const scopeRef = useRef(scope);
  scopeRef.current = scope;

  const needRoi = scope.source === "archive" && (!scope.roi || scope.roi.length < 3);

  const generate = useCallback(async () => {
    const s = scopeRef.current;
    if (s.source === "archive" && (!s.roi || s.roi.length < 3)) {
      setError("Gerçek arşiv brifingi için önce haritada bir poligon çiz (≤ ~50.000 km², Türkiye kutusu içinde).");
      return;
    }
    setBusy(true);
    setError("");
    try {
      setRes(await api.briefing(s));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => { if (tick > 0) void generate(); }, [tick, generate]);

  const download = () => {
    if (!res) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(res.json, null, 2)], { type: "application/json" }));
    const a = Object.assign(document.createElement("a"), { href: url, download: "aether-fire-briefing.json" });
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <section>
      {busy && <BriefingLoading />}
      <div className={styles.panelHead}>
        <h2>Taktik olay komutanı brifingi</h2>
        <div className={styles.inlineCtl}>
          <Button onClick={() => void generate()} disabled={busy || needRoi}>
            {busy ? "Üretiliyor..." : "Mevcut kapsam için üret"}
          </Button>
          <Button variant="secondary" disabled={!res} onClick={() => res && void navigator.clipboard.writeText(res.markdown)}>Markdown kopyala</Button>
          <Button variant="secondary" disabled={!res} onClick={download}>JSON indir</Button>
        </div>
      </div>
      {needRoi && (
        <EmptyRoi
          message="Arşiv brifingi için haritada bir poligon çiz (≤ ~50.000 km²)."
          onDraw={onDraw}
        />
      )}
      <Card>
        {error && <p className={styles.errText}>{error}</p>}
        {res
          ? <Markdown source={res.markdown} />
          : !needRoi && (
            <Hint>
              Geçerli bölge, pencere ve (çizildiyse) ROI poligonu için brifing üret. Brifing yalnızca hesaplanmış sayılardan oluşur.
            </Hint>
          )}
      </Card>

      <AiInsight
        focus="briefing"
        section="overview"
        label="Brifing özeti"
        roi={scope.roi}
        compact={scope.source === "archive" && Boolean(scope.roi && scope.roi.length >= 3)}
        disabledReason={
          scope.source === "live"
            ? "Arşiv AI bakışı için Gerçek arşiv (TR) + poligon kullan. (Canlı brifing ayrı.)"
            : undefined
        }
      />
    </section>
  );
}
