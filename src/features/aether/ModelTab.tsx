import { useEffect, useState } from "react";

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

/** Yalnız canlı / gerçek arşiv. Simüle benchmark tabloları yok. */
export function ModelTab({ source, roi, onDraw }: Props) {
  if (source === "live") {
    return (
      <section>
        <div className={styles.panelHead}><h2>Model ve doğrulama</h2></div>
        <div className={styles.banner}>
          Canlı NASA FIRMS yalnızca son 24 saati gösterir; uzun geçmiş doğrulama burada yok.
          <b> Gerçek arşiv (TR)</b> seçip poligon çiz.
        </div>
        <AiInsight
          focus="model"
          section="overview"
          label="Model"
          roi={roi}
          disabledReason="AI bakışı için Gerçek arşiv (TR) + poligon kullan."
        />
      </section>
    );
  }

  return <ArchiveModelPanel roi={roi} onDraw={onDraw} />;
}

function ArchiveModelPanel({ roi, onDraw }: { roi: LatLng[] | null; onDraw?: () => void }) {
  const [areaVal, setAreaVal] = useState<Record<string, number> | null>(null);
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
          setAreaErr(b.message || "Seçilen alanda tespit yok.");
          return;
        }
        setAreaVal(b.local_validation ?? {});
        setAreaMeta(b.meta ?? null);
      })
      .catch((e: Error) => live && setAreaErr(e.message))
      .finally(() => live && setLoading(false));
    return () => { live = false; };
  }, [roi]);

  const needRoi = !roi || roi.length < 3;

  return (
    <section>
      <div className={styles.panelHead}><h2>Seçilen alan — gerçek FIRMS</h2></div>
      <div className={styles.banner}>
        Çizdiğin poligonun gerçek NASA FIRMS özeti ve bu alandaki MODIS→VIIRS uyumu. Başka bölge tabloları yok.
      </div>
      {needRoi && (
        <EmptyRoi
          message="Doğrulama için haritada bir poligon çiz."
          onDraw={onDraw}
        />
      )}
      {areaErr && <div className={styles.banner}>{areaErr}</div>}
      {areaMeta && (
        <Card title="Alan özeti">
          <div className={styles.qStrip}>
            <div><b>{areaMeta.raw_count?.toLocaleString("tr-TR") ?? "-"}</b><span>ham tespit</span></div>
            <div><b>{areaMeta.area_km2?.toLocaleString("tr-TR") ?? "-"}</b><span>km²</span></div>
            <div><b>{areaMeta.counts?.raw_modis?.toLocaleString("tr-TR") ?? "-"}</b><span>MODIS</span></div>
            <div><b>{areaMeta.counts?.raw_viirs?.toLocaleString("tr-TR") ?? "-"}</b><span>VIIRS</span></div>
          </div>
          <Hint>
            {areaMeta.date_range?.[0]} → {areaMeta.date_range?.[1]}
            {areaMeta.partial ? ` · arşiv kısmi ${areaMeta.done_chunks}/${areaMeta.total_chunks}` : " · arşiv tamam"}
          </Hint>
          <AiInsight focus="model" section="summary" label="Alan özeti" roi={roi} compact />
        </Card>
      )}
      {areaVal && Object.keys(areaVal).length > 0 && (
        <Card title="Bu alanda MODIS → VIIRS (aylık, gerçek veri)">
          <table className={styles.tbl}>
            <thead>
              <tr>
                <th className={styles.l}>Metrik</th>
                <th className={styles.cRaw}>Ham MODIS</th>
                <th className={styles.cHarm}>Harmonize MODIS</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td className={styles.l}>RMSE (vs gerçek VIIRS)</td>
                <td className={styles.cRaw}>{fmt(areaVal.rmse_raw)}</td>
                <td className={styles.cHarm}>{fmt(areaVal.rmse_harmonized)}</td>
              </tr>
              <tr>
                <td className={styles.l}>Göreli sapma %</td>
                <td className={styles.cRaw}>{signed(areaVal.relative_bias_raw_pct)}%</td>
                <td className={styles.cHarm}>{signed(areaVal.relative_bias_harmonized_pct, 1)}%</td>
              </tr>
              <tr>
                <td className={styles.l}>Korelasyon</td>
                <td>{Number(areaVal.correlation_raw).toFixed(3)}</td>
                <td>{Number(areaVal.correlation_harmonized).toFixed(3)}</td>
              </tr>
              <tr>
                <td className={styles.l}>RMSE değişimi</td>
                <td colSpan={2}>{signed(areaVal.rmse_reduction_pct, 1)}% · n={fmt(areaVal.n)} ay</td>
              </tr>
            </tbody>
          </table>
          {(areaVal.rmse_reduction_pct ?? 0) < 0 && (
            <div className={styles.banner}>
              Bu alanda mevcut harmonize model VIIRS’e yaklaştırmıyor. Takvim/brifing için <b>Ham</b> seriyi kullan.
            </div>
          )}
          <AiInsight focus="model" section="validation" label="MODIS → VIIRS doğrulama" roi={roi} compact />
        </Card>
      )}
      {loading && <p className={styles.hint}>Alan doğrulaması yükleniyor...</p>}
    </section>
  );
}
