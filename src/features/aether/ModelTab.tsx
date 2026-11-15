import { useEffect, useState } from "react";

import { useT } from "@/lib/i18n";
import { api } from "./api";
import { fmt, signed } from "./format";
import type { AreaCalendarResult, LatLng, Source } from "./types";
import { AiInsight } from "./AiInsight";
import { Card, EmptyRoi, Hint } from "./ui";
import styles from "./aether.module.css";

interface Props {
  source: Source;
  roi: LatLng[] | null;
  onDraw?: () => void;
}

/** Model sekmesi: sentetik benchmark ile gerçek arşiv karşılaştırmasını ayırır. */
export function ModelTab({ source, roi, onDraw }: Props) {
  const t = useT();
  if (source === "live") {
    return (
      <section>
        <div className={styles.panelHead}><h2>{t.tabs.model}</h2></div>
        <div className={styles.disclaimer} role="note">{t.disclaimerModel}</div>
        <div className={styles.banner}>
          {t.lang === "en"
            ? "Live NASA FIRMS shows ≈24 h only — long-history validation is here under Real archive (TR). Draw a polygon after switching."
            : "Canlı NASA FIRMS yalnızca son 24 saati gösterir; uzun geçmiş doğrulama burada yok. Gerçek arşiv (TR) seçip poligon çiz."}
        </div>
        <SyntheticBenchmarkCard />
        <AiInsight
          focus="model"
          section="overview"
          label={t.tabs.model}
          roi={roi}
          disabledReason={t.lang === "en"
            ? "Use Real archive (TR) + polygon for AI insight."
            : "AI bakışı için Gerçek arşiv (TR) + poligon kullan."}
        />
      </section>
    );
  }

  return <ArchiveModelPanel roi={roi} onDraw={onDraw} />;
}

function SyntheticBenchmarkCard() {
  const t = useT();
  const en = t.lang === "en";
  return (
    <Card title={en
      ? "1) Synthetic benchmark evaluation — known simulation truth"
      : "1) Sentetik benchmark değerlendirmesi — bilinen simülasyon gerçeği"}>
      <div className={styles.disclaimer} role="note">
        {en
          ? "The harmonizer was trained on a physically simulated MODIS↔VIIRS matchup archive (not real NASA FIRMS). Train 2012–2019 · calibration 2020–2021 · hold-out 2022–2026 are synthetic-benchmark year blocks only — not a real-FIRMS hold-out."
          : "Harmonizasyon modeli fiziksel olarak simüle edilmiş MODIS↔VIIRS eşleştirmelerinde eğitildi (gerçek NASA FIRMS değil). Eğitim 2012–2019 · kalibrasyon 2020–2021 · hold-out 2022–2026 yalnızca sentetik benchmark yıl bloklarıdır — gerçek FIRMS hold-out’u değildir."}
      </div>
      <Hint>
        {en
          ? "Geographic generalization is not validated. Turkey real-archive metrics below are a separate check."
          : "Coğrafyaya genelleme doğrulanmadı. Aşağıdaki Türkiye gerçek arşiv metrikleri ayrı bir kontroldür."}
      </Hint>
    </Card>
  );
}

