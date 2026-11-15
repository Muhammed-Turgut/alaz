import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api } from "./api";
import { calendarHoverText, drawCalendar, envelopeChart, type HeatmapGeom } from "./charts";
import { usePrefs } from "@/lib/preferences";
import type { AreaCalendarResult, CalendarPayload, LatLng, Source } from "./types";
import { AiInsight } from "./AiInsight";
import { useT } from "@/lib/i18n";
import { Card, ChartBox, EmptyRoi, Hint, Seg } from "./ui";
import styles from "./aether.module.css";

const HOVER_IDLE = "Bir hücrenin üstüne gel: yıl, yılın günü, tespit sayısı.";
const MONTHS_TR = ["Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara"];

interface Props {
  visible: boolean;
  source: Source;
  roi: LatLng[] | null;
  onDraw?: () => void;
}

function yearsBetween(a: number, b: number): number[] {
  const lo = Math.min(a, b);
  const hi = Math.max(a, b);
  const out: number[] = [];
  for (let y = lo; y <= hi; y++) out.push(y);
  return out;
}

export function CalendarTab({ visible, source, roi, onDraw }: Props) {
  const t = useT();
  const [series, setSeries] = useState<"harmonized" | "raw">("harmonized");
  const [cal, setCal] = useState<CalendarPayload | null>(null);
  const [area, setArea] = useState<AreaCalendarResult | null>(null);
  const [error, setError] = useState("");
  const [hover, setHover] = useState(HOVER_IDLE);
  const [width, setWidth] = useState(0);
  const [busy, setBusy] = useState(false);
  const [startYear, setStartYear] = useState<number | null>(null);
  const [endYear, setEndYear] = useState<number | null>(null);
  const [targetYear, setTargetYear] = useState<number | null>(null);
  const [dataYears, setDataYears] = useState<[number, number] | null>(null);
  const wrap = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const geom = useRef<HeatmapGeom | null>(null);
  const { theme, calRamp } = usePrefs();

  // ROI değişince yıl seçimini sıfırla (yeni alanın tarih aralığı gelsin)
  useEffect(() => {
    setStartYear(null);
    setEndYear(null);
    setTargetYear(null);
    setDataYears(null);
  }, [roi]);

  useEffect(() => {
    let live = true;
    setError("");
    setCal(null);
    setArea(null);

    if (source === "live") {
      setError(
        "Canlı FIRMS yalnızca son ~24 saat (küresel) — yanma takvimi yok. "
        + "Gerçek arşiv + ROI kullan. TR yerel disk hızlı; dünya geneli = on-demand FIRMS API.",
      );
      return () => { live = false; };
    }

    if (!roi || roi.length < 3) {
      setError("");
      return () => { live = false; };
    }
    setBusy(true);
    api.areaCalendar({
      polygon: roi,
      series,
      start_year: startYear ?? undefined,
      end_year: endYear ?? undefined,
      target_year: targetYear ?? undefined,
    })
      .then((r) => {
        if (!live) return;
        setArea(r);
        if (r.empty) {
          setError(r.message || "Seçilen alanda tespit yok.");
          return;
        }
        if (r.calendar) setCal(r.calendar);
        const dr = r.meta?.date_range;
        if (dr?.[0] && dr?.[1]) {
          const sy = Number(dr[0].slice(0, 4));
          const ey = Number(dr[1].slice(0, 4));
          if (Number.isFinite(sy) && Number.isFinite(ey)) {
            setDataYears([sy, ey]);
            if (startYear == null) setStartYear(r.meta?.view_years?.[0] ?? sy);
            if (endYear == null) setEndYear(r.meta?.view_years?.[1] ?? ey);
            if (targetYear == null) setTargetYear(r.meta?.target_year ?? ey);
          }
        }
      })
      .catch((e: Error) => live && setError(e.message))
      .finally(() => live && setBusy(false));
    return () => { live = false; };
  }, [series, source, roi, startYear, endYear, targetYear]);

  // wrap yalnızca cal geldikten sonra DOM’da; görünür olunca yeniden ölç.
  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const measure = () => {
      const w = el.clientWidth;
      if (w >= 100) setWidth(w);
    };
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    measure();
    if (visible) requestAnimationFrame(measure);
    return () => ro.disconnect();
  }, [cal, visible]);

  useEffect(() => {
    if (!cal || !canvas.current || width < 100 || !visible) return;
    geom.current = drawCalendar(canvas.current, cal, width);
  }, [cal, width, visible, theme, calRamp]);

  const draw = useCallback((c: HTMLCanvasElement) => envelopeChart(c, cal!), [cal]);

  const onMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!cal || !geom.current) return;
    const r = e.currentTarget.getBoundingClientRect();
    setHover(calendarHoverText(cal, geom.current, e.clientX - r.left, e.clientY - r.top));
  };

  const yearOpts = useMemo(() => {
    if (!dataYears) return [];
    return yearsBetween(dataYears[0], dataYears[1]);
  }, [dataYears]);

  const vs = area?.current_month_vs_median;
  const my = area?.month_year;
  const meta = area?.meta;
  const needRoi = source === "archive" && (!roi || roi.length < 3);

  return (
    <section>
      <div className={styles.panelHead}>
        <h2>Gerçek FIRMS yanma takvimi</h2>
        <div className={styles.inlineCtl}>
          <Seg
            value={series}
            onChange={setSeries}
            options={[
              { v: "harmonized", label: "VIIRS-eşdeğer" },
              { v: "raw", label: "Ham (sensör ayrı)" },
            ]}
          />
        </div>
      </div>
      <div className={styles.disclaimer} role="note">{t.disclaimerCalendar}</div>

      {needRoi && (
        <EmptyRoi
          message="Takvim için ROI çiz veya enlem/boylam gir (dünya geneli, ≤ ~120.000 km²)."
          onDraw={onDraw}
        />
      )}

      {source === "archive" && yearOpts.length > 0 && (
        <div className={styles.yearRow}>
          <label>
            Başlangıç yılı
            <select
              value={startYear ?? yearOpts[0]}
              onChange={(e) => {
                const y = Number(e.target.value);
                setStartYear(y);
                if (endYear != null && y > endYear) setEndYear(y);
                if (targetYear != null && y > targetYear) setTargetYear(y);
              }}
            >
              {yearOpts.map((y) => <option key={y} value={y}>{y}</option>)}
            </select>
          </label>
          <label>
            Bitiş yılı
            <select
              value={endYear ?? yearOpts[yearOpts.length - 1]}
              onChange={(e) => {
                const y = Number(e.target.value);
                setEndYear(y);
                if (startYear != null && y < startYear) setStartYear(y);
                if (targetYear != null && y < targetYear) setTargetYear(y);
              }}
            >
              {yearOpts.map((y) => <option key={y} value={y}>{y}</option>)}
            </select>
          </label>
          <label>
            Hedef yıl (anomali / medyan)
            <select
              value={targetYear ?? endYear ?? yearOpts[yearOpts.length - 1]}
              onChange={(e) => setTargetYear(Number(e.target.value))}
            >
              {yearsBetween(startYear ?? yearOpts[0], endYear ?? yearOpts[yearOpts.length - 1]).map((y) => (
                <option key={y} value={y}>{y}</option>
              ))}
            </select>
          </label>
        </div>
      )}

      {source === "archive" && meta && (
        <Hint>
          {meta.partial ? `Arşiv iniyor: ${meta.done_chunks}/${meta.total_chunks} dilim. ` : "Arşiv hazır. "}
          Alan ~{meta.area_km2?.toLocaleString("tr-TR")} km² · ham tespit {meta.raw_count?.toLocaleString("tr-TR")}
          {meta.date_range?.[0] ? ` · veri ${meta.date_range[0]} → ${meta.date_range[1]}` : ""}
          {meta.view_years ? ` · görünüm ${meta.view_years[0]}–${meta.view_years[1]}` : ""}
          {meta.counts ? ` · MODIS ${meta.counts.raw_modis.toLocaleString("tr-TR")} / VIIRS ${meta.counts.raw_viirs.toLocaleString("tr-TR")}` : ""}
        </Hint>
      )}
      {source === "archive" && meta && (
        <Hint>
          Birincil seri <b>VIIRS-eşdeğer (harmonize)</b> — sensör farkını azaltmak için; model sentetik
          benchmark’ta eğitildi, VIIRS mutlak yer-gerçeği değil. Ham, MODIS/VIIRS’i ayrı tutar (karşılaştırma).
        </Hint>
      )}

      {busy && <div className={styles.banner}>Alan takvimi hesaplanıyor...</div>}
      {error && <div className={styles.banner}>{error}</div>}

      {vs?.available && (
        <Card title="Bu ay vs tarihsel medyan">
          <div className={styles.qStrip}>
            <div><b>{vs.month_total?.toLocaleString("tr-TR") ?? "-"}</b><span>{vs.year}-{String(vs.month).padStart(2, "0")} toplam (tespit)</span></div>
            <div><b>{vs.historical_median?.toLocaleString("tr-TR") ?? "-"}</b><span>tarihsel medyan ({vs.n_years} yıl)</span></div>
            <div><b>{vs.ratio != null ? `${vs.ratio.toFixed(2)}×` : "-"}</b><span>oran</span></div>
            <div><b>{vs.percentile != null ? `%${vs.percentile.toFixed(0)}` : "-"}</b><span>yüzdelik</span></div>
          </div>
          <Hint>
            Bu ayın sıcak nokta tespit toplamı, aynı ayın tarihsel medyanına göre nerede (yangın sayısı ≠ tespit).
            {vs.historical_p10 != null ? ` Tarihsel P10–P90: ${vs.historical_p10} – ${vs.historical_p90}.` : ""}
          </Hint>
          <AiInsight focus="calendar" section="month_vs_median" label="Bu ay vs medyan" roi={roi} compact />
        </Card>
      )}

      {cal && (
        <Card title="Yıl × gün ısı haritası (tespit sayısı)">
          <div ref={wrap}>
            <canvas ref={canvas} onMouseMove={onMove} onMouseLeave={() => setHover(HOVER_IDLE)} />
          </div>
          <Hint>{hover}</Hint>
          <AiInsight focus="calendar" section="heatmap" label="Isı haritası" roi={roi} compact />
        </Card>
      )}

      {my && my.years.length > 0 && (
        <Card title="Ay × yıl yoğunluk (aylık tespit sayısı)">
          <div className={styles.tblScroll}>
            <table className={`${styles.tbl} ${styles.tblSticky}`}>
              <thead>
                <tr>
                  <th className={styles.l}>Yıl</th>
                  {MONTHS_TR.map((m) => <th key={m}>{m}</th>)}
                </tr>
              </thead>
              <tbody>
                {my.years.map((y, yi) => (
                  <tr key={y}>
                    <td className={styles.l}>{y}</td>
                    {my.matrix[yi].map((v, mi) => (
                      <td key={`${y}-${mi}`}>{v == null ? "·" : Math.round(v).toLocaleString("tr-TR")}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <AiInsight focus="calendar" section="month_year" label="Ay × yıl tablosu" roi={roi} compact />
        </Card>
      )}

      {cal && (
        <div className={styles.grid2}>
          <div>
            <ChartBox draw={draw} height={300} title={`İklimsel zarf (P10 / P50 / P90) ve ${cal.target_year}`} />
            <AiInsight focus="calendar" section="envelope" label="İklimsel zarf" roi={roi} compact />
          </div>
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
                  {cal.anomalies.n_days} anomalik gün (|z| ≥ 2): {cal.anomalies.n_positive} üstünde, {cal.anomalies.n_negative} altında.
                </Hint>
              </>
            ) : <Hint>{cal.target_year} içinde çok günlük anomali yok.</Hint>}
            <AiInsight focus="calendar" section="anomalies" label="Anomali dönemleri" roi={roi} compact />
          </Card>
        </div>
      )}

      {area?.local_validation && Object.keys(area.local_validation).length > 0 && (
        <Card title="Yerel doğrulama (MODIS → VIIRS referansı, örtüşen aylar)">
          <Hint>
            RMSE ham {Number(area.local_validation.rmse_raw).toFixed(1)}
            {" → "}harmonize {Number(area.local_validation.rmse_harmonized).toFixed(1)}
            {" · "}göreli sapma ham %{Number(area.local_validation.relative_bias_raw_pct).toFixed(1)}
            {" → "}harmonize %{Number(area.local_validation.relative_bias_harmonized_pct).toFixed(1)}
            {area.local_validation.n ? ` · n=${area.local_validation.n} ay` : ""}
            {" · "}VIIRS karşılaştırma referansıdır (ground truth değil).
          </Hint>
          {Number(area.local_validation.rmse_reduction_pct ?? 0) < 0 && (
            <div className={styles.banner}>
              Bu ROI’de iyileşme yok — takvim için <b>Ham</b> seriyi tercih et
              (üstteki seçiciyi Ham yap). VIIRS-eşdeğer varsayılanı burada uygun değil.
            </div>
          )}
          <AiInsight focus="calendar" section="validation" label="Yerel doğrulama" roi={roi} compact />
        </Card>
      )}

      {source === "live" && (
        <AiInsight
          focus="calendar"
          section="overview"
          label="Takvim"
          roi={roi}
          disabledReason="AI bakışı için Gerçek arşiv (TR) + poligon kullan."
        />
      )}
    </section>
  );
}
