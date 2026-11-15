import mapboxgl from "mapbox-gl";
import type { Feature, FeatureCollection } from "geojson";
import "mapbox-gl/dist/mapbox-gl.css";

import { fmt } from "./format";
import { APP_CONFIG } from "./live/config";
import type { FirePoint } from "./live/processor";
import type { BBox, HexCell, LatLng, Sensor } from "./types";
import { viz } from "./viz";

const MAPBOX_TOKEN = import.meta.env.VITE_MAPBOX_TOKEN;

/** Token varsa Mapbox stili; yoksa OpenStreetMap raster stili (globe yine çalışır). */
function mapStyle(id: string): string | mapboxgl.StyleSpecification {
  if (MAPBOX_TOKEN) return `mapbox://styles/mapbox/${id}`;
  return {
    version: 8,
    sources: {
      osm: { type: "raster", tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"], tileSize: 256, maxzoom: 19, attribution: "&copy; OpenStreetMap" },
    },
    layers: [{ id: "osm", type: "raster", source: "osm" }],
  };
}

export interface LegendInfo {
  vmax: number;
  sensor: Sensor;
}

export interface MapViewOptions {
  el: HTMLElement;
  /** Mapbox stil kimliği (light-v11, dark-v11, satellite-streets-v12). */
  styleId: string;
  onHexClick: (id: string) => void;
  onRoi: (pts: LatLng[]) => void;
  onLegend: (l: LegendInfo) => void;
  onBasemap: (ok: boolean) => void;
  onDrawing: (drawing: boolean) => void;
}

type Fc = FeatureCollection;
const EMPTY: Fc = { type: "FeatureCollection", features: [] };

const HEX_LAYERS = ["hex-fill", "hex-dots"];
const HEX_VISUAL = ["hex-fill", "hex-line", "hex-dots"];
/** Cluster + individual detection points (live “points” style). */
const FIRE_POINT_LAYERS = ["fire-clusters", "fire-cluster-count", "fire-points"];
/** Kernel density of FIRMS hotspot detections (live “heatmap” style). */
const FIRE_HEAT_LAYERS = ["fire-heatmap"];
const FIRE_LAYERS = [...FIRE_POINT_LAYERS, ...FIRE_HEAT_LAYERS];
const ALL_LAYERS = [...HEX_VISUAL, ...FIRE_LAYERS, "box-line", "sel-line", "sel-dot", "roi-fill", "roi-line", "draw-line", "draw-pts"];
const ALL_SOURCES = ["hexes", "fires", "fires-heat", "box", "sel", "roi", "draw"];

/** points = clusters; heatmap = equal-weight detection density; heatmap_frp = FRP-weighted view. */
export type LiveMapStyle = "points" | "heatmap" | "heatmap_frp";

export type ArchiveDensityPoint = {
  lat: number;
  lon: number;
  w: number;
  n: number;
  h3?: string;
  boundary?: LatLng[];
  frp_sum?: number;
  platforms?: Record<string, number>;
  instruments?: Record<string, number>;
  window?: [string | null, string | null];
};

const esc = (v: unknown) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c] as string);

const ring = (pts: LatLng[]): [number, number][] => {
  const r = pts.map(([la, lo]) => [lo, la] as [number, number]);
  if (r.length) r.push(r[0]);
  return r;
};

/** 3B globe (Mapbox GL): ham tespitler (MODIS / VIIRS / ikisi) H3 altıgenleri olarak, elle ROI çizimi. */
export class MapView {
  private sensor: Sensor = "all";
  private cells: HexCell[] = [];
  private live = false;
  private liveStyle: LiveMapStyle = "points";
  /** Archive multi-year density as H3 polygons (exact per-cell counts). */
  private archiveHeat = false;
  /** When true in archive mode, show FIRMS point layer (hybrid VIIRS overlay). */
  private archivePoints = false;
  private fires: FirePoint[] = [];
  private archiveDensity: ArchiveDensityPoint[] = [];
  private densWin: [string | null, string | null] | null = null;
  private selected: string | null = null;
  private drawing = false;
  private points: LatLng[] = [];
  private roi: LatLng[] | null = null;
  private box: BBox | null = null;
  private tileErrors = 0;
  private ready = false;
  private bound = false;
  private styleId: string;
  private dead = false;
  private map: mapboxgl.Map;
  private tip: mapboxgl.Popup;
  private pop: mapboxgl.Popup;

