import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";

import { Button } from "@/components/ui/Button";
import { usePrefs } from "@/lib/preferences";
import { api } from "./api";
import { diurnalChart, driftChart, driverChart, drawGeometry } from "./charts";
import { fmt } from "./format";
import type { GeometryPayload, HarmonizedDetection, ModelPayload } from "./types";
import { Badge, Card, ChartBox, Hint, MiniChart } from "./ui";
import styles from "./aether.module.css";

function GeometryCard({ g, visible }: { g: GeometryPayload; visible: boolean }) {
  const wrap = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [width, setWidth] = useState(0);
  const { theme } = usePrefs();
  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    setWidth(el.clientWidth);
    return () => ro.disconnect();
  }, []);
  useEffect(() => {
    if (canvas.current && width > 100 && visible) drawGeometry(canvas.current, g, width);
  }, [g, width, visible, theme]);
  return (
    <Card title="Bow-tie piksel ayak izi">
      <div ref={wrap}><canvas ref={canvas} /></div>
      <Hint>
        MODIS: {g.modis.half_swath_km} km yarı-şerit; VIIRS 375 m: {g.viirs.half_swath_km} km. H3 res-7 = {g.h3.res7_km2} km², res-8 = {g.h3.res8_km2} km². {g.note}
      </Hint>
      <table className={styles.tbl}>
        <thead><tr><th>Açı</th><th>MODIS tarama x iz (km)</th><th>MODIS alan (km²)</th><th>VIIRS piksel (km)</th></tr></thead>
        <tbody>
          {g.scan_angle_deg.map((a, i) => (
            <tr key={a}>
              <td>{a}</td>
              <td className={styles.cRaw}>{g.modis.scan_km[i].toFixed(2)} x {g.modis.track_km[i].toFixed(2)}</td>
              <td className={styles.cRaw}>{g.modis.area_km2[i].toFixed(2)}</td>
              <td className={styles.cHarm}>{g.viirs.scan_km[i].toFixed(3)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

const SATS = ["Aqua", "Terra", "SNPP", "NOAA20"];

function HarmonizeCard() {
  const [res, setRes] = useState<HarmonizedDetection | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const num = (k: string) => (f.get(k) === "" || f.get(k) === null ? null : Number(f.get(k)));
    setBusy(true);
    setError("");
    try {
      const r = await api.harmonize({
        latitude: num("latitude"), longitude: num("longitude"), acq_date: f.get("acq_date"), acq_time: num("acq_time"),
        satellite: f.get("satellite"), frp: num("frp"), scan: num("scan"), track: num("track"), confidence: num("confidence"),
      });
      setRes(r.detections[0]);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const drivers = res?.drivers;
  const draw = useCallback((c: HTMLCanvasElement) => driverChart(c, drivers ?? []), [drivers]);

  return (
    <Card title="Bir tespiti harmonize et">
      <form onSubmit={submit} className={styles.formGrid}>
        <label>Enlem<input name="latitude" type="number" step="any" defaultValue="38.0" required /></label>
        <label>Boylam<input name="longitude" type="number" step="any" defaultValue="-121.8" required /></label>
        <label>Tarih (UTC)<input name="acq_date" type="date" defaultValue="2026-08-10" required /></label>
        <label>Saat (UTC, HHMM)<input name="acq_time" type="number" min={0} max={2359} defaultValue={2130} required /></label>
        <label>Uydu
          <select name="satellite" defaultValue="Aqua">{SATS.map((s) => <option key={s}>{s}</option>)}</select>
        </label>
        <label>FRP (MW)<input name="frp" type="number" step="any" min={0} defaultValue={42} required /></label>
        <label>Tarama boyutu (km)<input name="scan" type="number" step="any" min={0.1} defaultValue={3.5} /></label>
        <label>İz boyutu (km)<input name="track" type="number" step="any" min={0.1} defaultValue={1.8} /></label>
        <label>Güven (0-100)<input name="confidence" type="number" min={0} max={100} defaultValue={80} /></label>
        <div className={styles.formActions}>
          <Button type="submit" disabled={busy}>{busy ? "Hesaplanıyor..." : "Harmonize et"}</Button>
        </div>
      </form>
      {error && <p className={styles.errText}>{error}</p>}
      {res && (
        <div className={styles.hzResult}>
          <div className={styles.sub}>
            {res.satellite} · biyom {res.biome} · H3 r7 {res.h3_res7} · r8 {res.h3_res8}{" "}
            {res.is_static && <Badge tone="warn">kalıcı kaynak - dışlandı</Badge>}{" "}
            {res.low_confidence && <Badge tone="warn">düşük güven - dışlandı</Badge>}
          </div>
          <div className={styles.qStrip}>
            <div><b>{fmt(res.frp_p10, 1)}</b><span>FRP P10 (MW)</span></div>
            <div><b>{fmt(res.frp_p50, 1)}</b><span>FRP P50 harmonize</span></div>
            <div><b>{fmt(res.frp_p90, 1)}</b><span>FRP P90 (MW)</span></div>
          </div>
          <div className={styles.qStrip}>
            <div><b>{fmt(res.n_p50, 1)}</b><span>eşdeğer VIIRS sıcak nokta</span></div>
            <div><b>{fmt(res.n_lo90, 1)} - {fmt(res.n_hi90, 1)}</b><span>%90 GA</span></div>
            <div><b>{fmt(res.footprint_km2, 1)}</b><span>ayak izi km²</span></div>
          </div>
          <Hint>
            Yerel güneş saati {fmt(res.local_solar_time, 1)} sa · diurnal faktör {fmt(res.diurnal_factor, 2)} · diurnal normalize FRP {fmt(res.frp_diurnal_normalized, 1)} MW · gözlenen {fmt(res.frp_obs, 1)} MW.
          </Hint>
          <MiniChart draw={draw} height={190} />
        </div>
      )}
    </Card>
  );
}

export function ExplainTab({ visible }: { visible: boolean }) {
  const [geom, setGeom] = useState<GeometryPayload | null>(null);
  const [model, setModel] = useState<ModelPayload | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let live = true;
    Promise.all([api.geometry(), api.model()])
      .then(([g, m]) => { if (live) { setGeom(g); setModel(m); } })
      .catch((e: Error) => live && setError(e.message));
    return () => { live = false; };
  }, []);

  const dm = model?.diurnal;
  const drawD = useCallback((c: HTMLCanvasElement) => diurnalChart(c, dm!), [dm]);
  const drawF = useCallback((c: HTMLCanvasElement) => driftChart(c, dm!), [dm]);

  return (
    <section>
      <div className={styles.panelHead}><h2>Sıcak nokta açıklayıcı</h2></div>
      {error && <div className={styles.banner}>{error}</div>}
      <div className={styles.grid2}>
        <HarmonizeCard />
        {geom ? <GeometryCard g={geom} visible={visible} /> : <Hint>Yükleniyor...</Hint>}
      </div>
      {dm && (
        <div className={styles.grid2}>
          <ChartBox draw={drawD} height={300} title="Diurnal yangın döngüsü (yerel güneş saati)" />
          <ChartBox draw={drawF} height={300} title="Terra ve Aqua geçiş saati kayması" />
        </div>
      )}
    </section>
  );
}
