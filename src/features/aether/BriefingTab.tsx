import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/Button";
import { api } from "./api";
import { BriefingLoading } from "./BriefingLoading";
import { Markdown } from "./Markdown";
import type { BriefingAudience, BriefingResult, Scope } from "./types";
import { AiInsight } from "./AiInsight";
import { Card, EmptyRoi, Hint, Seg } from "./ui";
import styles from "./aether.module.css";

const AUDIENCES: { v: BriefingAudience; label: string }[] = [
  { v: "acil", label: "Acil" },
  { v: "erken_uyari", label: "Erken uyarı" },
  { v: "arazi", label: "Arazi" },
  { v: "bilim", label: "Bilim" },
  { v: "itfaiye", label: "İtfaiye" },
];

function downloadBlob(text: string, filename: string, type: string) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = Object.assign(document.createElement("a"), { href: url, download: filename });
  a.click();
  URL.revokeObjectURL(url);
}

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
  const [csvBusy, setCsvBusy] = useState(false);
  const [error, setError] = useState("");
  const [audience, setAudience] = useState<BriefingAudience>("acil");
  const scopeRef = useRef(scope);
  scopeRef.current = scope;

  const needRoi = scope.source === "archive" && (!scope.roi || scope.roi.length < 3);
  const canCsv = Boolean(scope.roi && scope.roi.length >= 3 && (scope.source === "live" || scope.source === "archive"));

  const generate = useCallback(async () => {
    const s = scopeRef.current;
    if (s.source === "archive" && (!s.roi || s.roi.length < 3)) {
      setError("Gerçek arşiv brifingi için önce haritada bir poligon çiz (≤ ~120.000 km², Türkiye kutusu içinde).");
      return;
    }
    setBusy(true);
    setError("");
    try {
      setRes(await api.briefing(s, audience));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }, [audience]);

  useEffect(() => { if (tick > 0) void generate(); }, [tick, generate]);

  const downloadJson = () => {
    if (!res) return;
    downloadBlob(JSON.stringify(res.json, null, 2), "aether-fire-briefing.json", "application/json");
  };

  const downloadCsv = async () => {
    if (!scope.roi || scope.roi.length < 3) return;
    if (scope.source !== "live" && scope.source !== "archive") return;
    setCsvBusy(true);
    setError("");
    try {
      const { text, n, truncated } = await api.exportHotspotsCsv({
        source: scope.source,
        polygon: scope.roi,
        days: scope.days,
      });
      downloadBlob(text, `alaz-hotspots-${scope.source}-${n}.csv`, "text/csv;charset=utf-8");
      if (truncated) setError(`CSV ilk ${n.toLocaleString("tr-TR")} satırla sınırlandı (üst sınır).`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setCsvBusy(false);
    }
  };

  return (
    <section>
      {busy && <BriefingLoading />}
      <div className={styles.panelHead}>
        <h2>Hedef kitle brifingi</h2>
        <div className={styles.inlineCtl}>
          <Seg value={audience} onChange={setAudience} options={AUDIENCES} />
          <Button onClick={() => void generate()} disabled={busy || needRoi}>
            {busy ? "Üretiliyor..." : "Mevcut kapsam için üret"}
          </Button>
          <Button variant="secondary" disabled={!res} onClick={() => res && void navigator.clipboard.writeText(res.markdown)}>
            Markdown kopyala
          </Button>
          <Button variant="secondary" disabled={!res} onClick={downloadJson}>JSON indir</Button>
          <Button variant="secondary" disabled={!canCsv || csvBusy} onClick={() => void downloadCsv()}>
            {csvBusy ? "CSV..." : "Hotspot CSV"}
          </Button>
        </div>
      </div>
      <Hint>
        Kitle: acil müdahale, erken uyarı, arazi yönetimi, bilim veya itfaiye — aynı sayılar, farklı vurgu.
        CSV: seçili ROI içindeki FIRMS sıcak noktaları.
      </Hint>
      {needRoi && (
        <EmptyRoi
          message="Arşiv brifingi için haritada bir poligon çiz (≤ ~120.000 km²)."
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