  constructor(private opt: MapViewOptions) {
    if (MAPBOX_TOKEN) mapboxgl.accessToken = MAPBOX_TOKEN;
    this.styleId = opt.styleId;
    this.map = new mapboxgl.Map({
      container: opt.el,
      style: mapStyle(opt.styleId),
      projection: "globe",
      center: [10, 22],
      zoom: 1.4,
      minZoom: 0.8,
      maxZoom: 18,
      attributionControl: true,
      dragRotate: true,
    });
    this.map.addControl(new mapboxgl.NavigationControl({ showCompass: true, visualizePitch: true }), "top-right");
    this.tip = new mapboxgl.Popup({ closeButton: false, closeOnClick: false, offset: 10, className: "hexTip" });

    this.pop = new mapboxgl.Popup({ closeButton: true, closeOnClick: true, offset: 8, className: "firePop", maxWidth: "260px" });

    this.map.on("style.load", () => this.setup());
    this.map.on("error", (e) => {
      const status = (e.error as { status?: number } | undefined)?.status;
      if (status === undefined) return;
      this.tileErrors += 1;
      if (this.tileErrors === 3) this.opt.onBasemap(false);
    });
    this.map.on("sourcedata", (e) => {
      if (
        e.sourceId !== "hexes" && e.sourceId !== "fires" && e.sourceId !== "fires-heat"
        && e.isSourceLoaded && this.tileErrors < 3
      ) {
        this.opt.onBasemap(true);
      }
    });
    this.map.on("click", (e) => this.mapClick(e));
    this.map.on("dblclick", (e) => {
      if (this.drawing) { e.preventDefault(); this.finishRoi(); }
    });
  }

  destroy() {
    this.dead = true;
    this.tip.remove();
    this.pop.remove();
    this.map.remove();
  }

