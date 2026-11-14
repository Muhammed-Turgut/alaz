import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/Button";
import { api } from "./api";
import { Markdown } from "./Markdown";
import type { BriefingResult, Scope } from "./types";
import { Card, Hint } from "./ui";
import styles from "./aether.module.css";

export function BriefingTab({ scope, tick }: { scope: Scope; tick: number }) {
  const [res, setRes] = useState<BriefingResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const scopeRef = useRef(scope);
  scopeRef.current = scope;

  const generate = useCallback(async () => {
    setBusy(true);
    setError("");
    try {
      setRes(await api.briefing(scopeRef.current));
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
      <div className={styles.panelHead}>
        <h2>Taktik olay komutanı brifingi</h2>
        <div className={styles.inlineCtl}>
          <Button onClick={() => void generate()} disabled={busy}>{busy ? "Üretiliyor..." : "Mevcut kapsam için üret"}</Button>
          <Button variant="secondary" disabled={!res} onClick={() => res && void navigator.clipboard.writeText(res.markdown)}>Markdown kopyala</Button>
          <Button variant="secondary" disabled={!res} onClick={download}>JSON indir</Button>
        </div>
      </div>
      <Card>
        {error && <p className={styles.errText}>{error}</p>}
        {res
          ? <Markdown source={res.markdown} />
          : <Hint>Geçerli bölge, pencere ve (çizildiyse) ROI poligonu için brifing üret. Brifing yalnızca hesaplanmış sayılardan oluşur.</Hint>}
      </Card>
    </section>
  );
}