function ArchiveModelPanel({ roi, onDraw }: { roi: LatLng[] | null; onDraw?: () => void }) {
  const t = useT();
  const en = t.lang === "en";
  const [areaVal, setAreaVal] = useState<Record<string, unknown> | null>(null);
  const [areaMeta, setAreaMeta] = useState<AreaCalendarResult["meta"] | null>(null);
  const [areaErr, setAreaErr] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let live = true;
    setAreaVal(null);
    setAreaMeta(null);
    setAreaErr("");
    if (!roi || roi.length < 3) {
      setAreaErr("");
      return () => { live = false; };
    }
    setLoading(true);
    api.areaBreak({ polygon: roi })
      .then((b) => {
        if (!live) return;
        if (b.empty) {
          setAreaErr(b.message || (en ? "No detections in selected area." : "Seçilen alanda tespit yok."));
          return;
        }
        setAreaVal((b.local_validation ?? {}) as Record<string, unknown>);
        setAreaMeta(b.meta ?? null);
      })
      .catch((e: Error) => live && setAreaErr(e.message))
      .finally(() => live && setLoading(false));
    return () => { live = false; };
  }, [roi, en]);

  const needRoi = !roi || roi.length < 3;
  const nMonths = Number(areaVal?.n_months ?? areaVal?.n ?? 0);
  const improved = areaVal?.improved;
  const recommendRaw = areaVal?.recommend_raw === true
    || (typeof areaVal?.rmse_reduction_pct === "number" && (areaVal.rmse_reduction_pct as number) < 0);

  const downloadReport = () => {
    const payload = {
      evaluation_kind: "real_firms_archive_reference_comparison",
      training_kind: "synthetic_benchmark",
      reference: "VIIRS comparison reference (not ground truth)",
      local_validation: areaVal,
      meta: areaMeta,
      exported_at_utc: new Date().toISOString(),
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "alaz-real-archive-validation.json";
    a.click();
    URL.revokeObjectURL(a.href);
  };

  return (
    <section>
      <div className={styles.panelHead}>
        <h2>{en ? "Selected area — real FIRMS" : "Seçilen alan — gerçek FIRMS"}</h2>
      </div>
      <div className={styles.disclaimer} role="note">{t.disclaimerModel}</div>

      <SyntheticBenchmarkCard />

      <Card title={en
        ? "2) Real FIRMS archive — VIIRS comparison reference (not ground truth)"
        : "2) Gerçek FIRMS arşivi — VIIRS karşılaştırma referansı; ground truth değil"}>
        <div className={styles.banner}>
          {en
            ? "Monthly MODIS→VIIRS comparison on your polygon. Not event-level independent validation. VIIRS itself has detection thresholds, cloud and sampling effects."
            : "Poligonunda aylık MODIS→VIIRS karşılaştırması. Olay düzeyinde bağımsız doğrulama değildir. VIIRS’in de eşik, bulut ve örnekleme etkileri vardır."}
        </div>
        {needRoi && (
          <EmptyRoi
            message={en ? "Draw a polygon on the map for validation." : "Doğrulama için haritada bir poligon çiz."}
            onDraw={onDraw}
          />
        )}
        {areaErr && <div className={styles.banner}>{areaErr}</div>}
        {areaMeta && (
          <>
            <div className={styles.qStrip}>
              <div><b>{areaMeta.raw_count?.toLocaleString(en ? "en" : "tr-TR") ?? "-"}</b><span>{en ? "raw detections" : "ham tespit"}</span></div>
              <div><b>{areaMeta.area_km2?.toLocaleString(en ? "en" : "tr-TR") ?? "-"}</b><span>km²</span></div>
              <div><b>{areaMeta.counts?.raw_modis?.toLocaleString(en ? "en" : "tr-TR") ?? "-"}</b><span>MODIS</span></div>
              <div><b>{areaMeta.counts?.raw_viirs?.toLocaleString(en ? "en" : "tr-TR") ?? "-"}</b><span>VIIRS</span></div>
            </div>
            <Hint>
              {areaMeta.date_range?.[0]} → {areaMeta.date_range?.[1]} UTC
              {areaMeta.partial
                ? (en
                  ? ` · archive partial ${areaMeta.done_chunks}/${areaMeta.total_chunks}`
                  : ` · arşiv kısmi ${areaMeta.done_chunks}/${areaMeta.total_chunks}`)
                : (en ? " · archive complete" : " · arşiv tamam")}
              {areaMeta.nrt_tail_note ? ` · ${areaMeta.nrt_tail_note}` : ""}
            </Hint>
            {areaMeta.product_kind_counts && (
              <Hint>
                {en ? "Product kinds: " : "Ürün türleri: "}
                {Object.entries(areaMeta.product_kind_counts as Record<string, number>)
                  .map(([k, v]) => `${k}=${v}`)
                  .join(", ") || (en ? "unknown" : "bilinmiyor")}
              </Hint>
            )}
            <AiInsight focus="model" section="summary" label={en ? "Area summary" : "Alan özeti"} roi={roi} compact />
          </>
        )}
        {areaVal && Object.keys(areaVal).length > 0 && nMonths > 0 && (
          <>
            <table className={styles.tbl}>
              <thead>
                <tr>
                  <th className={styles.l}>{en ? "Metric" : "Metrik"}</th>
                  <th className={styles.cRaw}>{en ? "Raw MODIS" : "Ham MODIS"}</th>
                  <th className={styles.cHarm}>{en ? "Harmonized MODIS" : "Harmonize MODIS"}</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td className={styles.l}>{en ? "RMSE (vs VIIRS reference)" : "RMSE (vs VIIRS referansı)"}</td>
                  <td className={styles.cRaw}>{fmt(Number(areaVal.rmse_raw))}</td>
                  <td className={styles.cHarm}>{fmt(Number(areaVal.rmse_harmonized))}</td>
                </tr>
                <tr>
                  <td className={styles.l}>{en ? "MAE" : "MAE"}</td>
                  <td className={styles.cRaw}>{fmt(Number(areaVal.mae_raw))}</td>
                  <td className={styles.cHarm}>{fmt(Number(areaVal.mae_harmonized))}</td>
                </tr>
                <tr>
                  <td className={styles.l}>{en ? "Relative bias %" : "Göreli sapma %"}</td>
                  <td className={styles.cRaw}>{signed(Number(areaVal.relative_bias_raw_pct))}%</td>
                  <td className={styles.cHarm}>{signed(Number(areaVal.relative_bias_harmonized_pct), 1)}%</td>
                </tr>
                <tr>
                  <td className={styles.l}>{en ? "Correlation" : "Korelasyon"}</td>
                  <td>{Number(areaVal.correlation_raw).toFixed(3)}</td>
                  <td>{Number(areaVal.correlation_harmonized).toFixed(3)}</td>
                </tr>
                <tr>
                  <td className={styles.l}>{en ? "RMSE change" : "RMSE değişimi"}</td>
                  <td colSpan={2}>
                    {signed(Number(areaVal.rmse_reduction_pct), 1)}% · n={fmt(nMonths)} {en ? "months" : "ay"}
                    {nMonths < 6 ? (en ? " (unstable CI)" : " (kararsız GA)") : ""}
                  </td>
                </tr>
                {areaVal.rmse_platform_scaled != null && (
                  <tr>
                    <td className={styles.l}>{en ? "Platform-scaled baseline RMSE" : "Platform ölçekli baseline RMSE"}</td>
                    <td colSpan={2}>{fmt(Number(areaVal.rmse_platform_scaled))}</td>
                  </tr>
                )}
              </tbody>
            </table>
            {recommendRaw && (
              <div className={styles.banner} role="status">
                {en
                  ? "No improvement in this ROI — prefer the Raw series for calendar/briefing instead of the default harmonized analysis."
                  : "Bu ROI’de iyileşme yok — takvim/brifing için varsayılan harmonize yerine Ham seriyi kullan."}
              </div>
            )}
            {improved === true && (
              <Hint>{en ? "Harmonized RMSE improved vs raw for this ROI (reference comparison only)." : "Bu ROI’de harmonize RMSE ham’a göre iyileşti (yalnız referans karşılaştırması)."}</Hint>
            )}
            <ConfidenceSensitivityBlock sens={areaVal.confidence_sensitivity} en={en} />
            <button type="button" className={styles.glossaryToggle} onClick={downloadReport}>
              {en ? "Download validation JSON" : "Doğrulama JSON indir"}
            </button>
            <AiInsight focus="model" section="validation" label={en ? "MODIS → VIIRS reference" : "MODIS → VIIRS referans"} roi={roi} compact />
          </>
        )}
        {loading && <p className={styles.hint}>{en ? "Loading area validation…" : "Alan doğrulaması yükleniyor..."}</p>}
      </Card>
    </section>
  );
}