  // ------------------------------------------------------------ katmanlar
  private setup() {
    if (this.dead) return;
    const v = viz();
    const m = this.map;

    m.setFog({
      color: v.atmosLow,
      "high-color": v.atmosHigh,
      "space-color": v.space,
      "horizon-blend": 0.04,
      "star-intensity": 0.5,
    });

    const stops = v.ramp.flatMap((c, i) => [i / (v.ramp.length - 1), c]);
    const color: mapboxgl.ExpressionSpecification = ["interpolate", ["linear"], ["get", "t"], ...stops];

    for (const id of ALL_SOURCES) {
      if (m.getSource(id)) continue;
      if (id === "fires") {
        m.addSource(id, {
          type: "geojson", data: EMPTY, cluster: true,
          clusterMaxZoom: APP_CONFIG.MAP.CLUSTER.MAX_ZOOM, clusterRadius: APP_CONFIG.MAP.CLUSTER.MAX_RADIUS,
        });
      } else if (id === "fires-heat") {
        // Heatmap needs unclustered points (kernel density of detections).
        m.addSource(id, { type: "geojson", data: EMPTY });
      } else {
        m.addSource(id, { type: "geojson", data: EMPTY });
      }
    }

    if (!m.getLayer("hex-fill")) {
      m.addLayer({
        id: "hex-fill", type: "fill", source: "hexes", filter: ["==", ["geometry-type"], "Polygon"],
        paint: { "fill-color": color, "fill-opacity": ["interpolate", ["linear"], ["zoom"], 8, 0, 10, 0.85] },
      });
      m.addLayer({
        id: "hex-line", type: "line", source: "hexes", filter: ["==", ["geometry-type"], "Polygon"],
        paint: { "line-color": v.text, "line-opacity": ["interpolate", ["linear"], ["zoom"], 8, 0, 10, 0.3], "line-width": 0.6 },
      });
      m.addLayer({
        id: "hex-dots", type: "circle", source: "hexes", filter: ["==", ["geometry-type"], "Point"],
        paint: {
          "circle-color": color,
          "circle-radius": ["interpolate", ["linear"], ["zoom"], 1, 3, 5, 5, 9, 7],
          "circle-opacity": ["interpolate", ["linear"], ["zoom"], 8, 0.9, 10, 0],
          "circle-stroke-color": v.surface,
          "circle-stroke-width": ["interpolate", ["linear"], ["zoom"], 8, 0.6, 10, 0],
          "circle-stroke-opacity": ["interpolate", ["linear"], ["zoom"], 8, 1, 10, 0],
        },
      });
      this.addFireLayers(v);
      m.addLayer({
        id: "box-line", type: "line", source: "box",
        paint: { "line-color": v.muted, "line-width": 1, "line-dasharray": [3, 3] },
      });
      m.addLayer({ id: "sel-line", type: "line", source: "sel", filter: ["==", ["geometry-type"], "Polygon"], paint: { "line-color": v.selected, "line-width": 3 } });
      m.addLayer({
        id: "sel-dot", type: "circle", source: "sel", filter: ["==", ["geometry-type"], "Point"],
        paint: { "circle-radius": 9, "circle-color": "rgba(0,0,0,0)", "circle-stroke-color": v.selected, "circle-stroke-width": 2.5 },
      });
      m.addLayer({ id: "roi-fill", type: "fill", source: "roi", paint: { "fill-color": v.roi, "fill-opacity": 0.08 } });
      m.addLayer({ id: "roi-line", type: "line", source: "roi", paint: { "line-color": v.roi, "line-width": 2, "line-dasharray": [3, 2] } });
      m.addLayer({ id: "draw-line", type: "line", source: "draw", filter: ["==", ["geometry-type"], "LineString"], paint: { "line-color": v.roi, "line-width": 2 } });
      m.addLayer({
        id: "draw-pts", type: "circle", source: "draw", filter: ["==", ["geometry-type"], "Point"],
        paint: { "circle-radius": 4.5, "circle-color": v.surface, "circle-stroke-color": v.roi, "circle-stroke-width": 2 },
      });

    }

    if (!this.bound) {
      this.bound = true;
      this.bindFireEvents();
      for (const id of HEX_LAYERS) {
        m.on("mousemove", id, (e) => {
          if (this.drawing) return;
          const f = e.features?.[0] as unknown as Feature | undefined;
          if (!f) return;
          m.getCanvas().style.cursor = "pointer";
          this.tip.setLngLat(e.lngLat).setHTML(this.tipHtml(f.properties ?? {})).addTo(m);
        });
        m.on("mouseleave", id, () => {
          m.getCanvas().style.cursor = this.drawing ? "crosshair" : "";
          this.tip.remove();
        });
        m.on("click", id, (e) => {
          const f = e.features?.[0] as unknown as Feature | undefined;
          const props = f?.properties ?? {};
          if (this.archiveHeat && props.dens) {
            this.pop.setLngLat(e.lngLat).setHTML(this.densHtml(props)).addTo(m);
            return;
          }
          const h3 = props.h3 as string | undefined;
          if (h3 && !this.drawing) this.opt.onHexClick(h3);
        });
      }
    }

    this.ready = true;
    this.refresh();
  }

