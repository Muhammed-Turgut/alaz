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
/** Fallback year span until ROI meta arrives (FIRMS MODIS era → now). */
const FALLBACK_YEAR_SPAN: [number, number] = [2001, new Date().getUTCFullYear()];

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
  const [series, setSeries] = useState<"harmonized" | "raw" | "hybrid">("hybrid");
  const [csvBusy, setCsvBusy] = useState(false);
  const [cal, setCal] = useState<CalendarPayload | null>(null);
  const [area, setArea] = useState<AreaCalendarResult | null>(null);
  const [error, setError] = useState("");
  const [hover, setHover] = useState(HOVER_IDLE);
  const [width, setWidth] = useState(0);
  const [busy, setBusy] = useState(false);
  const [startYear, setStartYear] = useState<number>(FALLBACK_YEAR_SPAN[0]);
  const [endYear, setEndYear] = useState<number>(FALLBACK_YEAR_SPAN[1]);
  const [targetYear, setTargetYear] = useState<number>(FALLBACK_YEAR_SPAN[1]);
  const [dataYears, setDataYears] = useState<[number, number] | null>(null);
  const seeded = useRef(false);
  const wrap = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const geom = useRef<HeatmapGeom | null>(null);
  const { theme, calRamp } = usePrefs();

  // ROI değişince yıl seçimini veri gelene kadar fallback'te tut
  useEffect(() => {
    seeded.current = false;
    setDataYears(null);
    setStartYear(FALLBACK_YEAR_SPAN[0]);
    setEndYear(FALLBACK_YEAR_SPAN[1]);
    setTargetYear(FALLBACK_YEAR_SPAN[1]);
    setCal(null);
    setArea(null);
  }, [roi]);

  useEffect(() => {
    let live = true;
    setError("");

    if (source === "live") {
      setCal(null);
      setArea(null);
      setError(
        "Canlı FIRMS yalnızca son ~24 saat (küresel) — yanma takvimi yok. "
        + "Gerçek arşiv + ROI kullan. TR yerel disk hızlı; dünya geneli = on-demand FIRMS API.",
      );
      return () => { live = false; };
    }

    if (!roi || roi.length < 3) {
      setCal(null);
      setArea(null);
      setError("");
      return () => { live = false; };
    }
    setBusy(true);
    api.areaCalendar({
      polygon: roi,
      series,
      start_year: startYear,
      end_year: endYear,
      target_year: targetYear,
    })
      .then((r) => {
        if (!live) return;
        setArea(r);
        if (r.empty) {
          setCal(null);
          setError(r.message || "Seçilen alanda / yıl aralığında tespit yok.");
          return;
        }
        setError("");
        if (r.calendar) setCal(r.calendar);
        const dr = r.meta?.date_range;
        if (dr?.[0] && dr?.[1]) {
          const sy = Number(dr[0].slice(0, 4));
          const ey = Number(dr[1].slice(0, 4));
          if (Number.isFinite(sy) && Number.isFinite(ey)) {
            setDataYears([sy, ey]);
            // Seed selectors once from archive span (don't fight user edits)
            if (!seeded.current) {
              seeded.current = true;
              const vs = r.meta?.view_years;
              const s0 = vs?.[0] ?? sy;
              const e0 = vs?.[1] ?? ey;
              setStartYear(s0);
              setEndYear(e0);
              setTargetYear(r.meta?.target_year ?? e0);
            }
          }
        }
      })
      .catch((e: Error) => {
        if (!live) return;
        setError(e.message);
        setCal(null);
      })
      .finally(() => live && setBusy(false));
    return () => { live = false; };
  }, [series, source, roi, startYear, endYear, targetYear]);

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
    const span = dataYears ?? FALLBACK_YEAR_SPAN;
    return yearsBetween(span[0], span[1]);
  }, [dataYears]);

  const applyPreset = (s: number, e: number) => {
    const lo = yearOpts[0] ?? s;
    const hi = yearOpts[yearOpts.length - 1] ?? e;
    const a = Math.max(lo, Math.min(s, hi));
    const b = Math.max(a, Math.min(e, hi));
    seeded.current = true;
    setStartYear(a);
    setEndYear(b);
    setTargetYear(b);
  };

  const vs = area?.current_month_vs_median;
  const my = area?.month_year;
  const meta = area?.meta;
  const needRoi = source === "archive" && (!roi || roi.length < 3);
  const showYearFilter = source === "archive" && !needRoi;

  return (
    <section>
      <div className={styles.panelHead}>
        <h2>Gerçek FIRMS yanma takvimi</h2>
        <div className={styles.inlineCtl}>
          <Seg
            value={series}
            onChange={setSeries}
            options={[
              { v: "hybrid", label: "Hibrit α" },
              { v: "harmonized", label: "VIIRS-eşdeğer (ML)" },
              { v: "raw", label: "Ham" },
            ]}
          />
          {source === "archive" && roi && roi.length >= 3 && (
            <button
              type="button"
              className={styles.archHeatBtn}
              disabled={csvBusy}
              onClick={() => {
                setCsvBusy(true);
                api.exportHybridCsv({ polygon: roi })
                  .then(({ text, n }) => {
                    const blob = new Blob([text], { type: "text/csv;charset=utf-8" });
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement("a");
                    a.href = url;
                    a.download = `alaz-hybrid-monthly-${n}.csv`;
                    a.click();
                    URL.revokeObjectURL(url);
                  })
                  .catch((e: Error) => setError(e.message))
                  .finally(() => setCsvBusy(false));
              }}
            >
              {csvBusy ? "CSV…" : "Hibrit CSV"}
            </button>
          )}
        </div>
      </div>
      <div className={styles.disclaimer} role="note">{t.disclaimerCalendar}</div>

      {needRoi && (
        <EmptyRoi
          message="Takvim için ROI çiz veya enlem/boylam gir (dünya geneli, ≤ ~120.000 km²)."
          onDraw={onDraw}
        />
      )}

      {showYearFilter && (
        <Card title="Yıl aralığı filtresi">
          <p className={styles.hint} style={{ marginTop: 0 }}>
            Takvim ve ay×yıl tablosu yalnızca seçtiğin yılları gösterir.
            {dataYears
              ? ` Bu ROI’de veri yaklaşık ${dataYears[0]}–${dataYears[1]}.`
              : " ROI verisi yüklenince aralık netleşir."}
          </p>
          <div className={styles.presetRow}>
            <button type="button" className={styles.archHeatBtn} onClick={() => applyPreset(2001, 2011)}>
              2001–2011 (MODIS)
            </button>
            <button type="button" className={styles.archHeatBtn} onClick={() => applyPreset(2012, yearOpts[yearOpts.length - 1] ?? 2026)}>
              2012–şimdi (VIIRS dönemi)
            </button>
            <button
              type="button"
              className={styles.archHeatBtn}
              onClick={() => {
                const hi = yearOpts[yearOpts.length - 1] ?? FALLBACK_YEAR_SPAN[1];
                applyPreset(Math.max(yearOpts[0] ?? hi - 4, hi - 4), hi);
              }}
            >
              Son 5 yıl
            </button>
            <button
              type="button"
              className={styles.archHeatBtn}
              onClick={() => applyPreset(yearOpts[0] ?? FALLBACK_YEAR_SPAN[0], yearOpts[yearOpts.length - 1] ?? FALLBACK_YEAR_SPAN[1])}
            >
              Tüm veri
            </button>
          </div>
          <div className={styles.yearRow}>
            <label>
              Başlangıç yılı
              <select
                value={startYear}
                onChange={(e) => {
                  const y = Number(e.target.value);
                  seeded.current = true;
                  setStartYear(y);
                  if (y > endYear) setEndYear(y);
                  if (y > targetYear) setTargetYear(y);
                }}
              >
                {yearOpts.map((y) => <option key={y} value={y}>{y}</option>)}
              </select>
            </label>
            <label>
              Bitiş yılı
              <select
                value={endYear}
                onChange={(e) => {
                  const y = Number(e.target.value);
                  seeded.current = true;
                  setEndYear(y);
                  if (y < startYear) setStartYear(y);
                  if (y < targetYear) setTargetYear(y);
                }}
              >
                {yearOpts.map((y) => <option key={y} value={y}>{y}</option>)}
              </select>
            </label>
            <label>
              Hedef yıl (anomali / medyan)
              <select
                value={targetYear}
                onChange={(e) => {
                  seeded.current = true;
                  setTargetYear(Number(e.target.value));
                }}
              >
                {yearsBetween(startYear, endYear).map((y) => (
                  <option key={y} value={y}>{y}</option>
                ))}
              </select>
            </label>
          </div>
          <Hint>
            Görünen aralık: <b>{startYear}–{endYear}</b>
            {meta?.view_years ? ` · API: ${meta.view_years[0]}–${meta.view_years[1]}` : ""}
            {" · "}hedef {targetYear}
          </Hint>
        </Card>
      )}

      {source === "archive" && meta && (
        <Hint>
          {meta.partial ? `Arşiv iniyor: ${meta.done_chunks}/${meta.total_chunks} dilim. ` : "Arşiv hazır. "}
          Alan ~{meta.area_km2?.toLocaleString("tr-TR")} km² · ham tespit {meta.raw_count?.toLocaleString("tr-TR")}
          {meta.date_range?.[0] ? ` · veri ${meta.date_range[0]} → ${meta.date_range[1]}` : ""}
          {meta.counts ? ` · MODIS ${meta.counts.raw_modis.toLocaleString("tr-TR")} / VIIRS ${meta.counts.raw_viirs.toLocaleString("tr-TR")}` : ""}
        </Hint>
      )}
      {source === "archive" && !needRoi && (
        <Hint>
          {series === "hybrid"
            ? <>Birincil seri <b>Hibrit α</b> — aktif hücre ızgarası + bölgesel Y=αX (VIIRS referans, yer-gerçeği değil).</>
            : series === "harmonized"
              ? <>Birincil seri <b>VIIRS-eşdeğer (ML)</b> — sentetik eğitilmiş model; VIIRS mutlak yer-gerçeği değil.</>
              : <>Ham seri — MODIS/VIIRS ayrı (karşılaştırma).</>}
        </Hint>
      )}
      {series === "hybrid" && area?.hybrid?.alpha && (
        <Hint>
          α={area.hybrid.alpha.value.toFixed(3)}
          {area.hybrid.alpha.r2 != null ? ` · R²=${area.hybrid.alpha.r2.toFixed(3)}` : ""}
          {` · n=${area.hybrid.alpha.n_months}`}
          {area.hybrid.alpha.fallback_used
            ? " · fallback katsayısı uygulandı (n<20 veya α sınır dışı)"
            : ""}
        </Hint>
      )}

      {busy && <div className={styles.banner}>Alan takvimi hesaplanıyor ({startYear}–{endYear})...</div>}
      {error && <div className={styles.banner}>{error}</div>}

      {vs?.available && (
        <Card title="Bu ay vs tarihsel medyan">
          <div className={styles.qStrip}>
            <div><b>{vs.month_total?.toLocaleString("tr-TR") ?? "-"}</b><span>{vs.year}-{String(vs.month).padStart(2, "0")} toplam</span></div>
            <div><b>{vs.historical_median?.toLocaleString("tr-TR") ?? "-"}</b><span>tarihsel medyan ({vs.n_years} yıl)</span></div>
            <div><b>{vs.anomaly_pct != null ? `${vs.anomaly_pct > 0 ? "+" : ""}${vs.anomaly_pct.toFixed(1)}%` : (vs.ratio != null ? `${vs.ratio.toFixed(2)}×` : "-")}</b><span>anomali %</span></div>
            <div><b>{vs.percentile != null ? `%${vs.percentile.toFixed(0)}` : "-"}</b><span>yüzdelik</span></div>
          </div>
          <Hint>
            Seçili ayın aktivitesi, aynı ayın çok yıllık medyanına göre sapma (yangın sayısı ≠ tespit).
            {vs.historical_p10 != null ? ` Tarihsel P10–P90: ${vs.historical_p10} – ${vs.historical_p90}.` : ""}
          </Hint>
          <AiInsight
            focus="calendar"
            section="month_vs_median"
            label="Bu ay vs medyan"
            roi={roi}
            compact
            series={series}
            startYear={startYear}
            endYear={endYear}
            targetYear={targetYear}
          />
        </Card>
      )}

      {cal && (
        <Card title={`Yıl × gün ısı haritası (${startYear}–${endYear})`}>
          <div ref={wrap}>
            <canvas ref={canvas} onMouseMove={onMove} onMouseLeave={() => setHover(HOVER_IDLE)} />
          </div>
          <Hint>{hover}</Hint>
          <AiInsight
            focus="calendar"
            section="heatmap"
            label="Isı haritası"
            roi={roi}
            compact
            series={series}
            startYear={startYear}
            endYear={endYear}
            targetYear={targetYear}
          />
        </Card>
      )}

      {my && my.years.length > 0 && (
        <Card title={`Ay × yıl yoğunluk (${startYear}–${endYear})`}>
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
          <AiInsight
            focus="calendar"
            section="month_year"
            label="Ay × yıl tablosu"
            roi={roi}
            compact
            series={series}
            startYear={startYear}
            endYear={endYear}
            targetYear={targetYear}
          />
        </Card>
      )}

      {cal && (
        <div className={styles.grid2}>
          <div>
            <ChartBox draw={draw} height={300} title={`İklimsel zarf (P10 / P50 / P90) ve ${cal.target_year}`} />
            <AiInsight
              focus="calendar"
              section="envelope"
              label="İklimsel zarf"
              roi={roi}
              compact
              series={series}
              startYear={startYear}
              endYear={endYear}
              targetYear={targetYear}
            />
          </div>
          <Card title={`${cal.target_year} anomali dönemleri`}>
            {cal.anomalies.episodes.length ? (
              <>
                <table className={styles.tbl}>
                  <thead>
                    <tr><th>Başlangıç</th><th>Bitiş</th><th>Gün</th><th>Zirve z</th></tr>
                  </thead>
                  <tbody>
                    {cal.anomalies.episodes.map((e) => (
                      <tr key={`${e.start}-${e.end}`}>
                        <td>{e.start}</td><td>{e.end}</td><td>{e.days}</td><td>{e.peak_z}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <AiInsight
                  focus="calendar"
                  section="anomalies"
                  label="Anomali dönemleri"
                  roi={roi}
                  compact
                  series={series}
                  startYear={startYear}
                  endYear={endYear}
                  targetYear={targetYear}
                />
              </>
            ) : (
              <Hint>Bu yılda işaretli anomali dönemi yok (eşik / veri). Ham veya hibrit seriyi dene.</Hint>
            )}
          </Card>
        </div>
      )}

      {area?.local_validation && (
        <Card title="Yerel referans kontrolü (MODIS→VIIRS)">
          <Hint>
            Bu ROI’de iyileşme yok — takvim için <b>Ham</b> seriyi tercih et
            {typeof area.local_validation.rmse_reduction_pct === "number"
              && area.local_validation.rmse_reduction_pct < 0
              ? ""
              : " — veya Hibrit α"}.
          </Hint>
        </Card>
      )}
    </section>
  );
}