type ConfPolicyRow = {
  id?: string;
  name?: string;
  modis?: string;
  viirs?: string;
  is_primary?: boolean;
  vs_viirs_same_policy?: {
    n_months?: number;
    rmse?: number;
    relative_bias_pct?: number;
    correlation?: number;
  };
  filter_counts?: { MODIS?: { kept?: number; dropped?: number }; VIIRS?: { kept?: number; dropped?: number } };
};

function ConfidenceSensitivityBlock({ sens, en }: { sens: unknown; en: boolean }) {
  if (!sens || typeof sens !== "object") return null;
  const s = sens as {
    primary_policy?: string;
    auto_select_best?: boolean;
    closest_by_rmse_to_viirs?: string | null;
    selection_note?: string;
    scale_note?: string;
    tradeoff_note?: string;
    policies?: ConfPolicyRow[];
  };
  const rows = s.policies ?? [];
  if (!rows.length) return null;
  return (
    <div style={{ marginTop: "1rem" }}>
      <div className={styles.sect}>
        {en ? "Confidence-filter sensitivity (A/B/C)" : "Güven filtresi duyarlılığı (A/B/C)"}
      </div>
      <Hint>
        {en
          ? "Main result stays on policy A. Closer-to-VIIRS RMSE is diagnostic only — not auto-selected."
          : "Ana sonuç politika A’da kalır. VIIRS’e yakınlık (RMSE) yalnız tanısal — otomatik seçilmez."}
      </Hint>
      <table className={styles.tbl}>
        <thead>
          <tr>
            <th className={styles.l}>{en ? "Policy" : "Politika"}</th>
            <th>{en ? "MODIS / VIIRS rule" : "MODIS / VIIRS kuralı"}</th>
            <th>{en ? "RMSE vs VIIRS" : "RMSE vs VIIRS"}</th>
            <th>{en ? "Rel. bias %" : "Göreli sapma %"}</th>
            <th>n</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((p) => {
            const vs = p.vs_viirs_same_policy ?? {};
            const label = `${p.id ?? "?"} — ${p.name ?? ""}`
              + (p.is_primary ? (en ? " (primary)" : " (birincil)") : "")
              + (s.closest_by_rmse_to_viirs === p.id ? (en ? " · closest RMSE" : " · en yakın RMSE") : "");
            return (
              <tr key={String(p.id)}>
                <td className={styles.l}>{label}</td>
                <td>{p.modis} · {p.viirs}</td>
                <td>{vs.rmse != null ? fmt(vs.rmse) : "—"}</td>
                <td>{vs.relative_bias_pct != null ? `${signed(vs.relative_bias_pct, 1)}%` : "—"}</td>
                <td>{vs.n_months ?? "—"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {s.scale_note && <Hint>{s.scale_note}</Hint>}
      {s.tradeoff_note && <Hint>{s.tradeoff_note}</Hint>}
      {s.selection_note && <Hint>{s.selection_note}</Hint>}
    </div>
  );
}
