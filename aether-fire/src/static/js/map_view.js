/* Mission-control map: two synchronised Leaflet maps (raw | harmonized), H3 hexagon layers,
   hand-written ROI polygon drawing.  No external plugins required. */

const L = window.L;

const RAMP = [ // light -> dark so dense areas stand out on a white basemap
  [0.00, [255, 244, 196]],
  [0.25, [253, 205, 110]],
  [0.50, [245, 135, 52]],
  [0.75, [214, 50, 42]],
  [1.00, [110, 18, 52]],
];

export function rampColor(t) {
  t = Math.max(0, Math.min(1, t));
  for (let i = 1; i < RAMP.length; i++) {
    if (t <= RAMP[i][0]) {
      const [t0, c0] = RAMP[i - 1], [t1, c1] = RAMP[i];
      const u = (t - t0) / (t1 - t0);
      const c = c0.map((v, k) => Math.round(v + (c1[k] - v) * u));
      return `rgb(${c[0]},${c[1]},${c[2]})`;
    }
  }
  return "rgb(110,18,52)";
}

export function rampGradientCss() {
  return "linear-gradient(90deg," + RAMP.map(([t, c]) => `rgb(${c.join(",")}) ${t * 100}%`).join(",") + ")";
}

const TILE_URL = "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png";

function fmt(v, d = 1) {
  if (v === null || v === undefined || Number.isNaN(v)) return "-";
  return Number(v).toLocaleString("en-US", { maximumFractionDigits: d, minimumFractionDigits: 0 });
}

export class MapView {
  constructor({ onHexClick, onRoi, onBasemapStatus }) {
    this.onHexClick = onHexClick || (() => {});
    this.onRoi = onRoi || (() => {});
    this.onBasemapStatus = onBasemapStatus || (() => {});
    this.mode = "split";
    this.metric = "count";
    this.cells = [];
    this.selected = null;
    this.drawing = false;
    this.points = [];
    this.roiLayers = [];
    this.tileErrors = 0;
    this._sync = false;

    this.maps = {
      a: this._makeMap("map-a"),
      b: this._makeMap("map-b"),
    };
    this.layers = { a: L.layerGroup().addTo(this.maps.a), b: L.layerGroup().addTo(this.maps.b) };
    this.selLayers = { a: L.layerGroup().addTo(this.maps.a), b: L.layerGroup().addTo(this.maps.b) };
    this.drawLayers = { a: L.layerGroup().addTo(this.maps.a), b: L.layerGroup().addTo(this.maps.b) };
    this.regionBox = { a: null, b: null };
    this.tip = { a: L.tooltip({ sticky: true, direction: "top", opacity: 0.95 }), b: L.tooltip({ sticky: true, direction: "top", opacity: 0.95 }) };

    for (const k of ["a", "b"]) {
      const other = k === "a" ? "b" : "a";
      this.maps[k].on("move", () => {
        if (this._sync || this.mode !== "split") return;
        this._sync = true;
        this.maps[other].setView(this.maps[k].getCenter(), this.maps[k].getZoom(), { animate: false });
        this._sync = false;
      });
      this.maps[k].on("click", (e) => this._mapClick(e));
      this.maps[k].on("dblclick", () => this.finishRoi());
    }
    this.maps.a.setView([20, 0], 2);
    this.maps.b.setView([20, 0], 2);
    this.setMode("split");
  }

  _makeMap(id) {
    const map = L.map(id, { preferCanvas: true, minZoom: 2, maxZoom: 14, zoomSnap: 0.5, worldCopyJump: false, attributionControl: true });
    const tiles = L.tileLayer(TILE_URL, {
      subdomains: "abcd", maxZoom: 14, attribution: "&copy; OpenStreetMap, &copy; CARTO",
    });
    tiles.on("tileerror", () => {
      this.tileErrors += 1;
      if (this.tileErrors === 6) this.onBasemapStatus(false);
    });
    tiles.on("tileload", () => {
      if (this.tileErrors < 6) this.onBasemapStatus(true);
    });
    tiles.addTo(map);
    return map;
  }

