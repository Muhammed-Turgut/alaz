import { useCallback, useState } from "react";

import { Button } from "@/components/ui/Button";
import { miniSeriesChart } from "./charts";
import { fmt, signed } from "./format";
import type { DetectionExplain, DriverSummary, HexDetail, RoiResult, SatStat } from "./types";
import { BarRows, Badge, MiniChart } from "./ui";
import styles from "./aether.module.css";

export type InspectorState =
  | { kind: "empty" }
  | { kind: "loading"; label: string }
  | { kind: "error"; message: string }
  | { kind: "hex"; data: HexDetail }
  | { kind: "roi"; data: RoiResult };

function Drivers({ d }: { d: DriverSummary | undefined }) {
  if (!d || !d.frp.length) return null;
  return (
    <>
      <div className={styles.sect}>MODIS FRP neden yeniden ölçeklendi (ortalama etki, {d.n_rows} tespit)</div>
      <BarRows items={d.frp.map((x) => ({ label: x.driver, v: x.mean_effect_pct, text: `${signed(x.mean_effect_pct)}%` }))} />
    </>
  );
}

function SatTable({ by }: { by: Record<string, SatStat> | undefined }) {
  const rows = Object.entries(by ?? {});
  const detailed = rows.some(([, v]) => typeof v !== "number");
  return (
    <table className={styles.tbl}>
      <thead>
        <tr><th className={styles.l}>Uydu</th><th>Tespit</th>{detailed && <><th>Ort. FRP</th><th>Ayak izi km²</th></>}</tr>
      </thead>
      <tbody>
        {rows.map(([s, v]) => (
          <tr key={s}>
            <td className={styles.l}>{s}</td>
            <td>{fmt(typeof v === "number" ? v : v.n)}</td>
            {detailed && typeof v !== "number" && <><td>{fmt(v.frp_mean, 1)}</td><td>{fmt(v.area_mean, 2)}</td></>}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function ExplainRow({ r }: { r: DetectionExplain }) {
  return (
    <p className={styles.hint}>
      Piksel {r.footprint_km2.toFixed(1)} km² (nadirin {r.bowtie_x.toFixed(1)} katı), LST {r.lst.toFixed(1)} sa, diurnal faktör {r.diurnal_factor.toFixed(2)}.<br />
      FRP {r.frp_obs.toFixed(1)} MW → harmonize P10/P50/P90 {r.frp_harm_p10.toFixed(1)} / <b>{r.frp_harm_p50.toFixed(1)}</b> / {r.frp_harm_p90.toFixed(1)} MW
      (%90 GA {r.frp_lo90.toFixed(1)}-{r.frp_hi90.toFixed(1)}).<br />
      Eşdeğer VIIRS sıcak nokta {r.n_p50.toFixed(1)} ({r.n_lo90.toFixed(1)}-{r.n_hi90.toFixed(1)}), ölçek faktörü {r.scale_factor.toFixed(2)}.<br />
      Etkenler: {r.drivers.map((d) => `${d.driver} ${signed(d.effect_pct)}%`).join(" · ")}
    </p>
  );
}

function HexView({ d }: { d: HexDetail }) {
  const [open, setOpen] = useState<number | null>(null);
  const t = d.totals;
  const dm = d.diurnal;
  return (
    <>
      <h3>Altıgen <Badge>{d.biome}</Badge></h3>
      <div className={styles.sub}>{d.h3} · {d.lat.toFixed(3)}, {d.lon.toFixed(3)} · {fmt(d.area_km2, 2)} km²</div>
      <table className={styles.tbl}>
        <tbody>
          <tr><td className={styles.l}>Ham tespit</td><td className={styles.cRaw}>{fmt(t.raw_total)}</td></tr>
          <tr><td className={styles.l}>&nbsp;&nbsp;MODIS / VIIRS</td><td>{fmt(t.raw_modis)} / {fmt(t.raw_viirs)}</td></tr>
          <tr><td className={styles.l}>Kalıcı kaynak / düşük güven çıkarılan</td><td>{fmt(t.static_n)} / {fmt(t.lowconf_n)}</td></tr>
          <tr><td className={styles.l}>Harmonize sıcak nokta</td><td className={styles.cHarm}>{fmt(t.harmonized, 1)}</td></tr>
          <tr><td className={styles.l}>&nbsp;&nbsp;%90 GA</td><td>{t.hi - t.lo > 1e-6 ? `${fmt(t.lo, 1)} - ${fmt(t.hi, 1)}` : "VIIRS-yerel (GA yok)"}</td></tr>
          <tr><td className={styles.l}>Ort. FRP ham / harmonize (MW)</td><td>{fmt(t.frp_raw_mean, 1)} / <span className={styles.cHarm}>{fmt(t.frp_harm_mean, 1)}</span></td></tr>
        </tbody>
      </table>
      <div className={styles.sect}>Uydulara göre</div>
      <SatTable by={d.by_satellite} />
      {dm.mean_lst !== undefined && (
        <>
          <div className={styles.sect}>Geçiş zamanı</div>
          <p className={styles.hint}>
            Ortalama yerel güneş saati {fmt(dm.mean_lst, 1)}; diurnal eğri {dm.peak_hour} sa'de tepe yapar. FRP, gündüz {dm.reference_day} sa / gece {dm.reference_night} sa referansına ölçeklenir.
          </p>
        </>
      )}
      <Drivers d={d.drivers} />
      {d.top_modis_detections.length > 0 && (
        <>
          <div className={styles.sect}>En parlak MODIS tespitleri (açıklamak için tıkla)</div>
          <table className={styles.tbl}>
            <thead>
              <tr><th className={styles.l}>UTC</th><th className={styles.l}>Uydu</th><th>FRP</th><th>km²</th><th>LST</th><th>FRPh</th><th>Eş. n</th></tr>
            </thead>
            <tbody>
              {d.top_modis_detections.map((r, i) => (
                <FragmentRow key={i} r={r} open={open === i} onToggle={() => setOpen(open === i ? null : i)} />
              ))}
            </tbody>
          </table>
        </>
      )}
    </>
  );
}

function FragmentRow({ r, open, onToggle }: { r: DetectionExplain; open: boolean; onToggle: () => void }) {
  return (
    <>
      <tr className={styles.click} onClick={onToggle}>
        <td className={styles.l}>{r.time.slice(5, 16)}</td>
        <td className={styles.l}>{r.satellite}</td>
        <td className={styles.cRaw}>{fmt(r.frp_obs, 1)}</td>
        <td>{fmt(r.footprint_km2, 1)}</td>
        <td>{fmt(r.lst, 1)}</td>
        <td className={styles.cHarm}>{fmt(r.frp_harm_p50, 1)}</td>
        <td className={styles.cHarm}>{fmt(r.n_p50, 1)}</td>
      </tr>
      {open && <tr><td colSpan={7}><ExplainRow r={r} /></td></tr>}
    </>
  );
}

function RoiView({ d, onBriefing }: { d: RoiResult; onBriefing: () => void }) {
  const ts = d.timeseries;
  const draw = useCallback((c: HTMLCanvasElement) => miniSeriesChart(c, ts ?? { labels: [], raw: [], harmonized: [] }), [ts]);
  if (d.empty || !d.totals) {
    return (
      <>
        <h3>İlgi alanı</h3>
        <div className={styles.sub}>{fmt(d.roi_area_km2)} km² · {d.n_cells_in_roi} hex</div>
        <p className={styles.hint}>Bu pencerede poligon içinde tespit yok.</p>
      </>
    );
  }
  const t = d.totals;
  return (
    <>
      <h3>İlgi alanı</h3>
      <div className={styles.sub}>{fmt(d.roi_area_km2)} km² · {d.n_cells_in_roi} H3 res-7 hex · {d.static_cells} kalıcı kaynaklı</div>
      <table className={styles.tbl}>
        <tbody>
          <tr><td className={styles.l}>Ham tespit</td><td className={styles.cRaw}>{fmt(t.raw_total)}</td></tr>
          <tr><td className={styles.l}>&nbsp;&nbsp;MODIS / VIIRS</td><td>{fmt(t.raw_modis)} / {fmt(t.raw_viirs)}</td></tr>
          <tr><td className={styles.l}>Kalıcı kaynak / düşük güven çıkarılan</td><td>{fmt(t.static_removed)} / {fmt(t.lowconf_removed)}</td></tr>
          <tr><td className={styles.l}>Harmonize sıcak nokta</td><td className={styles.cHarm}>{fmt(t.harmonized, 1)}</td></tr>
          <tr><td className={styles.l}>&nbsp;&nbsp;%90 GA</td><td>{t.harmonized_hi90 - t.harmonized_lo90 > 1e-6 ? `${fmt(t.harmonized_lo90, 1)} - ${fmt(t.harmonized_hi90, 1)}` : "VIIRS-yerel (GA yok)"}</td></tr>
          <tr><td className={styles.l}>Ort. FRP ham / harmonize (MW)</td><td>{fmt(t.frp_raw_mean, 1)} / <span className={styles.cHarm}>{fmt(t.frp_harm_mean, 1)}</span></td></tr>
        </tbody>
      </table>
      <div className={styles.sect}>Biyom (harmonize / ham)</div>
      <BarRows items={Object.entries(d.by_biome ?? {}).map(([b, v]) => ({ label: b, v: v.harmonized, text: `${fmt(v.harmonized, 1)} / ${fmt(v.raw)}` }))} />
      <div className={styles.sect}>Uydulara göre</div>
      <SatTable by={d.by_satellite} />
      <div className={styles.sect}>Zaman serisi</div>
      <MiniChart draw={draw} />
      <Drivers d={d.drivers} />
      <Button variant="secondary" className={styles.full} onClick={onBriefing}>Bu ROI için brifing</Button>
    </>
  );
}

export function Inspector({ state, onBriefing }: { state: InspectorState; onBriefing: () => void }) {
  switch (state.kind) {
    case "loading":
      return <p className={styles.hint}>{state.label}</p>;
    case "error":
      return <p className={styles.errText}>{state.message}</p>;
    case "hex":
      return state.data.empty
        ? <p className={styles.hint}>{state.data.h3} için bu pencerede tespit yok.</p>
        : <HexView key={state.data.h3} d={state.data} />;
    case "roi":
      return <RoiView d={state.data} onBriefing={onBriefing} />;
    default:
      return (
        <div>
          <h3>Denetçi</h3>
          <p className={styles.hint}>
            Bir altıgene (H3 res-7, ~5,16 km²) tıkla: ham-harmonize dökümü açılır. Ya da özel bir ilgi alanı için poligon çiz.
          </p>
          <p className={styles.hint}>Kesikli turkuaz çerçeveler, harmonize sayımdan çıkarılan kalıcı termal kaynakları gösterir.</p>
        </div>
      );
  }
}
