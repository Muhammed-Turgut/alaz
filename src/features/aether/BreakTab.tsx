import { useCallback, useEffect, useMemo, useState } from "react";

import { api } from "./api";
import { breakChart } from "./charts";
import { fmt, fmtP, signed } from "./format";
import type { BreakPayload, BreakReport, LatLng, SeriesPayload, Source } from "./types";
import { useT } from "@/lib/i18n";
import { AiInsight } from "./AiInsight";
import { Badge, Card, ChartBox, EmptyRoi, Seg, Hint } from "./ui";
import styles from "./aether.module.css";

const NAMES: Record<keyof BreakPayload["reports"], { label: string; cls: string }> = {
  raw_all: { label: "Ham, raporlandığı gibi", cls: styles.cRaw },
  raw_filtered: { label: "Ham, kaynaklar çıkarılmış", cls: styles.cRaw },
  harmonized: { label: "Harmonize (VIIRS-eşd.)", cls: styles.cHarm },
  truth: { label: "VIIRS referansı (karşılaştırma)", cls: styles.cTruth },
};

const PCell = ({ p }: { p: number }) => (
  <td>
    {fmtP(p)}{" "}
    <Badge tone={p < 0.05 ? "warn" : "ok"} title="p&lt;0.05 otokorelasyonlu serilerde abartılı olabilir">
      {p < 0.05 ? "p&lt;0,05" : "p≥0,05"}
    </Badge>
  </td>
);

function roiFingerprint(roi: LatLng[] | null): string {
  if (!roi || roi.length < 3) return "";
  return roi.map(([a, b]) => `${a.toFixed(4)},${b.toFixed(4)}`).join("|");
}

interface Props {
  source: Source;
  roi: LatLng[] | null;
  onDraw?: () => void;
}