  private addFireLayers(v: ReturnType<typeof viz>) {
    const m = this.map;
    const { MEDIUM_THRESHOLD: mid, LARGE_THRESHOLD: big } = APP_CONFIG.MAP.CLUSTER;
    m.addLayer({
      id: "fire-clusters", type: "circle", source: "fires", filter: ["has", "point_count"],
      paint: {
        "circle-color": ["step", ["get", "point_count"], v.cluster.small, mid, v.cluster.medium, big, v.text],
        "circle-radius": ["step", ["get", "point_count"], 14, mid, 19, big, 25],
        "circle-stroke-color": v.surface,
        "circle-stroke-width": 1.5,
      },
    });
    if (MAPBOX_TOKEN) {
      m.addLayer({
        id: "fire-cluster-count", type: "symbol", source: "fires", filter: ["has", "point_count"],
        layout: { "text-field": ["get", "point_count_abbreviated"], "text-size": 12, "text-allow-overlap": true },
        paint: { "text-color": ["step", ["get", "point_count"], v.text, big, v.surface] },
      });
    }
    m.addLayer({
      id: "fire-points", type: "circle", source: "fires", filter: ["!", ["has", "point_count"]],
      paint: {
        "circle-color": ["match", ["get", "conf"], "HIGH", v.conf.HIGH, "NOMINAL", v.conf.NOMINAL, "LOW", v.conf.LOW, v.conf.UNKNOWN],
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 2, 3, 8, 5, 14, 8],
        "circle-stroke-color": v.surface,
        "circle-stroke-width": 0.8,
      },
    });
    // Live detection-density heatmap (not burned area / fire-event count).
    // Default weight = 1 per hotspot; heatmap_frp uses mild FRP boost (not cross-sensor FRP equality).
    const ramp = v.calRamp;
    m.addLayer({
      id: "fire-heatmap",
      type: "heatmap",
      source: "fires-heat",
      maxzoom: 15,
      paint: {
        "heatmap-weight": [
          "interpolate", ["linear"], ["get", "w"],
          0, 0, 0.2, 0.35, 1, 1,
        ],
        "heatmap-intensity": [
          "interpolate", ["linear"], ["zoom"],
          0, 0.55, 4, 0.9, 9, 1.35, 13, 1.8,
        ],
        "heatmap-color": [
          "interpolate", ["linear"], ["heatmap-density"],
          0, "rgba(0,0,0,0)",
          0.12, ramp[0],
          0.35, ramp[1],
          0.55, ramp[2],
          0.78, ramp[3],
          1, ramp[4],
        ],
        "heatmap-radius": [
          "interpolate", ["linear"], ["zoom"],
          0, 2, 3, 8, 6, 18, 9, 28, 12, 40,
        ],
        "heatmap-opacity": [
          "interpolate", ["linear"], ["zoom"],
          7, 0.92, 12, 0.75, 14, 0.35, 15, 0,
        ],
      },
    }, "fire-clusters");
  }

  private bindFireEvents() {
    const m = this.map;
    m.on("click", "fire-clusters", (e) => {
      if (this.drawing) return;
      const f = e.features?.[0];
      const id = f?.properties?.cluster_id as number | undefined;
      if (id === undefined || !f) return;
      this.src("fires")?.getClusterExpansionZoom(id, (err, zoom) => {
        if (err || zoom == null) return;
        m.easeTo({ center: (f.geometry as GeoJSON.Point).coordinates as [number, number], zoom: zoom + 0.5 });
      });
    });
    m.on("click", "fire-points", (e) => {
      if (this.drawing) return;
      const f = e.features?.[0];
      if (!f) return;
      this.pop.setLngLat((f.geometry as GeoJSON.Point).coordinates as [number, number]).setHTML(this.pointHtml(f.properties ?? {})).addTo(m);
    });
    for (const id of ["fire-clusters", "fire-points"]) {
      m.on("mouseenter", id, () => { if (!this.drawing) m.getCanvas().style.cursor = "pointer"; });
      m.on("mouseleave", id, () => { m.getCanvas().style.cursor = this.drawing ? "crosshair" : ""; });
    }
  }

  private pointHtml(p: Record<string, unknown>): string {
    return `<b>${esc(p.inst)} &middot; ${esc(p.sat)}</b><br>${esc(p.at)}<br>`
      + `Parlaklık sıcaklığı: ${fmt(Number(p.temp), 1)} &deg;C<br>FRP: ${fmt(Number(p.frp), 1)} MW<br>Güven: ${esc(p.label)}`;
  }

  private applyMode() {
    if (!this.ready) return;
    const showPoints =
      (this.live && this.liveStyle === "points")
      || (!this.live && this.archivePoints);
    const showLiveHeat = this.live && (this.liveStyle === "heatmap" || this.liveStyle === "heatmap_frp");
    const showArchiveHeat = !this.live && this.archiveHeat;
    // Archive density uses H3 polygon fill on hexes (exact per-cell counts), not kernel heatmap.
    for (const id of HEX_VISUAL) {
      if (!this.map.getLayer(id)) continue;
      const on = showArchiveHeat || (!this.live && !this.archiveHeat && !this.archivePoints);
      this.map.setLayoutProperty(id, "visibility", on ? "visible" : "none");
    }
    // Density polygons should stay visible at low zoom (unlike live hex fade-in).
    if (this.map.getLayer("hex-fill")) {
      this.map.setPaintProperty(
        "hex-fill",
        "fill-opacity",
        showArchiveHeat
          ? 0.72
          : (["interpolate", ["linear"], ["zoom"], 8, 0, 10, 0.85] as mapboxgl.ExpressionSpecification),
      );
    }
    if (this.map.getLayer("hex-line")) {
      this.map.setPaintProperty(
        "hex-line",
        "line-opacity",
        showArchiveHeat
          ? 0.35
          : (["interpolate", ["linear"], ["zoom"], 8, 0, 10, 0.3] as mapboxgl.ExpressionSpecification),
      );
    }
    for (const id of FIRE_POINT_LAYERS) {
      if (this.map.getLayer(id)) this.map.setLayoutProperty(id, "visibility", showPoints ? "visible" : "none");
    }
    for (const id of FIRE_HEAT_LAYERS) {
      if (this.map.getLayer(id)) this.map.setLayoutProperty(id, "visibility", showLiveHeat ? "visible" : "none");
    }
    if (this.map.getLayer("fire-heatmap") && showLiveHeat) {
      const radius = ["interpolate", ["linear"], ["zoom"], 0, 2, 3, 8, 6, 18, 9, 28, 12, 40] as mapboxgl.ExpressionSpecification;
      const intensity = ["interpolate", ["linear"], ["zoom"], 0, 0.55, 4, 0.9, 9, 1.35, 13, 1.8] as mapboxgl.ExpressionSpecification;
      this.map.setPaintProperty("fire-heatmap", "heatmap-radius", radius);
      this.map.setPaintProperty("fire-heatmap", "heatmap-intensity", intensity);
    }
    if (this.live && !showArchiveHeat) this.tip.remove();
    if (!showPoints && !showArchiveHeat) this.pop.remove();
  }

  /** Canlı mod: kümelenmiş noktalar veya ısı haritası; kapalıyken H3 / arşiv yoğunluk. */
  setLive(live: boolean) {
    this.live = live;
    if (live) {
      this.archiveHeat = false;
      this.archiveDensity = [];
      this.densWin = null;
      this.src("hexes")?.setData({ type: "FeatureCollection", features: [] });
      this.cells = [];
    }
    this.applyMode();
    if (!live) this.renderArchiveDensity();
  }

  /** Live display: points, equal-weight density heatmap, or FRP-weighted heatmap. */
  setLiveStyle(style: LiveMapStyle) {
    this.liveStyle = style;
    this.applyMode();
    this.renderFires();
  }

  /** Toggle archive multi-year H3 density polygons (requires setArchiveDensity). */
  setArchiveHeat(on: boolean) {
    this.archiveHeat = on && !this.live;
    this.applyMode();
    this.renderArchiveDensity();
  }

  /** Show real VIIRS (or other) point coordinates in archive/hybrid mode. */
  setArchivePointsVisible(on: boolean) {
    this.archivePoints = on && !this.live;
    this.applyMode();
    this.renderFires();
  }

  setArchiveDensity(points: ArchiveDensityPoint[], window?: [string | null, string | null] | null) {
    this.archiveDensity = points;
    this.densWin = window ?? null;
    this.renderArchiveDensity();
  }

  setPoints(points: FirePoint[]) {
    this.fires = points;
    this.renderFires();
  }

  private renderArchiveDensity() {
    if (!this.ready || this.dead) return;
    if (this.live || !this.archiveHeat) {
      if (!this.live) this.src("hexes")?.setData(EMPTY);
      this.src("fires-heat")?.setData(EMPTY);
      return;
    }
    const nMax = Math.max(1, ...this.archiveDensity.map((p) => p.n), 1);
    const features: Feature[] = [];
    for (const p of this.archiveDensity) {
      if (p.n <= 0) continue;
      const t = Math.min(1, Math.sqrt(p.n / nMax));
      const platforms = p.platforms ? JSON.stringify(p.platforms) : "";
      const instruments = p.instruments ? JSON.stringify(p.instruments) : "";
      const props = {
        h3: p.h3 ?? "",
        n: p.n,
        frp: p.frp_sum ?? 0,
        t,
        platforms,
        instruments,
        win0: this.densWin?.[0] ?? "",
        win1: this.densWin?.[1] ?? "",
        dens: 1,
      };
      const b = p.boundary;
      if (b && b.length >= 3) {
        features.push({
          type: "Feature",
          properties: props,
          geometry: { type: "Polygon", coordinates: [ring(b)] },
        });
      } else {
        features.push({
          type: "Feature",
          properties: props,
          geometry: { type: "Point", coordinates: [p.lon, p.lat] },
        });
      }
    }
    this.src("hexes")?.setData({ type: "FeatureCollection", features });
    this.src("fires-heat")?.setData(EMPTY);
  }

  private densHtml(p: Record<string, unknown>): string {
    let plat = "";
    try {
      const o = JSON.parse(String(p.platforms || "{}")) as Record<string, number>;
      plat = Object.entries(o).map(([k, v]) => `${esc(k)} ${fmt(v)}`).join(" · ");
    } catch { /* ignore */ }
    let inst = "";
    try {
      const o = JSON.parse(String(p.instruments || "{}")) as Record<string, number>;
      inst = Object.entries(o).map(([k, v]) => `${esc(k)} ${fmt(v)}`).join(" · ");
    } catch { /* ignore */ }
    const win = p.win0 && p.win1 ? `${esc(p.win0)} → ${esc(p.win1)} UTC` : "";
    return `<b>${esc(p.h3)}</b><br>`
      + `Tespit (detections): <b>${fmt(Number(p.n))}</b><br>`
      + (inst ? `${inst}<br>` : "")
      + (plat ? `Platform: ${plat}<br>` : "")
      + `FRP sum: ${fmt(Number(p.frp), 1)} MW<br>`
      + (win ? `${win}<br>` : "")
      + `<small>FIRMS hotspot detections — not fire events / burned area</small>`;
  }

  private renderFires() {
    if (!this.ready || this.dead) return;
    const features: Feature[] = new Array(this.fires.length);
    const heatFeatures: Feature[] = new Array(this.fires.length);
    const frpMode = this.liveStyle === "heatmap_frp";
    for (let i = 0; i < this.fires.length; i++) {
      const p = this.fires[i];
      const geom = { type: "Point" as const, coordinates: [p.longitude, p.latitude] as [number, number] };
      features[i] = {
        type: "Feature",
        properties: {
          conf: p.confidenceCode, label: p.confidenceLabel, inst: p.instrument, sat: p.satellite,
          at: p.acquiredAt, temp: p.temperatureCelsius, frp: p.frp,
        },
        geometry: geom,
      };
      const frp = Number.isFinite(p.frp) ? Math.max(0, p.frp) : 0;
      // Equal weight per detection by default; FRP mode is explicitly labeled in the UI.
      const w = frpMode
        ? Math.min(1, 0.35 + Math.log1p(frp) / Math.log1p(80))
        : 1;
      heatFeatures[i] = {
        type: "Feature",
        properties: { w, frp, inst: p.instrument, sat: p.satellite },
        geometry: geom,
      };
    }
    this.src("fires")?.setData({ type: "FeatureCollection", features });
    if (this.live) {
      this.src("fires-heat")?.setData({ type: "FeatureCollection", features: heatFeatures });
    }
  }

  private refresh() {
    if (!this.ready || this.dead) return;
    this.applyMode();
    this.renderFires();
    this.render();
    this.renderBox();
    this.renderSelected();
    this.renderRoi();
    this.renderDraw();
  }

  private src(id: string): mapboxgl.GeoJSONSource | undefined {
    return this.map.getSource(id) as mapboxgl.GeoJSONSource | undefined;
  }

  // ------------------------------------------------------------------ durum
  /** Tema / harita stili değişimi: stil değişirse setStyle (style.load katmanları kurar), değişmezse yalnız renkler yenilenir. */
  setAppearance(styleId: string) {
    if (styleId === this.styleId && !this.ready) return;
    if (MAPBOX_TOKEN && styleId !== this.styleId) {
      this.styleId = styleId;
      this.ready = false;
      this.map.setStyle(mapStyle(styleId));
      return;
    }
    if (!this.ready) return;
    for (const id of ALL_LAYERS) {
      if (this.map.getLayer(id)) this.map.removeLayer(id);
    }
    for (const id of ALL_SOURCES) {
      if (this.map.getSource(id)) this.map.removeSource(id);
    }
    this.ready = false;
    this.setup();
  }

  setSensor(sensor: Sensor) {
    this.sensor = sensor;
    this.render();
  }

  invalidate() {
    window.setTimeout(() => {
      if (!this.dead) this.map.resize();
    }, 40);
  }

  setCells(cells: HexCell[]) {
    this.cells = cells;
    this.render();
  }

  showRegionBox(bbox: BBox | null) {
    this.box = bbox;
    this.renderBox();
  }

  /** BBox = [south, west, north, east] — ROI'ye fitBounds ile gider. */
  fitBBox(bbox: BBox) {
    const [south, west, north, east] = bbox;
    const pad = 0.12;
    try {
      this.map.fitBounds(
        [
          [west - pad, south - pad],
          [east + pad, north + pad],
        ],
        { padding: 48, maxZoom: 10, duration: 650 },
      );
    } catch {
      this.map.jumpTo({
        center: [(west + east) / 2, (south + north) / 2],
        zoom: 6,
      });
    }
  }

  fitWorld() {
    this.map.jumpTo({ center: [10, 22], zoom: 1.4 });
  }

  // ------------------------------------------------------------------ çizim
  private value(c: HexCell): number {
    return this.sensor === "MODIS" ? c.raw_modis : this.sensor === "VIIRS" ? c.raw_viirs : c.raw_total;
  }

  private scaleMax(): number {
    const vals = this.cells.map((c) => this.value(c)).filter((x) => x > 0).sort((x, y) => x - y);
    if (!vals.length) return 1;
    return Math.max(vals[Math.floor(0.98 * (vals.length - 1))], 1);
  }

  private tipHtml(p: Record<string, unknown>): string {
    if (p.dens) {
      return `${esc(p.h3)}<br>${fmt(Number(p.n))} detections`
        + (p.frp != null ? ` · FRP Σ ${fmt(Number(p.frp), 1)} MW` : "");
    }
    return `${String(p.h3)}<br>MODIS ${fmt(Number(p.modis))} &middot; VIIRS ${fmt(Number(p.viirs))}`;
  }

  private render() {
    if (!this.ready || this.dead) return;
    if (this.archiveHeat) {
      this.renderArchiveDensity();
      return;
    }
    const vmax = this.scaleMax();
    const features: Feature[] = [];
    for (const c of this.cells) {
      const val = this.value(c);
      if (val <= 0) continue;
      const props = { h3: c.h3, t: Math.min(1, Math.sqrt(val / vmax)), modis: c.raw_modis, viirs: c.raw_viirs };
      const b = c.boundary;
      const lat = b.reduce((s, p) => s + p[0], 0) / b.length;
      const lng = b.reduce((s, p) => s + p[1], 0) / b.length;
      features.push({ type: "Feature", properties: props, geometry: { type: "Polygon", coordinates: [ring(b)] } });
      features.push({ type: "Feature", properties: props, geometry: { type: "Point", coordinates: [lng, lat] } });
    }
    this.src("hexes")?.setData({ type: "FeatureCollection", features });
    this.opt.onLegend({ vmax, sensor: this.sensor });
    this.renderSelected();
  }

  private renderBox() {
    const b = this.box;
    this.src("box")?.setData(
      b
        ? { type: "FeatureCollection", features: [{ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: ring([[b[0], b[1]], [b[0], b[3]], [b[2], b[3]], [b[2], b[1]]]) } }] }
        : EMPTY,
    );
  }

  select(id: string | null) {
    this.selected = id;
    this.renderSelected();
  }

  private renderSelected() {
    const c = this.selected ? this.cells.find((x) => x.h3 === this.selected) : undefined;
    if (!c) return void this.src("sel")?.setData(EMPTY);
    const b = c.boundary;
    const lat = b.reduce((s, p) => s + p[0], 0) / b.length;
    const lng = b.reduce((s, p) => s + p[1], 0) / b.length;
    this.src("sel")?.setData({
      type: "FeatureCollection",
      features: [
        { type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [ring(b)] } },
        { type: "Feature", properties: {}, geometry: { type: "Point", coordinates: [lng, lat] } },
      ],
    });
  }

  // ------------------------------------------------------------------ ROI
  startRoi() {
    this.clearRoi();
    this.drawing = true;
    this.points = [];
    this.opt.onDrawing(true);
    this.map.doubleClickZoom.disable();
    this.map.getCanvas().style.cursor = "crosshair";
  }

  private mapClick(e: mapboxgl.MapMouseEvent) {
    if (!this.drawing) return;
    this.points.push([e.lngLat.lat, e.lngLat.lng]);
    this.renderDraw();
  }

  private renderDraw() {
    const feats: Feature[] = this.points.map(([la, lo]) => ({
      type: "Feature", properties: {}, geometry: { type: "Point", coordinates: [lo, la] },
    }));
    if (this.points.length > 1) {
      feats.push({ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: this.points.map(([la, lo]) => [lo, la]) } });
    }
    this.src("draw")?.setData({ type: "FeatureCollection", features: feats });
  }

  /** Çizimi bitirir; >= 3 ayrık köşe varsa true. */
  finishRoi(): boolean {
    if (!this.drawing) return false;
    const pts: LatLng[] = [];
    for (const p of this.points) {
      const q = pts[pts.length - 1];
      if (!q || Math.abs(q[0] - p[0]) > 1e-6 || Math.abs(q[1] - p[1]) > 1e-6) pts.push(p);
    }
    this.endDrawing();
    if (pts.length < 3) return false;
    this.setRoiPolygon(pts, { fit: true });
    this.opt.onRoi(pts);
    return true;
  }

  private endDrawing() {
    this.drawing = false;
    this.points = [];
    this.opt.onDrawing(false);
    this.renderDraw();
    this.map.doubleClickZoom.enable();
    this.map.getCanvas().style.cursor = "";
  }

  clearRoi() {
    if (this.drawing) this.endDrawing();
    this.roi = null;
    this.renderRoi();
  }

  setRoiPolygon(pts: LatLng[], opts?: { fit?: boolean }) {
    this.roi = pts;
    this.renderRoi();
    if (opts?.fit && pts.length >= 3) {
      const lats = pts.map((p) => p[0]);
      const lons = pts.map((p) => p[1]);
      const south = Math.min(...lats);
      const north = Math.max(...lats);
      const west = Math.min(...lons);
      const east = Math.max(...lons);
      const pad = 0.15;
      try {
        this.map.fitBounds(
          [
            [west - pad, south - pad],
            [east + pad, north + pad],
          ],
          { padding: 48, maxZoom: 9, duration: 600 },
        );
      } catch {
        this.fitBBox([south, west, north, east]);
      }
    }
  }

  private renderRoi() {
    const p = this.roi;
    this.src("roi")?.setData(
      p ? { type: "FeatureCollection", features: [{ type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [ring(p)] } }] } : EMPTY,
    );
  }
}
