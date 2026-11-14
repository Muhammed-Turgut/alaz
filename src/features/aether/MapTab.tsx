import { useEffect, useRef, type ReactNode, type Ref } from "react";

import { mapStyleId, usePrefs } from "@/lib/preferences";
import { fmt } from "./format";
import type { FirePoint } from "./live/processor";
import { MapView } from "./mapView";
import type { BBox, HexPayload, LatLng, Sensor } from "./types";
import { Seg } from "./ui";
import { viz } from "./viz";
import styles from "./aether.module.css";

export interface FitCommand { id: number; bbox: BBox | null }

interface Props {
  mapRef: Ref<MapView | null>;
  live: boolean;
  archiveMode?: boolean;
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
}

const SENSORS: { v: Sensor; label: string }[] = [
  { v: "all", label: "MODIS + VIIRS" },
  { v: "MODIS", label: "MODIS" },
  { v: "VIIRS", label: "VIIRS" },
];

export function MapTab(p: Props) {
  const el = useRef<HTMLDivElement>(null);
  const view = useRef<MapView | null>(null);
  const { sensor, onSensor: setSensor } = p;
  const prefs = usePrefs();
  const styleId = mapStyleId(prefs);
  const styleRef = useRef(styleId);
  styleRef.current = styleId;
  const cb = useRef(p);
  cb.current = p;

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

  useEffect(() => { view.current?.setAppearance(styleId); }, [styleId, prefs.theme]);
  useEffect(() => { view.current?.setSensor(sensor); }, [sensor]);
  useEffect(() => { view.current?.setLive(p.live); }, [p.live]);
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

  const c = viz();
  const confLegend: [string, string][] = [
    ["Yüksek güven", c.conf.HIGH], ["Normal güven", c.conf.NOMINAL], ["Düşük güven", c.conf.LOW],
  ];

  return (
    <div>
      <div className={styles.mapBar}>
        <div className={styles.mapStats}>
          {p.live ? (
            <>
              <span><b>{fmt(p.points.length)}</b> / {fmt(p.liveTotal)} nokta (son 24 saat, gerçek FIRMS)</span>
              {sensor !== "VIIRS" && <span>MODIS {fmt(p.livePerInstrument.MODIS)}</span>}
              {sensor !== "MODIS" && <span>VIIRS {fmt(p.livePerInstrument.VIIRS)}</span>}
            </>
          ) : (
            <span>
              <b>Arşiv modu</b> — haritada canlı nokta yok; kesikli kutu Türkiye kapsaması.
              {" "}Poligon çizerek takvim / kırılma / brifing çalıştır.
            </span>
          )}
        </div>
        {p.live && <Seg value={sensor} onChange={setSensor} options={SENSORS} />}
      </div>

      {p.hasSelection ? (
        <section className={styles.inspectBand}>
          <button type="button" className={styles.bandClose} onClick={p.onClose} aria-label="Kapat">Kapat</button>
          <div className={styles.bandBody}>{p.inspector}</div>
        </section>
      ) : (
        <p className={styles.mapHint}>
          {p.live
            ? "GERÇEK NASA FIRMS (son ~24 saat). Uzaktayken kümeler (sayı), yakınlaşınca güven renkleri. Süzgeçler solda."
            : "Tarihsel arşiv: harita alan seçmek içindir. Soldan «Poligon çiz» veya aşağıdaki düğme."}
          {" "}Dünyayı sürükleyerek döndür, tekerlekle yakınlaştır.
          {!p.live && p.onDrawRoi && (
            <>
              {" "}
              <button type="button" className={styles.inlineLink} onClick={p.onDrawRoi}>Poligon çizmeye başla</button>
            </>
          )}
        </p>
      )}

      <div className={styles.mapWrap}>
        <div ref={el} className={styles.mapEl} />
        {p.live && (
          <div className={styles.legend}>
            <div>küme (uzak)</div>
            <div className={styles.legendRow}>
              <span className={styles.legendDot} style={{ background: c.cluster.small }} />az
            </div>
            <div className={styles.legendRow}>
              <span className={styles.legendDot} style={{ background: c.cluster.medium }} />orta
            </div>
            <div className={styles.legendRow}>
              <span className={styles.legendDot} style={{ background: c.text }} />çok
            </div>
            <div className={styles.legendGap}>yakın: tespit güveni</div>
            {confLegend.map(([label, color]) => (
              <div key={label} className={styles.legendRow}>
                <span className={styles.legendDot} style={{ background: color }} />{label}
              </div>
            ))}
          </div>
        )}
        {p.loading && <div className={styles.mapLoading}>NASA FIRMS verisi yükleniyor...</div>}
      </div>
    </div>
  );
}