export function BreakTab({ source, roi, onDraw }: Props) {
  const t = useT();
  const [freq, setFreq] = useState<"monthly" | "annual">("monthly");
  const [series, setSeries] = useState<SeriesPayload | null>(null);
  const [brk, setBrk] = useState<BreakPayload | null>(null);
  const [error, setError] = useState("");
  const [metaHint, setMetaHint] = useState("");
  const [busy, setBusy] = useState(false);
  const [loadedScope, setLoadedScope] = useState("");

  const wantScope = useMemo(() => {
    if (source === "archive") return `archive:${roiFingerprint(roi)}:${freq}`;
    return "live";
  }, [source, roi, freq]);

  useEffect(() => {
    let live = true;
    setError("");
    setSeries(null);
    setBrk(null);
    setMetaHint("");
    setLoadedScope("");
    setBusy(false);

    if (source === "live") {
      setError(
        "Canlı FIRMS yalnızca son ~24 saattir; 2001–2026 kırılma grafiği burada yok. "
        + "Gerçek arşiv (TR) seçip poligon çiz → Yapısal kırılma.",
      );
      return () => { live = false; };
    }

    if (!roi || roi.length < 3) {
      setError("");
      return () => { live = false; };
    }
    setBusy(true);
    const scopeAtStart = wantScope;
    Promise.all([api.areaSeries({ polygon: roi, freq }), api.areaBreak({ polygon: roi })])
      .then(([s, b]) => {
        if (!live) return;
        if (s.empty || b.empty) {
          setError(s.message || b.message || "Seçilen alanda tespit yok.");
          return;
        }
        if (b.region !== "archive:tr") {
          setError("Beklenen ROI arşiv yanıtı gelmedi; sayfayı yenile.");
          return;
        }
        setSeries(s);
        setBrk(b);
        setLoadedScope(scopeAtStart);
        const m = s.meta || b.meta;
        const step = b.reports.raw_filtered.step;
        if (m) {
          setMetaHint(
            `GERÇEK ROI · ~${m.area_km2?.toLocaleString("tr-TR")} km² · ham ${m.raw_count?.toLocaleString("tr-TR")} `
            + `· ${m.date_range?.[0]} → ${m.date_range?.[1]} `
            + `· 2012 öncesi aylık ort. ${fmt(step.pre_mean, 0)} → sonrası ${fmt(step.post_mean, 0)}`,
          );
        }
      })
      .catch((e: Error) => live && setError(e.message))
      .finally(() => live && setBusy(false));
    return () => { live = false; };
  }, [wantScope, source, roi, freq]);

  const draw = useCallback(
    (c: HTMLCanvasElement) => breakChart(c, series!, freq, brk, { archive: true }),
    [series, freq, brk],
  );

  const show = Boolean(series && brk && loadedScope === wantScope);
  const needRoi = source === "archive" && (!roi || roi.length < 3);

  return (
    <section>
      <div className={styles.panelHead}>
        <h2>2012 sensör geçişinde yapısal kırılma</h2>
        <div className={styles.inlineCtl}>
          <Seg value={freq} onChange={setFreq} options={[{ v: "monthly", label: "Aylık" }, { v: "annual", label: "Yıllık" }]} />
        </div>
      </div>

      <div className={styles.disclaimer} role="note">{t.disclaimerBreak}</div>
      {source === "archive" && (
        <div className={styles.banner}>
          Bu sekme çizdiğin poligonun gerçek NASA FIRMS geçmişini kullanır. Yüzde adımlar benzer görünebilir;
          mutlak sayılara (aylık ortalama, ham tespit, km²) bak.
        </div>
      )}
      {needRoi && (
        <EmptyRoi
          message="Kırılma analizi için haritada bir poligon çiz (≤ ~120.000 km²)."
          onDraw={onDraw}
        />
      )}
      {metaHint && (
        <div className={styles.bannerInfo}>
          <div className={styles.banner}>{metaHint}</div>
        </div>
      )}
      {error && <div className={styles.banner}>{error}</div>}
      {busy && <p className={styles.hint}>Seçilen kapsam için seri hesaplanıyor...</p>}

      {show ? (
        <>
          <ChartBox draw={draw} height={400} />
          <AiInsight
            focus="break"
            section="chart"
            label="Kırılma grafiği"
            roi={roi}
            compact
          />
          <div className={styles.grid2}>
            <Card title="İstatistik testleri (etki + p; p tek başına yeterli değil)">
              <div className={styles.tblScroll}>
                <table className={styles.tbl}>
                  <thead>
                    <tr>
                      <th className={styles.l}>Seri</th>
                      <th>2012 adımı %</th>
                      <th title="Yıllık Pettitt değişim-noktası testi">Pettitt yıllık</th>
                      <th title="2012’de yapısal kırılma (Chow)">Chow yıllık</th>
                      <th title="Aylık Chow @2012">Chow aylık</th>
                      <th title="2012 civarı ±6 yıl yerel Pettitt">Pettitt ±6 y</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(Object.entries(brk!.reports) as [keyof BreakPayload["reports"], BreakReport][]).map(([k, r]) => (
                      <tr key={k}>
                        <td className={`${styles.l} ${NAMES[k].cls}`}>{NAMES[k].label}</td>
                        <td className={(r.step.step_pct ?? 0) > 25 ? styles.cRaw : undefined}>
                          {r.step.step_pct == null ? "-" : `${signed(r.step.step_pct, 1)}%`}
                        </td>
                        <PCell p={r.pettitt_annual.p_value} /><PCell p={r.chow_annual.p_value} />
                        <PCell p={r.chow_monthly.p_value} /><PCell p={r.pettitt_local.p_value} />
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <Hint>
                {t.lang === "en"
                  ? "p<0.05 is not a definitive break: autocorrelation can inflate p-values. Read with step % and period means. Pre-VIIRS reference is missing (not zero activity) — comparison reference only. Raw step: "
                  : "p<0,05 = “kesin kırılma” değildir: otokorelasyon p-değerlerini abartabilir. Adım % ve dönem ortalamalarıyla birlikte okuyun. VIIRS referansı VIIRS öncesi dönemde yoktur (sıfır aktivite değil) — karşılaştırma referansıdır. Ham adım: "}
                {fmt(brk!.reports.raw_filtered.step.pre_mean, 0)} → {fmt(brk!.reports.raw_filtered.step.post_mean, 0)} / {t.lang === "en" ? "mo" : "ay"}.
                {brk!.verdict.pvalue_caveat ? ` ${brk!.verdict.pvalue_caveat}` : ""}
              </Hint>
              <AiInsight focus="break" section="stats" label="İstatistik testleri" roi={roi} compact />
            </Card>
            <Card title="Yorum">
              <p className={styles.prose}>
                <Badge tone={brk!.verdict.raw_break_at_transition ? "bad" : "ok"}>
                  ham: {brk!.verdict.raw_break_at_transition ? "sensör geçişinde yapay kırılma" : "kırılma yok"}
                </Badge>{" "}
                <Badge tone={brk!.verdict.harmonized_step_eliminated ? "ok" : "bad"}>
                  harmonize: {brk!.verdict.harmonized_step_eliminated ? "adım ortadan kalktı" : "adım sürüyor"}
                </Badge>
              </p>
              <ul className={styles.prose}>
                {brk!.verdict.notes.map((n) => <li key={n}>{n}</li>)}
              </ul>
              <AiInsight focus="break" section="verdict" label="Makine yorumu" roi={roi} compact />
            </Card>
          </div>
          <Card title={t.lang === "en" ? "What this shows" : "Bu ne gösteriyor"}>
            <p className={styles.prose}>
              {t.lang === "en"
                ? "VIIRS (375 m) began 2012-01-20. Raw MODIS+VIIRS counts create an artificial jump; the harmonized (VIIRS-equivalent) series tries to close it — model trained on a synthetic benchmark. The VIIRS column is a comparison reference, not ground truth. Hotspot counts are not fire counts or burned area. Charts use only real FIRMS rows inside your ROI."
                : "VIIRS (375 m) 2012-01-20'de hizmete girdi. Ham MODIS+VIIRS sayımı yapay bir sıçrama üretir; harmonize (VIIRS-eşdeğer) seri bunu kapatmaya çalışır — model sentetik benchmark’ta eğitildi. VIIRS sütunu karşılaştırma referansıdır, mutlak yer-gerçeği değildir. Sıcak nokta sayısı yangın sayısı veya yanmış alan değildir. Grafik ve tablolar yalnızca çizdiğin ROI içindeki gerçek FIRMS kayıtlarından hesaplanır."}
            </p>
            <AiInsight focus="break" section="explain" label={t.lang === "en" ? "What this shows" : "Bu ne gösteriyor"} roi={roi} compact />
          </Card>
        </>
      ) : !error && !busy && !needRoi && <p className={styles.hint}>Yükleniyor...</p>}

      {source === "live" && (
        <AiInsight
          focus="break"
          section="overview"
          label="Kırılma"
          roi={roi}
          disabledReason="AI bakışı için Gerçek arşiv (TR) + poligon kullan."
        />
      )}
    </section>
  );
}
