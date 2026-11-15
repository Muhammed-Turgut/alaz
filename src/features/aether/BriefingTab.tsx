import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/Button";
import { useT } from "@/lib/i18n";
import { usePrefs } from "@/lib/preferences";
import { api } from "./api";
import { BriefingLoading } from "./BriefingLoading";
import { Markdown } from "./Markdown";
import type { BriefingAudience, BriefingResult, Scope } from "./types";
import { AiInsight } from "./AiInsight";
import { Card, EmptyRoi, Hint, Seg } from "./ui";
import styles from "./aether.module.css";

const AUDIENCES_TR: { v: BriefingAudience; label: string }[] = [
  { v: "acil", label: "Acil" },
  { v: "erken_uyari", label: "Erken uyarı" },
  { v: "arazi", label: "Arazi" },
  { v: "bilim", label: "Bilim" },
  { v: "itfaiye", label: "İtfaiye" },
];
const AUDIENCES_EN: { v: BriefingAudience; label: string }[] = [
  { v: "acil", label: "Emergency" },
  { v: "erken_uyari", label: "Early warning" },
  { v: "arazi", label: "Field" },
  { v: "bilim", label: "Science" },
  { v: "itfaiye", label: "Fire service" },
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
  const { lang } = usePrefs();
  const t = useT();
  const scopeRef = useRef(scope);
  scopeRef.current = scope;

  const needRoi = scope.source === "archive" && (!scope.roi || scope.roi.length < 3);
  const canCsv = Boolean(scope.roi && scope.roi.length >= 3 && (scope.source === "live" || scope.source === "archive"));
  const audiences = lang === "en" ? AUDIENCES_EN : AUDIENCES_TR;

  const generate = useCallback(async () => {
    const s = scopeRef.current;
    if (s.source === "archive" && (!s.roi || s.roi.length < 3)) {
      setError(lang === "en"
        ? "For archive briefing, draw a polygon first (≤ ~120,000 km², anywhere worldwide)."
        : "Gerçek arşiv brifingi için önce haritada bir poligon çiz (≤ ~120.000 km², dünya geneli).");
      return;
    }
    setBusy(true);
    setError("");
    try {
      setRes(await api.briefing(s, audience, lang));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }, [audience, lang]);

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
        <h2>{lang === "en" ? "Audience pre-screen summary" : "Hedef kitle ön tarama özeti"}</h2>
        <div className={styles.inlineCtl}>
          <Seg value={audience} onChange={setAudience} options={audiences} />
          <Button onClick={() => void generate()} disabled={busy || needRoi} title={needRoi ? (lang === "en" ? "Draw a polygon first" : "Önce poligon çiz") : undefined}>
            {busy ? (lang === "en" ? "Generating…" : "Üretiliyor...") : (lang === "en" ? "Generate for current scope" : "Mevcut kapsam için üret")}
          </Button>
          <Button variant="secondary" disabled={!res} onClick={() => res && void navigator.clipboard.writeText(res.markdown)}>
            {lang === "en" ? "Copy Markdown" : "Markdown kopyala"}
          </Button>
          <Button variant="secondary" disabled={!res} onClick={downloadJson}>{lang === "en" ? "Download JSON" : "JSON indir"}</Button>
          <Button variant="secondary" disabled={!canCsv || csvBusy} onClick={() => void downloadCsv()} title={!canCsv ? (lang === "en" ? "ROI required" : "ROI gerekli") : undefined}>
            {csvBusy ? "CSV..." : "Hotspot CSV"}
          </Button>
        </div>
      </div>
      <div className={styles.disclaimer} role="note">{t.disclaimerBriefing}</div>
      <Hint>
        {lang === "en"
          ? "Audience: emergency, early warning, field, science, or fire service — same numbers, different emphasis (not orders). CSV: FIRMS hotspot detections inside the ROI. Language: TR/EN in the header."
          : "Kitle: acil, erken uyarı, arazi, bilim veya itfaiye — aynı sayılar, farklı vurgu (talimat değil). CSV: seçili ROI içindeki FIRMS sıcak nokta tespitleri. Dil: üst bardaki TR/EN."}
      </Hint>
      {needRoi && (
        <EmptyRoi
          message={lang === "en" ? "Draw a polygon for archive briefing (≤ ~120,000 km²)." : "Arşiv brifingi için haritada bir poligon çiz (≤ ~120.000 km²)."}
          onDraw={onDraw}
        />
      )}
      <Card>
        {error && <p className={styles.errText}>{error}</p>}
        {res
          ? <Markdown source={res.markdown} />
          : !needRoi && (
            <Hint>
              {lang === "en"
                ? "Generate a briefing for the current region, window, and ROI (if drawn). Briefings use computed numbers only."
                : "Geçerli bölge, pencere ve (çizildiyse) ROI poligonu için brifing üret. Brifing yalnızca hesaplanmış sayılardan oluşur."}
            </Hint>
          )}
      </Card>

      <AiInsight
        focus="briefing"
        section="overview"
        label={lang === "en" ? "Briefing summary" : "Brifing özeti"}
        roi={scope.roi}
        compact={scope.source === "archive" && Boolean(scope.roi && scope.roi.length >= 3)}
        disabledReason={
          scope.source === "live"
            ? (lang === "en" ? "Use Real archive (TR) + polygon for archive AI insight." : "Arşiv AI bakışı için Gerçek arşiv (TR) + poligon kullan. (Canlı brifing ayrı.)")
            : undefined
        }
      />
    </section>
  );
}
