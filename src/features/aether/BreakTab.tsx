import { useCallback, useEffect, useState } from "react";

import { api } from "./api";
import { breakChart } from "./charts";
import { fmt, fmtP, signed } from "./format";
import type { BreakPayload, BreakReport, Region, SeriesPayload } from "./types";
import { Badge, Card, ChartBox, Seg, Hint } from "./ui";
import styles from "./aether.module.css";

const NAMES: Record<keyof BreakPayload["reports"], { label: string; cls: string }> = {
  raw_all: { label: "Ham, raporlandığı gibi", cls: styles.cRaw },
  raw_filtered: { label: "Ham, kaynaklar çıkarılmış", cls: styles.cRaw },
  harmonized: { label: "Harmonize", cls: styles.cHarm },
  truth: { label: "Gerçek (SNPP)", cls: styles.cTruth },
};

const PCell = ({ p }: { p: number }) => (
  <td>{fmtP(p)} <Badge tone={p < 0.05 ? "bad" : "ok"}>{p < 0.05 ? "kırılma" : "yok"}</Badge></td>
);

export function BreakTab({ regions }: { regions: Region[] }) {
  const [region, setRegion] = useState("all");
  const [freq, setFreq] = useState<"monthly" | "annual">("monthly");
  const [series, setSeries] = useState<SeriesPayload | null>(null);
  const [brk, setBrk] = useState<BreakPayload | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let live = true;
    setError("");
    Promise.all([api.series(region, freq), api.structuralBreak(region)])
      .then(([s, b]) => { if (live) { setSeries(s); setBrk(b); } })
      .catch((e: Error) => live && setError(e.message));
    return () => { live = false; };
  }, [region, freq]);

  const draw = useCallback((c: HTMLCanvasElement) => breakChart(c, series!, freq, brk), [series, freq, brk]);

  return (
    <section>
      <div className={styles.panelHead}>
        <h2>2012 sensör geçişinde yapısal kırılma</h2>
        <div className={styles.inlineCtl}>
          <label>Kapsam
            <select value={region} onChange={(e) => setRegion(e.target.value)}>
              <option value="all">Tüm bölgeler</option>
              {regions.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
            </select>
          </label>
          <Seg value={freq} onChange={setFreq} options={[{ v: "monthly", label: "Aylık" }, { v: "annual", label: "Yıllık" }]} />
        </div>
      </div>
      {error && <div className={styles.banner}>{error}</div>}
      {series && brk ? (
        <>
          <ChartBox draw={draw} height={380} />
          <div className={styles.grid2}>
            <Card title="Test sonuçları">
              <table className={styles.tbl}>
                <thead>
                  <tr><th className={styles.l}>Seri</th><th>2012 adımı</th><th>Pettitt (yıllık)</th><th>Chow @2012 (yıllık)</th><th>Chow @2012 (aylık)</th><th>Pettitt ±6 y</th></tr>
                </thead>
                <tbody>
                  {(Object.entries(brk.reports) as [keyof BreakPayload["reports"], BreakReport][]).map(([k, r]) => (
                    <tr key={k}>
                      <td className={`${styles.l} ${NAMES[k].cls}`}>{NAMES[k].label}</td>
                      <td className={r.step.step_pct > 25 ? styles.cRaw : undefined}>{signed(r.step.step_pct, 1)}%</td>
                      <PCell p={r.pettitt_annual.p_value} /><PCell p={r.chow_annual.p_value} />
                      <PCell p={r.chow_monthly.p_value} /><PCell p={r.pettitt_local.p_value} />
                    </tr>
                  ))}
                </tbody>
              </table>
              <Hint>Değerler p-değeridir: "kırılma" p &lt; 0,05 demektir. Chow tam 2012-01-20'de değerlendirilir; Pettitt kırılma noktasını serbestçe arar.</Hint>
            </Card>
            <Card title="Yorum">
              <p className={styles.prose}>
                <Badge tone={brk.verdict.raw_break_at_transition ? "bad" : "ok"}>
                  ham: {brk.verdict.raw_break_at_transition ? "sensör geçişinde yapay kırılma" : "kırılma yok"}
                </Badge>{" "}
                <Badge tone={brk.verdict.harmonized_step_eliminated ? "ok" : "bad"}>
                  harmonize: {brk.verdict.harmonized_step_eliminated ? "adım ortadan kalktı" : "adım sürüyor"}
                </Badge>
              </p>
              <ul className={styles.prose}>
                {brk.verdict.notes.map((n) => <li key={n}>{n}</li>)}
              </ul>
              <Hint>Son ham adım: {fmt(brk.reports.raw_filtered.step.step_pct, 0)}% (kaynaklar çıkarılmış).</Hint>
            </Card>
          </div>
          <Card title="Bu ne gösteriyor">
            <p className={styles.prose}>
              VIIRS (375 m) 2012-01-20'de hizmete girdi. Tespitlerini MODIS (1 km) ile yan yana saymak, yangınların kendisiyle ilgisi olmayan yapay bir "yanan alan" sıçraması üretir. Ham seri geçişte kırılır; harmonize seri (2012 öncesi VIIRS-eşdeğerine ölçeklenmiş MODIS, sonrası VIIRS sayımları) kırılmaz. Yeşil çizgi gizli karşı-olgusal seridir: var olmayan SNPP kaydı 2012 öncesini nasıl anlatırdı.
            </p>
          </Card>
        </>
      ) : !error && <p className={styles.hint}>Yükleniyor...</p>}
    </section>
  );
}
