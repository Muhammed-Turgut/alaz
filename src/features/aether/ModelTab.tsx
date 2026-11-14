import { useCallback, useEffect, useState } from "react";

import { api } from "./api";
import { importanceChart } from "./charts";
import { fmt, signed } from "./format";
import type { ModelPayload, StaticFilterStats, StaticSources, ValidationPayload } from "./types";
import { Card, ChartBox, Hint, Kpi } from "./ui";
import styles from "./aether.module.css";

export function ModelTab() {
  const [model, setModel] = useState<ModelPayload | null>(null);
  const [val, setVal] = useState<ValidationPayload | null>(null);
  const [ss, setSs] = useState<StaticSources | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let live = true;
    Promise.all([api.model(), api.validation(), api.staticSources()])
      .then(([m, v, s]) => { if (live) { setModel(m); setVal(v); setSs(s); } })
      .catch((e: Error) => live && setError(e.message));
    return () => { live = false; };
  }, []);

  const imp = model?.importance.frp;
  const draw = useCallback((c: HTMLCanvasElement) => importanceChart(c, imp!), [imp]);

  if (error) return <div className={styles.banner}>{error}</div>;
  if (!model || !val || !ss) return <p className={styles.hint}>Yükleniyor...</p>;

  const f = model.metrics.per_target.frp;
  const n = model.metrics.per_target.n;
  const mt = model.matchups;
  const tests: [keyof ValidationPayload["regions"][string], string][] = [
    ["holdout_2022_2026", "Hold-out 2022-26"],
    ["counterfactual_2001_2011", "Karşı-olgusal 2001-11"],
  ];
  const sf = Object.entries(val.static_filter).filter(([k]) => k !== "catalog_cells") as [string, StaticFilterStats][];

  return (
    <section>
      <div className={styles.panelHead}><h2>Model, doğrulama ve veri bütünlüğü</h2></div>
      <div className={styles.kpis}>
        <Kpi tone="harm" label="FRP hatası (naif doğrusala göre)" value={`-${fmt(f.mae_improvement_pct)}%`} sub={`MAE(log) ${f.mae_log_model.toFixed(2)} vs ${f.mae_log_naive_linear.toFixed(2)}`} />
        <Kpi tone="harm" label="Sıcak nokta sayısı hatası" value={`-${fmt(n.mae_improvement_pct)}%`} sub={`MAE(log) ${n.mae_log_model.toFixed(2)} vs ${n.mae_log_naive_linear.toFixed(2)}`} />
        <Kpi tone="ok" label="Conformal %90 kapsama" value={`${(f.coverage_90_conformal * 100).toFixed(1)}% / ${(n.coverage_90_conformal * 100).toFixed(1)}%`} sub="hold-out yıllarında FRP / sayı" />
        <Kpi label="Eğitim eşleşmesi" value={fmt(mt.n_matchups)} sub={`eğitim ${mt.train_years.join("-")} · kalibrasyon ${mt.calib_years.join("-")} · test ${mt.test_years.join("-")}`} />
        <Kpi tone="fire" label="Toplulaştırma varyans faktörü" value={`${model.kappa.toFixed(2)}x`} sub="deneysel GA şişirme (ilişkili hatalar)" />
        <Kpi label="VIIRS karşılığı olan MODIS" value={`${(mt.frac_modis_with_viirs_counterpart * 100).toFixed(0)}%`} sub="geometrik ayak izi eşleşmesi" />
      </div>
      <div className={styles.grid2}>
        <Card title="Hold-out ve karşı-olgusal doğrulama">
          <table className={styles.tbl}>
            <thead>
              <tr><th className={styles.l}>Kapsam</th><th className={styles.l}>Test</th><th>RMSE ham</th><th>RMSE harm.</th><th>Sapma ham</th><th>Sapma harm.</th><th>r</th><th>GA kap.</th></tr>
            </thead>
            <tbody>
              {Object.entries(val.regions).flatMap(([rid, r]) => tests.map(([k, label]) => {
                const x = r[k];
                return (
                  <tr key={rid + k}>
                    <td className={styles.l}>{rid}</td><td className={styles.l}>{label}</td>
                    <td className={styles.cRaw}>{fmt(x.rmse_raw)}</td><td className={styles.cHarm}>{fmt(x.rmse_harmonized)}</td>
                    <td className={styles.cRaw}>{signed(x.relative_bias_raw_pct)}%</td><td className={styles.cHarm}>{signed(x.relative_bias_harmonized_pct, 1)}%</td>
                    <td>{x.correlation_harmonized.toFixed(3)}</td><td>{(x.ci90_coverage * 100).toFixed(0)}%</td>
                  </tr>
                );
              }))}
            </tbody>
          </table>
          <Hint>
            Aylık MODIS-türevli seri, VIIRS-yerel referansa karşı. 2012 öncesi referans gizli karşı-olgusal SNPP kaydıdır; 2022-26 referansı gerçek VIIRS'tir. Ham sapma, MODIS-yalnız sayımın VIIRS'e göre oranıdır.
          </Hint>
        </Card>
        <ChartBox draw={draw} height={300} title="Permütasyon önemi (FRP hedefi)" />
      </div>
      <div className={styles.grid2}>
        <Card title="Kalıcı kaynak filtresi, gerçekle karşılaştırma">
          <table className={styles.tbl}>
            <thead><tr><th className={styles.l}>Bölge</th><th>Kesinlik</th><th>Duyarlılık</th><th>Gerçek satır</th><th>Yanlış +</th></tr></thead>
            <tbody>
              {sf.map(([k, x]) => (
                <tr key={k}>
                  <td className={styles.l}>{k}</td><td>{(x.precision * 100).toFixed(2)}%</td><td>{(x.recall * 100).toFixed(1)}%</td>
                  <td>{fmt(x.true_static_rows)}</td><td>{fmt(x.false_positive_rows)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <Hint>Kural: yılda &gt; 90 farklı gün, aylık CV ≤ 0,6, ≥ 9 aktif ay, ≥ 2 yılda işaretli; MODIS pikselleri için ayak izi-duyarlı genişletme.</Hint>
        </Card>
        <Card title="Tespit edilen kalıcı kaynaklar">
          <div className={styles.scroll}>
            <table className={styles.tbl}>
              <thead><tr><th className={styles.l}>Bölge</th><th className={styles.l}>Kaynak</th><th>Gün/yıl</th><th>Yıl</th><th className={styles.l}>Tür</th></tr></thead>
              <tbody>
                {ss.detected.map((d) => (
                  <tr key={d.cell}>
                    <td className={styles.l}>{d.region}</td><td className={styles.l}>{d.scenario_source ?? d.cell}</td>
                    <td>{fmt(d.median_days_per_year)}</td><td>{d.years_flagged}</td><td className={styles.l}>{d.kind}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </div>
      <Card title="Yöntem notları">
        <div className={styles.prose}>
          <p><b>Geometri.</b> Piksel ayak izleri küresel-Dünya eğik-mesafe geometrisinden gelir (MODIS 705 km, VIIRS 824 km). Her VIIRS pikselinin tespit kütlesi, örtüşen MODIS ayak izlerine gerçek dikdörtgen kesişimiyle paylaştırılır; sonra H3 res-8 komşuluğu (~0,74 km²) ile birleştirilip res-7'ye (~5,16 km²) toplanır.</p>
          <p><b>Diurnal.</b> Yerel güneş saati = UTC + boylam/15 + zaman denklemi. FRP, Gauss diurnal eğriyle (tepe 14:30, çukur ~04:00) SNPP 13:30 / 01:30 referansına ölçeklenir; Terra ve Aqua yörünge kayması dahildir.</p>
          <p><b>Model.</b> Biyom ve hedef başına bir HistGradientBoosting kantil modeli (P10/P50/P90); ayrık bir dönemde conformal kalibrasyon. Katkılar grup-ablasyon etkileridir.</p>
          <p><b>Dürüstlük.</b> Benchmark, bilinen gerçeğe sahip simüle bir veridir; raporlanan hatalar bu simülatör için gerçektir ama NASA verisi hakkında iddia değildir. Toplam aylık Pettitt testi 2018 gibi sonraki bir kırılmayı yine işaretleyebilir; gerçek seri de aynısını gösterir, bu sensör artefaktı değil, senaryodaki gerçek etkinliktir.</p>
        </div>
      </Card>
    </section>
  );
}