  // ---------------------------------------------------------------- state
  setMode(mode) {
    this.mode = mode;
    const wrap = document.getElementById("map-wrap");
    wrap.classList.toggle("single", mode !== "split");
    const label = document.getElementById("label-a");
    label.textContent = mode === "harmonized" ? "HARMONIZED" : "RAW";
    label.className = "map-label" + (mode === "harmonized" ? " harm" : "");
    setTimeout(() => {
      this.maps.a.invalidateSize();
      this.maps.b.invalidateSize();
      if (mode === "split") this.maps.b.setView(this.maps.a.getCenter(), this.maps.a.getZoom(), { animate: false });
    }, 30);
    this.render();
  }

  setMetric(metric) {
    this.metric = metric;
    this.render();
  }

  invalidate() {
    setTimeout(() => { this.maps.a.invalidateSize(); this.maps.b.invalidateSize(); }, 30);
  }

  setCells(cells) {
    this.cells = cells || [];
    this.render();
  }

  showRegionBox(bbox) {
    for (const k of ["a", "b"]) {
      if (this.regionBox[k]) this.maps[k].removeLayer(this.regionBox[k]);
      this.regionBox[k] = bbox
        ? L.rectangle([[bbox[0], bbox[1]], [bbox[2], bbox[3]]], { color: "#5d6a7a", weight: 1, dashArray: "6 6", fill: false, interactive: false }).addTo(this.maps[k])
        : null;
    }
  }

  fitBBox(bbox) {
    this.maps.a.fitBounds([[bbox[0], bbox[1]], [bbox[2], bbox[3]]], { animate: false, padding: [20, 20] });
    if (this.mode === "split") this.maps.b.setView(this.maps.a.getCenter(), this.maps.a.getZoom(), { animate: false });
  }

  fitWorld() {
    this.maps.a.setView([22, 10], 2, { animate: false });
    this.maps.b.setView([22, 10], 2, { animate: false });
  }

  fitCells() {
    if (!this.cells.length) return;
    let s = 90, w = 180, n = -90, e = -180;
    for (const c of this.cells) for (const [la, lo] of c.boundary) { s = Math.min(s, la); n = Math.max(n, la); w = Math.min(w, lo); e = Math.max(e, lo); }
    this.fitBBox([s, w, n, e]);
  }

  // ---------------------------------------------------------------- rendering
  _value(c, which) {
    if (this.metric === "count") return which === "raw" ? c.raw_total : c.harmonized;
    return which === "raw" ? c.frp_raw_mean : c.frp_harm_mean;
  }

  _scaleMax(whiches) {
    const vals = [];
    for (const c of this.cells) for (const w of whiches) { const v = this._value(c, w); if (v > 0) vals.push(v); }
    if (!vals.length) return 1;
    vals.sort((x, y) => x - y);
    return Math.max(vals[Math.floor(0.98 * (vals.length - 1))], 1e-6);
  }

  render() {
    const sides = this.mode === "split" ? [["a", "raw"], ["b", "harmonized"]] : [["a", this.mode === "harmonized" ? "harmonized" : "raw"]];
    const vmax = this._scaleMax(sides.map(([, w]) => w));
    this.vmax = vmax;
    for (const k of ["a", "b"]) this.layers[k].clearLayers();
    for (const [k, which] of sides) {
      const map = this.maps[k];
      const renderer = L.canvas({ padding: 0.5 });
      const group = this.layers[k];
      for (const c of this.cells) {
        const v = this._value(c, which);
        const has = v > 0;
        const poly = L.polygon(c.boundary, {
          renderer,
          stroke: true,
          weight: c.static ? 2 : 0.6,
          color: c.static ? "#0b8fa3" : "rgba(30,40,55,.22)",
          dashArray: c.static ? "4 3" : null,
          fillColor: has ? rampColor(Math.sqrt(v / vmax)) : "#fff",
          fillOpacity: has ? 0.78 : 0.0,
          bubblingMouseEvents: true,
        });
        poly.on("mouseover", (e) => {
          const t = this.tip[k];
          t.setLatLng(e.latlng).setContent(this._tipHtml(c)).addTo(map);
        });
        poly.on("mouseout", () => map.closeTooltip(this.tip[k]));
        poly.on("click", () => { if (!this.drawing) this.onHexClick(c.h3); });
        group.addLayer(poly);
      }
    }
    this._renderLegend(sides.map(([, w]) => w), vmax);
    this._renderSelected();
  }

