import { useEffect, useRef, useState, type ReactNode, type Ref } from "react";

import { useT } from "@/lib/i18n";
import { mapStyleId, usePrefs } from "@/lib/preferences";
import { api } from "./api";
import { fmt } from "./format";
import type { FirePoint } from "./live/processor";
import { MapView, type LiveMapStyle } from "./mapView";
import type { AreaDensityResult, BBox, HexPayload, LatLng, Sensor } from "./types";
import { Seg } from "./ui";
import { viz } from "./viz";
import styles from "./aether.module.css";

export interface FitCommand { id: number; bbox: BBox | null }

type DensityYears = "1" | "3" | "5" | "10" | "15";

interface Props {
  mapRef: Ref<MapView | null>;
  live: boolean;
  archiveMode?: boolean;
  roi?: LatLng[] | null;
  points: FirePoint[];
  livePerInstrument: { MODIS: number; VIIRS: number };
  liveTotal: number;
  sensor: Sensor;
  onSensor: (s: Sensor) => void;
  payload: HexPayload | null;
  regionBox: BBox | null;
  fit: FitCommand | null;
  selected: string | null;
  loading: boolean;
  visible: boolean;
  inspector: ReactNode;
  hasSelection: boolean;
  onClose: () => void;
  onHexClick: (id: string) => void;
  onRoi: (pts: LatLng[]) => void;
  onDrawing: (d: boolean) => void;
  onBasemap: (ok: boolean) => void;
  onDrawRoi?: () => void;
  /** Remote Area API year cap (default 10). Local TR archive may use 15. */
  remoteMaxYears?: number;
}
const SENSORS: { v: Sensor; label: string }[] = [
  { v: "all", label: "MODIS + VIIRS" },
  { v: "MODIS", label: "MODIS" },
  { v: "VIIRS", label: "VIIRS" },
];

const YEAR_OPTS: { v: DensityYears; label: string }[] = [
  { v: "1", label: "1y" },
  { v: "3", label: "3y" },
  { v: "5", label: "5y" },
  { v: "10", label: "10y" },
  { v: "15", label: "15y" },
];

