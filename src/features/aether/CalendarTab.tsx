import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "./api";
import { calendarHoverText, drawCalendar, envelopeChart, type HeatmapGeom } from "./charts";
import { usePrefs } from "@/lib/preferences";
import type { CalendarPayload, Region } from "./types";
import { Card, ChartBox, Hint, Seg } from "./ui";
import styles from "./aether.module.css";

const HOVER_IDLE = "Bir hücrenin üstüne gel: yıl, yılın günü, etkinlik.";

export function CalendarTab({ regions, visible }: { regions: Region[]; visible: boolean }) {
  const [region, setRegion] = useState("california");
  const [series, setSeries] = useState<"harmonized" | "raw">("harmonized");
  const [cal, setCal] = useState<CalendarPayload | null>(null);
  const [error, setError] = useState("");
  const [hover, setHover] = useState(HOVER_IDLE);
  const [width, setWidth] = useState(0);
  const wrap = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const geom = useRef<HeatmapGeom | null>(null);
  const { theme } = usePrefs();

  useEffect(() => {
    let live = true;
    setError("");
    api.calendar(region, series)
      .then((c) => live && setCal(c))
      .catch((e: Error) => live && setError(e.message));
    return () => { live = false; };
  }, [region, series]);

  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    setWidth(el.clientWidth);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    if (!cal || !canvas.current || width < 100 || !visible) return;
    geom.current = drawCalendar(canvas.current, cal, width);
  }, [cal, width, visible, theme]);

  const draw = useCallback((c: HTMLCanvasElement) => envelopeChart(c, cal!), [cal]);

  const onMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!cal || !geom.current) return;
    const r = e.currentTarget.getBoundingClientRect();
    setHover(calendarHoverText(cal, geom.current, e.clientX - r.left, e.clientY - r.top));
  };

  return (
    <section>
      <div className={styles.panelHead}>
        <h2>25 yıllık yanma takvimi</h2>
        <div className={styles.inlineCtl}>
          <label>Bölge
            <select value={region} onChange={(e) => setRegion(e.target.value)}>
              <option value="all">Tüm bölgeler</option>
              {regions.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
            </select>
          </label>
          <Seg value={series} onChange={setSeries} options={[{ v: "harmonized", label: "Harmonize" }, { v: "raw", label: "Ham" }]} />
        </div>
      </div>
      {error && <div className={styles.banner}>{error}</div>}
      <Card>
        <div ref={wrap}>
          <canvas ref={canvas} onMouseMove={onMove} onMouseLeave={() => setHover(HOVER_IDLE)} />
        </div>
        <Hint>{hover}</Hint>
      </Card>
      {cal && (
        <div className={styles.grid2}>
          <ChartBox draw={draw} height={300} title="İklimsel zarf (P10 / P50 / P90) ve 2026" />
          <Card title={`${cal.target_year} anomali dönemleri`}>
            {cal.anomalies.episodes.length ? (
              <>
                <table className={styles.tbl}>
                  <thead><tr><th className={styles.l}>Başlangıç</th><th className={styles.l}>Bitiş</th><th>Gün</th><th>Tepe z</th></tr></thead>
                  <tbody>
                    {cal.anomalies.episodes.map((e) => (
                      <tr key={e.start}>
                        <td className={styles.l}>{e.start}</td><td className={styles.l}>{e.end}</td><td>{e.days}</td>
                        <td className={e.peak_z > 0 ? styles.cRaw : styles.cHarm}>{e.peak_z > 0 ? "+" : ""}{e.peak_z.toFixed(1)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <Hint>
                  {cal.anomalies.n_days} anomalik gün (log ölçekte |z| ≥ 2, ölçek tabanı 0,35): {cal.anomalies.n_positive} gün 2001-2025 gün-medyanının üstünde, {cal.anomalies.n_negative} gün altında.
                </Hint>
              </>
            ) : <Hint>{cal.target_year} içinde çok günlük anomali yok.</Hint>}
          </Card>
        </div>
      )}
    </section>
  );
}