  _tipHtml(c) {
    const ci = c.hi90 - c.lo90 > 1e-6 ? ` [${fmt(c.lo90)}-${fmt(c.hi90)}]` : "";
    if (this.metric === "count") {
      return `${c.h3}<br>raw ${fmt(c.raw_total, 0)} &middot; harmonized ${fmt(c.harmonized)}${ci}` +
        (c.static ? "<br><b style='color:#0b8fa3'>persistent source</b>" : "");
    }
    return `${c.h3}<br>FRP raw ${fmt(c.frp_raw_mean)} MW &middot; harmonized ${fmt(c.frp_harm_mean)} MW` +
      (c.static ? "<br><b style='color:#0b8fa3'>persistent source</b>" : "");
  }

  _renderLegend(whiches, vmax) {
    const unit = this.metric === "count" ? "hotspots / hex" : "mean FRP (MW)";
    const shared = whiches.length > 1 ? "shared scale" : "scale";
    document.getElementById("legend").innerHTML =
      `<div>${unit} &middot; ${shared} (sqrt)</div>` +
      `<div class="bar" style="background:${rampGradientCss()}"></div>` +
      `<div class="ticks"><span>0</span><span>${fmt(vmax * 0.25)}</span><span>${fmt(vmax)}+</span></div>` +
      `<div style="margin-top:5px"><span class="sw"></span>persistent source (removed)</div>`;
  }

  select(cellId) {
    this.selected = cellId;
    this._renderSelected();
  }

  _renderSelected() {
    for (const k of ["a", "b"]) this.selLayers[k].clearLayers();
    if (!this.selected) return;
    const c = this.cells.find((x) => x.h3 === this.selected);
    if (!c) return;
    for (const k of ["a", "b"]) {
      L.polygon(c.boundary, { color: "#111827", weight: 3, fill: false, interactive: false }).addTo(this.selLayers[k]);
    }
  }

  // ---------------------------------------------------------------- ROI drawing
  startRoi() {
    this.clearRoi();
    this.drawing = true;
    this.points = [];
    document.getElementById("map-wrap").classList.add("drawing");
    document.body.classList.add("drawing");
    for (const k of ["a", "b"]) this.maps[k].doubleClickZoom.disable();
  }

  _mapClick(e) {
    if (!this.drawing) return;
    this.points.push([e.latlng.lat, e.latlng.lng]);
    this._drawPreview();
  }

  _drawPreview() {
    for (const k of ["a", "b"]) {
      this.drawLayers[k].clearLayers();
      if (this.points.length > 1) L.polyline(this.points, { color: "#1d4ed8", weight: 2, interactive: false }).addTo(this.drawLayers[k]);
      for (const p of this.points) L.circleMarker(p, { radius: 4, color: "#1d4ed8", fillColor: "#ffffff", fillOpacity: 1, weight: 2, interactive: false }).addTo(this.drawLayers[k]);
    }
  }

  finishRoi() {
    if (!this.drawing) return false;
    // drop duplicate vertices produced by the click pair of a double-click
    const pts = [];
    for (const p of this.points) {
      const q = pts[pts.length - 1];
      if (!q || Math.abs(q[0] - p[0]) > 1e-6 || Math.abs(q[1] - p[1]) > 1e-6) pts.push(p);
    }
    this.drawing = false;
    document.getElementById("map-wrap").classList.remove("drawing");
    document.body.classList.remove("drawing");
    for (const k of ["a", "b"]) { this.drawLayers[k].clearLayers(); this.maps[k].doubleClickZoom.enable(); }
    this.points = [];
    if (pts.length < 3) return false;
    for (const k of ["a", "b"]) {
      this.roiLayers.push(L.polygon(pts, { color: "#1d4ed8", weight: 2, dashArray: "6 4", fillColor: "#1d4ed8", fillOpacity: 0.06, interactive: false }).addTo(this.maps[k]));
    }
    this.onRoi(pts);
    return true;
  }

  clearRoi() {
    this.drawing = false;
    this.points = [];
    document.getElementById("map-wrap").classList.remove("drawing");
    document.body.classList.remove("drawing");
    for (const k of ["a", "b"]) { this.drawLayers[k].clearLayers(); this.maps[k].doubleClickZoom.enable(); }
    for (const l of this.roiLayers) l.remove();
    this.roiLayers = [];
  }

  setRoiPolygon(pts) {
    this.clearRoi();
    for (const k of ["a", "b"]) {
      this.roiLayers.push(L.polygon(pts, { color: "#1d4ed8", weight: 2, dashArray: "6 4", fillColor: "#1d4ed8", fillOpacity: 0.06, interactive: false }).addTo(this.maps[k]));
    }
  }
}