export function MapTab(p: Props) {
  const t = useT();
  const en = t.lang === "en";
  const el = useRef<HTMLDivElement>(null);
  const view = useRef<MapView | null>(null);
  const { sensor, onSensor: setSensor } = p;
  const [liveStyle, setLiveStyle] = useState<LiveMapStyle>("points");
  const [archHeat, setArchHeat] = useState(false);
  const [years, setYears] = useState<DensityYears>("5");
  const [density, setDensity] = useState<AreaDensityResult | null>(null);
  const [densBusy, setDensBusy] = useState(false);
  const [densErr, setDensErr] = useState("");
  const prefs = usePrefs();
  const styleId = mapStyleId(prefs);
  const styleRef = useRef(styleId);
  styleRef.current = styleId;
  const cb = useRef(p);
  cb.current = p;

  const LIVE_STYLES: { v: LiveMapStyle; label: string }[] = [
    { v: "points", label: en ? "Points" : "Noktalar" },
    { v: "heatmap", label: en ? "Density" : "Yoğunluk" },
    { v: "heatmap_frp", label: en ? "FRP-weighted" : "FRP ağırlıklı" },
  ];

  const hasRoi = Boolean(p.roi && p.roi.length >= 3);
  const remoteCap = p.remoteMaxYears ?? 10;
  const isRemoteDensity = density?.meta?.local_archive === false;
  const effectiveYears = density?.effective_years ?? density?.years ?? Number(years);
  const yearOpts = YEAR_OPTS.filter((o) => {
    // Always allow selecting up to remoteCap; 15y stays for local TR ROIs.
    if (Number(o.v) <= remoteCap) return true;
    if (isRemoteDensity) return false;
    return true;
  });

  useEffect(() => {
    if (!el.current) return;
    const mv = new MapView({
      el: el.current,
      styleId: styleRef.current,
      onHexClick: (id) => cb.current.onHexClick(id),
      onRoi: (pts) => cb.current.onRoi(pts),
      onLegend: () => undefined,
      onBasemap: (ok) => cb.current.onBasemap(ok),
      onDrawing: (d) => cb.current.onDrawing(d),
    });
    view.current = mv;
    const r = cb.current.mapRef;
    if (typeof r === "function") r(mv);
    else if (r) (r as { current: MapView | null }).current = mv;
    mv.invalidate();
    return () => {
      mv.destroy();
      view.current = null;
      if (typeof r === "function") r(null);
      else if (r) (r as { current: MapView | null }).current = null;
    };
  }, []);

  useEffect(() => { view.current?.setAppearance(styleId); }, [styleId, prefs.theme, prefs.calRamp]);
  useEffect(() => { view.current?.setSensor(sensor); }, [sensor]);
  useEffect(() => { view.current?.setLive(p.live); }, [p.live]);
  useEffect(() => { view.current?.setLiveStyle(liveStyle); }, [liveStyle]);
  useEffect(() => { view.current?.setPoints(p.points); }, [p.points]);
  useEffect(() => { view.current?.setCells(p.payload?.cells ?? []); }, [p.payload]);
  useEffect(() => { view.current?.showRegionBox(p.regionBox); }, [p.regionBox]);
  useEffect(() => { view.current?.select(p.selected); }, [p.selected, p.payload]);
  useEffect(() => {
    if (!p.fit) return;
    if (p.fit.bbox) view.current?.fitBBox(p.fit.bbox);
    else view.current?.fitWorld();
  }, [p.fit]);
  useEffect(() => { if (p.visible) view.current?.invalidate(); }, [p.visible]);

  useEffect(() => {
    if (!p.live) setLiveStyle("points");
    else {
      setArchHeat(false);
      setDensity(null);
      view.current?.setArchiveHeat(false);
      view.current?.setArchiveDensity([]);
    }
  }, [p.live]);

  // Fetch archive density when ROI + years + heat toggle
  useEffect(() => {
    if (p.live || !p.archiveMode || !archHeat) {
      view.current?.setArchiveHeat(false);
      return;
    }
    if (!hasRoi || !p.roi) {
      setDensity(null);
      setDensErr(en ? "Draw a ROI polygon first." : "Önce ROI poligonu çiz.");
      view.current?.setArchiveHeat(false);
      view.current?.setArchiveDensity([]);
      return;
    }
    let alive = true;
    setDensBusy(true);
    setDensErr("");
    const y = Number(years);
    const inst = sensor === "all" ? "all" : sensor;
    api.areaDensity({ polygon: p.roi, years: y, instrument: inst })
      .then((res) => {
        if (!alive) return;
        setDensity(res);
        if (res.empty || !res.points?.length) {
          setDensErr(res.message || (en ? "No detections in window." : "Pencerede tespit yok."));
          view.current?.setArchiveDensity([]);
          view.current?.setArchiveHeat(true);
          return;
        }
        view.current?.setArchiveDensity(
          res.points.map((pt) => ({
            lat: pt.lat,
            lon: pt.lon,
            w: pt.w,
            n: pt.n,
            h3: pt.h3,
            boundary: pt.boundary,
            frp_sum: pt.frp_sum,
            platforms: pt.platforms,
            instruments: pt.instruments,
          })),
          res.window ?? null,
        );
        view.current?.setArchiveHeat(true);
      })
      .catch((e: Error) => {
        if (!alive) return;
        setDensErr(e.message);
        view.current?.setArchiveHeat(false);
        view.current?.setArchiveDensity([]);
      })
      .finally(() => alive && setDensBusy(false));
    return () => { alive = false; };
  }, [p.live, p.archiveMode, archHeat, hasRoi, p.roi, years, sensor, en]);

  useEffect(() => {
    if (!isRemoteDensity) return;
    if (Number(years) > remoteCap) setYears(String(remoteCap) as DensityYears);
  }, [isRemoteDensity, years, remoteCap]);

  const c = viz();
  const confLegend: [string, string][] = [
    [en ? "High conf." : "Yüksek güven", c.conf.HIGH],
    [en ? "Nominal" : "Normal güven", c.conf.NOMINAL],
    [en ? "Low conf." : "Düşük güven", c.conf.LOW],
  ];
  const heatLegend = c.calRamp;
  const legend = density?.legend;

  return (
    <div>
      <div className={styles.mapBar}>
        <div className={styles.mapStats}>
          {p.live ? (
            <>
              <span>
                <b>{fmt(p.points.length)}</b> / {fmt(p.liveTotal)}{" "}
                {en ? "detections (last ~24 h, real FIRMS)" : "nokta (son 24 saat, gerçek FIRMS)"}
              </span>
              {sensor !== "VIIRS" && <span>MODIS {fmt(p.livePerInstrument.MODIS)}</span>}
              {sensor !== "MODIS" && <span>VIIRS {fmt(p.livePerInstrument.VIIRS)}</span>}
            </>
          ) : archHeat && density && !density.empty ? (
            <>
              <span>
                <b>{fmt(density.n_detections ?? 0)}</b>{" "}
                {en
                  ? `detections · last ${effectiveYears}y`
                  : `tespit · son ${effectiveYears} yıl`}
                {density.requested_years != null && density.requested_years !== effectiveYears
                  ? (en ? ` (asked ${density.requested_years}y)` : ` (istenilen ${density.requested_years}y)`)
                  : ""}
              </span>
              <span>
                {density.window?.[0]} → {density.window?.[1]} UTC
              </span>
              <span>{fmt(density.n_cells ?? 0)} H3</span>
              {isRemoteDensity && (
                <span>
                  {en ? `remote ≤${remoteCap}y` : `uzaktan ≤${remoteCap}y`}
                </span>
              )}
            </>
          ) : (
            <span>
              <b>{en ? "Archive mode" : "Arşiv modu"}</b>
              {en
                ? " — draw a polygon, then open density map for last X years."
                : " — poligon çiz, sonra son X yıl yoğunluk haritasını aç."}
            </span>
          )}
        </div>
        {p.live ? (
          <div className={styles.mapBarCtl}>
            <Seg value={liveStyle} onChange={setLiveStyle} options={LIVE_STYLES} />
            <Seg value={sensor} onChange={setSensor} options={SENSORS} />
          </div>
        ) : p.archiveMode ? (
          <div className={styles.mapBarCtl}>
            <button
              type="button"
              className={`${styles.archHeatBtn} ${archHeat ? styles.archHeatOn : ""}`}
              aria-pressed={archHeat}
              disabled={!hasRoi && !archHeat}
              title={!hasRoi ? (en ? "Draw a ROI first" : "Önce ROI çiz") : undefined}
              onClick={() => setArchHeat((v) => !v)}
            >
              {archHeat
                ? (en ? "Hide density" : "Yoğunluğu gizle")
                : (en ? "Density map" : "Yoğunluk haritası")}
            </button>
            {archHeat && (
              <>
                <Seg
                  value={years}
                  onChange={setYears}
                  options={yearOpts.map((o) => ({
                    v: o.v,
                    label: en
                      ? (Number(o.v) > remoteCap ? `${o.v}y (local)` : `${o.v}y`)
                      : (Number(o.v) > remoteCap ? `${o.v} yıl (yerel)` : `${o.v} yıl`),
                  }))}
                />
                <Seg value={sensor} onChange={setSensor} options={SENSORS} />
              </>
            )}
          </div>
        ) : null}
      </div>
      <p className={styles.hint}>{t.mapHotspotNote}</p>
      {p.live && liveStyle === "heatmap" && (
        <p className={styles.hint} role="note">
          {en
            ? "Density = equal-weight FIRMS hotspot detections (last ~24 h), not fire count or burned area."
            : "Yoğunluk = eşit ağırlıklı FIRMS sıcak nokta tespitleri (son ~24 sa); yangın sayısı veya yanmış alan değil."}
        </p>
      )}
      {p.live && liveStyle === "heatmap_frp" && (
        <p className={styles.hint} role="note">
          {en
            ? "FRP-weighted hotspot view (mild boost). FRP is not directly comparable across MODIS/VIIRS. Not fire count or burned area."
            : "FRP ağırlıklı hotspot görünümü (hafif boost). FRP, MODIS/VIIRS arasında doğrudan karşılaştırılamaz. Yangın sayısı/yanmış alan değil."}
        </p>
      )}
      {!p.live && archHeat && (
        <p className={styles.hint} role="note">
          {en
            ? `H3 polygon density = detections per cell (~5.2 km²). Color bins = exact counts. Not fire events or burned area. Missing cells ≠ zero fires. Remote ROI: last ${remoteCap}y cap.`
            : `H3 polygon yoğunluğu = hücre başına tespit (~5,2 km²). Renk dilimleri = tam sayılar. Yangın/yanmış alan değil. Eksik hücre ≠ sıfır yangın. Uzaktan ROI: son ${remoteCap} yıl üst sınırı.`}
        </p>
      )}
      {density?.year_note && archHeat && (
        <div className={`${styles.banner} ${styles.bannerInfo}`}>{density.year_note}</div>
      )}
      {archHeat && density?.meta?.partial && (
        <div className={`${styles.banner} ${styles.bannerInfo}`}>
          {en
            ? `Partial remote data: ${density.meta.done_chunks ?? "?"}/${density.meta.total_chunks ?? "?"} chunks`
              + (density.meta.failed_chunks ? `, ${density.meta.failed_chunks} failed` : "")
              + (density.meta.truncated ? ", row-cap truncated" : "")
              + "."
            : `Kısmi uzaktan veri: ${density.meta.done_chunks ?? "?"}/${density.meta.total_chunks ?? "?"} dilim`
              + (density.meta.failed_chunks ? `, ${density.meta.failed_chunks} başarısız` : "")
              + (density.meta.truncated ? ", satır limiti kesildi" : "")
              + "."}
        </div>
      )}
      {densErr && archHeat && <div className={styles.banner}>{densErr}</div>}

      {p.hasSelection ? (
        <section className={styles.inspectBand}>
          <button type="button" className={styles.bandClose} onClick={p.onClose} aria-label={en ? "Close" : "Kapat"}>
            {en ? "Close" : "Kapat"}
          </button>
          <div className={styles.bandBody}>{p.inspector}</div>
        </section>
      ) : (
        <p className={styles.mapHint}>
          {p.live
            ? (liveStyle === "heatmap" || liveStyle === "heatmap_frp"
              ? (en
                ? (liveStyle === "heatmap_frp"
                  ? "REAL NASA FIRMS FRP-weighted hotspot view. Toggle Density for equal weight, Points for clusters."
                  : "REAL NASA FIRMS equal-weight detection density. Toggle Points for cluster/confidence view.")
                : (liveStyle === "heatmap_frp"
                  ? "GERÇEK NASA FIRMS FRP ağırlıklı görünüm. Eşit ağırlık için Yoğunluk, kümeler için Noktalar."
                  : "GERÇEK NASA FIRMS eşit ağırlıklı tespit yoğunluğu. Kümeler için «Noktalar»."))
              : (en
                ? "REAL NASA FIRMS (last ~24 h). Clusters = detection counts, not fire events."
                : "GERÇEK NASA FIRMS (son ~24 saat). Kümeler = tespit sayısı, yangın olayı değil."))
            : (en
              ? "Archive: draw a ROI, then Density map + last X years (H3 polygons)."
              : "Arşiv: ROI çiz, ardından Yoğunluk haritası + son X yıl (H3 polygon).")}
          {" "}
          {en ? "Drag to pan, scroll to zoom." : "Sürükle / tekerlekle yakınlaştır."}
          {!p.live && p.onDrawRoi && (
            <>
              {" "}
              <button type="button" className={styles.inlineLink} onClick={p.onDrawRoi}>
                {en ? "Start drawing polygon" : "Poligon çizmeye başla"}
              </button>
            </>
          )}
        </p>
      )}

      <div className={styles.mapWrap}>
        <div ref={el} className={styles.mapEl} />
        {p.live && liveStyle === "points" && (
          <div className={styles.legend}>
            <div>{en ? "cluster (detections)" : "küme (tespit)"}</div>
            <div className={styles.legendRow}>
              <span className={styles.legendDot} style={{ background: c.cluster.small }} />{en ? "few" : "az"}
            </div>
            <div className={styles.legendRow}>
              <span className={styles.legendDot} style={{ background: c.cluster.medium }} />{en ? "mid" : "orta"}
            </div>
            <div className={styles.legendRow}>
              <span className={styles.legendDot} style={{ background: c.text }} />{en ? "many" : "çok"}
            </div>
            <div className={styles.legendGap}>{en ? "close: detection confidence" : "yakın: tespit güveni"}</div>
            {confLegend.map(([label, color]) => (
              <div key={label} className={styles.legendRow}>
                <span className={styles.legendDot} style={{ background: color }} />{label}
              </div>
            ))}
          </div>
        )}
        {((p.live && (liveStyle === "heatmap" || liveStyle === "heatmap_frp")) || (!p.live && archHeat)) && (
          <div className={styles.legend} aria-label={en ? "Density legend" : "Yoğunluk lejantı"}>
            <div>
              {legend
                ? (en ? legend.title_en : legend.title_tr)
                : (p.live && liveStyle === "heatmap_frp"
                  ? (en ? "FRP-weighted hotspots" : "FRP ağırlıklı hotspot")
                  : (en ? "detection density" : "tespit yoğunluğu"))}
            </div>
            <div className={styles.heatBar} style={{ background: `linear-gradient(90deg, ${heatLegend.join(", ")})` }} />
            <div className={styles.heatBarLabels}>
              <span>{en ? "low" : "az"}</span>
              <span>{en ? "high" : "çok"}</span>
            </div>
            {legend?.bins && legend.bins.length > 0 && (
              <ul className={styles.heatBins}>
                {legend.bins.map((b) => (
                  <li key={b.level}>{en ? b.label_en : b.label_tr}</li>
                ))}
              </ul>
            )}
            {legend && (
              <>
                <div className={styles.legendGap}>{en ? legend.unit_en : legend.unit_tr}</div>
                <div className={styles.heatNot}>{en ? legend.not_en : legend.not_tr}</div>
              </>
            )}
            {!legend && (
              <div className={styles.legendGap}>{en ? "not burned area" : "yanmış alan değil"}</div>
            )}
            {density && !density.empty && density.n_max_per_cell != null && (
              <div className={styles.heatMeta}>
                {en ? "Max / cell" : "Hücre max"}: {fmt(density.n_max_per_cell)}
                {density.excluded_lowconf_or_static
                  ? ` · −${fmt(density.excluded_lowconf_or_static)} ${en ? "filtered" : "süzüldü"}`
                  : ""}
              </div>
            )}
          </div>
        )}
        {(p.loading || densBusy) && (
          <div className={styles.mapLoading} role="status" aria-live="polite">
            {densBusy
              ? (en
                ? "Loading density… First remote download may take several minutes; later loads use cache."
                : "Yoğunluk yükleniyor… İlk uzaktan indirme birkaç dakika sürebilir; sonraki yüklemeler önbellekten.")
              : (en ? "Loading NASA FIRMS…" : "NASA FIRMS verisi yükleniyor...")}
          </div>
        )}
      </div>
    </div>
  );
}
