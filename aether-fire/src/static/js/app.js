/* AETHER-FIRE Mission Control - application controller. */
import { MapView } from "./map_view.js";
import * as charts from "./charts.js";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (v, d = 0) => (v === null || v === undefined || Number.isNaN(v) ? "-" : Number(v).toLocaleString("en-US", { maximumFractionDigits: d, minimumFractionDigits: 0 }));
const fmtP = (p) => (p === null || p === undefined ? "-" : p < 0.001 ? p.toExponential(1) : p.toFixed(3));

const S = {
  source: "benchmark", region: "california", start: null, end: null, days: 1,
  view: "split", metric: "count", regions: [], model: null, validation: null,
  roiPoly: null, hexPayload: null, selectedHex: null, loaded: {}, token: 0, firstFit: true,
  briefing: null, breakFreq: "monthly", calSeries: "harmonized", health: null,
};

const REGION_PEAK = { // [month, day] start/end of the typical peak fire window
  california: [[8, 10], [8, 25]], anatolia: [[7, 20], [8, 5]], amazon: [[8, 20], [9, 5]], australia: [[1, 5], [1, 20]],
};
const pad = (n) => String(n).padStart(2, "0");
function presetsFor(rid) {
  const [a, b] = REGION_PEAK[rid] || [[8, 1], [8, 15]];
  const mk = (y, label) => [label, `${y}-${pad(a[0])}-${pad(a[1])}`, `${y}-${pad(b[0])}-${pad(b[1])}`];
  return [
    ["Last 14 days of archive", "2026-11-01", "2026-11-14"],
    mk(2025, "Peak window 2025 (VIIRS era)"),
    mk(2020, "Peak window 2020 (drifted Terra/Aqua)"),
    mk(2012, "Peak window 2012 (sensor transition year)"),
    mk(2005, "Peak window 2005 (MODIS only, no VIIRS)"),
    mk(2002, "Peak window 2002 (Terra only before Aqua matured)"),
  ];
}

// ------------------------------------------------------------------ api
async function api(path, opts) {
  let res;
  try {
    res = await fetch(path, opts);
  } catch (e) {
    throw new Error("network error: " + e.message);
  }
  if (!res.ok) {
    let d = res.statusText;
    try { const j = await res.json(); d = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch (_) { /* ignore */ }
    throw new Error(`${res.status}: ${d}`);
  }
  return res.json();
}
const post = (p, body) => api(p, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

function banner(msg, kind = "warn") {
  const b = $("banner");
  if (!msg) { b.classList.add("hidden"); return; }
  b.textContent = msg;
  b.className = "banner" + (kind === "info" ? " info" : "");
}

function setPill(id, text, cls) {
  const el = $(id);
  el.textContent = text;
  el.className = "pill" + (cls ? " " + cls : "");
}

// ------------------------------------------------------------------ boot
const map = new MapView({
  onHexClick: (id) => showHex(id),
  onRoi: (pts) => runRoi(pts),
  onBasemapStatus: (ok) => {
    if (!ok) banner("Basemap tiles are unreachable (offline). Hexagon layers still render on a plain light background.", "info");
  },
});

async function boot() {
  setPill("pill-engine", "engine: loading", "warn");
  let h;
  for (let i = 0; i < 240; i++) {
    try { h = await api("/api/health"); } catch (_) { h = null; }
    if (h && h.ready) break;
    if (h && h.status === "error") { setPill("pill-engine", "engine: error", "bad"); banner("Engine failed to start: " + JSON.stringify(h.notes)); return; }
    await new Promise((r) => setTimeout(r, 1500));
  }
  if (!h || !h.ready) { setPill("pill-engine", "engine: unavailable", "bad"); return; }
  S.health = h;
  setPill("pill-engine", `engine: ready (${fmt(h.detections / 1e6, 2)}M detections)`, "ok");
  setPill("pill-model", `model: ${h.model}`, "ok");

  S.regions = await api("/api/regions");
  for (const id of ["region", "break-region", "cal-region"]) {
    const el = $(id);
    el.innerHTML = (id === "region" ? "" : '<option value="all">All regions</option>') +
      S.regions.map((r) => `<option value="${r.id}">${esc(r.name)}</option>`).join("");
  }
  $("region").value = S.region;
  $("break-region").value = "all";
  $("cal-region").value = "california";
  fillPresets();
  applyPreset(0);
  wire();
  await loadMap(true);
}

function fillPresets() {
  const ps = presetsFor(S.region);
  $("preset").innerHTML = ps.map((p, i) => `<option value="${i}">${esc(p[0])}</option>`).join("");
}
function applyPreset(i) {
  const p = presetsFor(S.region)[i];
  S.start = p[1]; S.end = p[2];
  $("start").value = S.start; $("end").value = S.end;
  $("preset").value = String(i);
}

// ------------------------------------------------------------------ wiring
function seg(id, cb) {
  $(id).addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    for (const x of $(id).querySelectorAll("button")) x.classList.toggle("on", x === b);
    cb(b.dataset.v);
  });
}

function wire() {
  seg("seg-source", (v) => setSource(v));
  seg("seg-view", (v) => { S.view = v; map.setMode(v); });
  seg("seg-metric", (v) => { S.metric = v; map.setMetric(v); });
  seg("seg-days", (v) => { S.days = Number(v); loadMap(true); });
  seg("seg-freq", (v) => { S.breakFreq = v; loadBreak(); });
  seg("seg-calseries", (v) => { S.calSeries = v; loadCalendar(); });

  $("region").addEventListener("change", () => { S.region = $("region").value; fillPresets(); applyPreset(0); clearRoiUi(); S.firstFit = true; loadMap(true); });
  $("preset").addEventListener("change", () => { applyPreset(Number($("preset").value)); loadMap(false); });
  $("btn-apply").addEventListener("click", () => {
    const s = $("start").value, e = $("end").value;
    if (!s || !e) return banner("Select both a start and an end date.");
    if (s > e) return banner("Start date must not be after the end date.");
    S.start = s; S.end = e; banner(null); loadMap(false);
  });
  $("btn-live-refresh").addEventListener("click", () => loadMap(true, true));

  $("btn-roi-draw").addEventListener("click", () => {
    switchTab("map");
    map.startRoi();
    $("btn-roi-finish").classList.remove("hidden");
    $("roi-hint").textContent = "Drawing: click to add vertices, double-click or press Finish.";
  });
  $("btn-roi-finish").addEventListener("click", () => { if (!map.finishRoi()) $("roi-hint").textContent = "A polygon needs at least 3 distinct vertices."; $("btn-roi-finish").classList.add("hidden"); });
  $("btn-roi-clear").addEventListener("click", () => { clearRoiUi(); resetDetail(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && map.drawing) { map.clearRoi(); clearRoiUi(); } });
  map.maps.a.on("dblclick", () => $("btn-roi-finish").classList.add("hidden"));
  map.maps.b.on("dblclick", () => $("btn-roi-finish").classList.add("hidden"));

  $("tabs").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) switchTab(b.dataset.tab); });
  $("break-region").addEventListener("change", loadBreak);
  $("cal-region").addEventListener("change", loadCalendar);

  $("btn-brief").addEventListener("click", () => { switchTab("briefing"); runBriefing(); });
  $("btn-brief2").addEventListener("click", runBriefing);
  $("btn-brief-copy").addEventListener("click", () => { if (S.briefing) navigator.clipboard.writeText(S.briefing.markdown); });
  $("btn-brief-json").addEventListener("click", () => {
    if (!S.briefing) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(S.briefing.json, null, 2)], { type: "application/json" }));
    const a = Object.assign(document.createElement("a"), { href: url, download: "aether-fire-briefing.json" });
    a.click(); URL.revokeObjectURL(url);
  });
  $("hz-form").addEventListener("submit", (e) => { e.preventDefault(); runHarmonize(); });
  window.addEventListener("resize", () => { if (S.loaded.calendar && $("tab-calendar").classList.contains("on")) renderCalendarOnly(); });
}

function clearRoiUi() {
  map.clearRoi(); S.roiPoly = null;
  $("btn-roi-finish").classList.add("hidden");
  $("roi-hint").textContent = "Click the map to add vertices, double-click to close.";
}

function setSource(v) {
  S.source = v;
  $("panel-bench").classList.toggle("hidden", v !== "benchmark");
  $("panel-live").classList.toggle("hidden", v !== "live");
  $("source-hint").textContent = v === "benchmark"
    ? "Physically simulated archive with a known ground truth. Not NASA observations."
    : "NASA FIRMS near-real-time MODIS + VIIRS. Falls back to cache / bundled snapshot when offline - always labelled.";
  clearRoiUi(); resetDetail();
  S.firstFit = true;
  loadMap(true);
}

function switchTab(t) {
  for (const b of $("tabs").querySelectorAll("button")) b.classList.toggle("on", b.dataset.tab === t);
  for (const el of document.querySelectorAll(".tab")) el.classList.toggle("on", el.id === "tab-" + t);
  if (t === "map") map.invalidate();
  if (t === "break" && !S.loaded.break) loadBreak();
  if (t === "calendar" && !S.loaded.calendar) loadCalendar();
  if (t === "explain" && !S.loaded.explain) loadExplain();
  if (t === "model" && !S.loaded.model) loadModel();
}

// ------------------------------------------------------------------ map tab
async function loadMap(fit, force = false) {
  const tok = ++S.token;
  $("map-loading").classList.remove("hidden");
  try {
    let p;
    if (S.source === "benchmark") {
      p = await api(`/api/hexes?region=${S.region}&start=${S.start}&end=${S.end}`);
    } else {
      p = await api(`/api/live?days=${S.days}${force ? "&refresh=true" : ""}`);
    }
    if (tok !== S.token) return;
    S.hexPayload = p;
    map.setCells(p.cells);
    map.select(null);
    const reg = S.regions.find((r) => r.id === S.region);
    if (S.source === "benchmark") {
      map.showRegionBox(reg ? reg.bbox : null);
      if (fit && reg && S.firstFit) { map.fitBBox(reg.bbox); S.firstFit = false; }
    } else {
      map.showRegionBox(null);
      if (S.firstFit) { map.fitWorld(); S.firstFit = false; }
    }
    renderKpis(p);
    updateDataStatus(p);
    if (!p.cells.length) banner("No detections in this window. Try another preset or region.", "info");
    else if (p.truncated) banner(`Showing the ${fmt(p.cells.length)} busiest hexagons; KPIs cover all ${fmt(p.totals.n_cells)} active hexagons.`, "info");
    else if (S.source === "benchmark") banner(null);
    // if an ROI exists re-run it for the new window
    if (S.roiPoly) runRoi(S.roiPoly, true);
  } catch (e) {
    if (tok === S.token) banner("Could not load map data: " + e.message);
  } finally {
    if (tok === S.token) $("map-loading").classList.add("hidden");
  }
}

function updateDataStatus(p) {
  if (S.source === "benchmark") {
    setPill("pill-data", "data: SIMULATED benchmark", "warn");
    $("live-meta").textContent = "-";
    return;
  }
  const m = p.meta || {};
  const tiers = (m.tiers || []).join(", ") || "none";
  const liveOk = m.live === true;
  setPill("pill-data", liveOk ? `data: LIVE FIRMS (${tiers})` : `data: NOT LIVE (${tiers})`, liveOk ? "ok" : "warn");
  $("live-meta").textContent =
    `tiers: ${tiers}\nfetched: ${m.fetched_at || "-"}\nkey: ${m.key_configured ? "configured" : "not set"}` +
    (m.platforms ? `\nplatforms: MODIS x${m.platforms.MODIS}, VIIRS x${m.platforms.VIIRS}` : "");
  if (m.warning) banner(m.warning);
  else if (!p.truncated) banner(null);
}

function kpi(cls, k, v, s) {
  return `<div class="kpi ${cls}"><div class="k">${k}</div><div class="v">${v}</div><div class="s">${s || ""}</div></div>`;
}

function renderKpis(p) {
  const t = p.totals || {};
  if (!p.cells || !p.cells.length) { $("kpis").innerHTML = kpi("", "No detections", "0", "in this window"); return; }
  const ci = t.harmonized_hi90 - t.harmonized_lo90 > 1e-6
    ? `90% CI ${fmt(t.harmonized_lo90)} - ${fmt(t.harmonized_hi90)}`
    : "VIIRS-native reference (no cross-sensor uncertainty)";
  const ratio = t.harmonized > 0 ? (t.raw_total / t.harmonized).toFixed(2) + "x" : "-";
  $("kpis").innerHTML =
    kpi("raw", "Raw detections", fmt(t.raw_total), `MODIS ${fmt(t.raw_modis)} &middot; VIIRS ${fmt(t.raw_viirs)}`) +
    kpi("fire", "Persistent sources removed", fmt(t.static_removed), `${fmt(t.static_cells)} static hexes`) +
    kpi("", "Low-confidence removed", fmt(t.lowconf_removed), "confidence filter") +
    kpi("harm", "Harmonized hotspots", fmt(t.harmonized, 1), ci) +
    kpi("", "Raw / harmonized", ratio, "overlapping platforms + static + low-conf") +
    kpi("ok", "Active hexagons", fmt(t.n_cells), "H3 res-7 (~5.16 km2)");
}

// ------------------------------------------------------------------ detail panel
function resetDetail() {
  $("detail").innerHTML = `<div class="detail-empty"><h3>Inspector</h3>
    <p>Click a hexagon (H3 res-7, ~5.16 km2) for its raw-vs-harmonized breakdown, or draw a polygon to analyse a custom region of interest.</p>
    <p class="hint">Dashed cyan outlines mark persistent thermal sources removed from the harmonized count.</p></div>`;
  map.select(null); S.selectedHex = null;
}

function bars(items, color = "var(--fire)") {
  const mx = Math.max(1e-9, ...items.map((i) => Math.abs(i.v)));
  return `<div class="bars">${items.map((i) => `<div class="bar-row"><span>${esc(i.label)}</span>
    <span class="track"><span class="fill" style="left:${i.v < 0 ? 50 - 50 * Math.abs(i.v) / mx : 50}%;width:${50 * Math.abs(i.v) / mx}%;background:${i.v < 0 ? "var(--harm)" : color}"></span></span>
    <span class="val">${esc(i.text)}</span></div>`).join("")}</div>`;
}

function driverBars(drv) {
  if (!drv || !drv.frp || !drv.frp.length) return "";
  return `<div class="sect">Why MODIS FRP was rescaled (mean effect, ${drv.n_rows} detections)</div>` +
    bars(drv.frp.map((d) => ({ label: d.driver, v: d.mean_effect_pct, text: `${d.mean_effect_pct > 0 ? "+" : ""}${d.mean_effect_pct.toFixed(0)}%` })));
}

function satTable(bySat) {
  const rows = Object.entries(bySat || {}).map(([s, v]) => {
    const n = typeof v === "number" ? v : v.n;
    const extra = typeof v === "number" ? "" : `<td>${fmt(v.frp_mean, 1)}</td><td>${fmt(v.area_mean, 2)}</td>`;
    return `<tr><td class="l">${esc(s)}</td><td>${fmt(n)}</td>${extra}</tr>`;
  }).join("");
  const has = Object.values(bySat || {}).some((v) => typeof v !== "number");
  return `<table class="tbl"><thead><tr><th class="l">Satellite</th><th>Detections</th>${has ? "<th>Mean FRP</th><th>Footprint km2</th>" : ""}</tr></thead><tbody>${rows}</tbody></table>`;
}

async function showHex(id) {
  S.selectedHex = id; map.select(id);
  $("detail").innerHTML = `<div class="detail-empty"><p>Loading ${esc(id)} ...</p></div>`;
  try {
    const q = S.source === "benchmark"
      ? `source=benchmark&region=${S.region}&start=${S.start}&end=${S.end}`
      : `source=live&days=${S.days}`;
    const d = await api(`/api/hex/${id}?${q}`);
    if (S.selectedHex !== id) return;
    if (d.empty) { $("detail").innerHTML = `<div class="detail-empty"><p>No detections in ${esc(id)} for this window.</p></div>`; return; }
    const t = d.totals;
    const ci = t.hi - t.lo > 1e-6 ? `${fmt(t.lo, 1)} - ${fmt(t.hi, 1)}` : "VIIRS-native (no CI)";
    const dm = d.diurnal || {};
    const det = (d.top_modis_detections || []).map((r, i) => `<tr class="click" data-i="${i}">
      <td class="l">${esc(r.time.slice(5, 16))}</td><td class="l">${esc(r.satellite)}</td><td class="r">${fmt(r.frp_obs, 1)}</td>
      <td>${fmt(r.footprint_km2, 1)}</td><td>${fmt(r.lst, 1)}</td><td class="h">${fmt(r.frp_harm_p50, 1)}</td><td class="h">${fmt(r.n_p50, 1)}</td></tr>
      <tr class="expl hidden" data-e="${i}"><td colspan="7" style="font-family:inherit">${explainRow(r)}</td></tr>`).join("");
    $("detail").innerHTML = `
      <h3>Hexagon <span class="badge">${esc(d.biome)}</span></h3>
      <div class="sub">${esc(d.h3)} &middot; ${d.lat.toFixed(3)}, ${d.lon.toFixed(3)} &middot; ${fmt(d.area_km2, 2)} km2</div>
      <table class="tbl"><tbody>
        <tr><td class="l">Raw detections</td><td class="r">${fmt(t.raw_total)}</td></tr>
        <tr><td class="l">&nbsp;&nbsp;MODIS / VIIRS</td><td>${fmt(t.raw_modis)} / ${fmt(t.raw_viirs)}</td></tr>
        <tr><td class="l">Persistent-source / low-conf removed</td><td>${fmt(t.static_n)} / ${fmt(t.lowconf_n)}</td></tr>
        <tr><td class="l">Harmonized hotspots</td><td class="h">${fmt(t.harmonized, 1)}</td></tr>
        <tr><td class="l">&nbsp;&nbsp;90% CI</td><td>${ci}</td></tr>
        <tr><td class="l">Mean FRP raw / harmonized (MW)</td><td>${fmt(t.frp_raw_mean, 1)} / <span class="h">${fmt(t.frp_harm_mean, 1)}</span></td></tr>
      </tbody></table>
      <div class="sect">By satellite</div>${satTable(d.by_satellite)}
      ${dm.mean_lst !== undefined ? `<div class="sect">Overpass timing</div><p class="hint" style="margin:0">Mean local solar time ${fmt(dm.mean_lst, 1)} h; diurnal curve peaks ${dm.peak_hour} h. FRP is scaled to ${dm.reference_day} h (day) / ${dm.reference_night} h (night).</p>` : ""}
      ${driverBars(d.drivers)}
      ${det ? `<div class="sect">Brightest MODIS detections (click to explain)</div>
        <table class="tbl" id="det-table"><thead><tr><th class="l">UTC</th><th class="l">Sat</th><th>FRP</th><th>km2</th><th>LST</th><th>FRPh</th><th>Eq. n</th></tr></thead><tbody>${det}</tbody></table>` : ""}`;
    const tbl = $("det-table");
    if (tbl) tbl.addEventListener("click", (e) => {
      const tr = e.target.closest("tr.click"); if (!tr) return;
      const row = tbl.querySelector(`tr[data-e="${tr.dataset.i}"]`);
      row.classList.toggle("hidden");
    });
  } catch (e) {
    $("detail").innerHTML = `<div class="detail-empty"><p>${esc(e.message)}</p></div>`;
  }
}

function explainRow(r) {
  const drivers = (r.drivers || []).map((d) => `${esc(d.driver)} ${d.effect_pct > 0 ? "+" : ""}${d.effect_pct.toFixed(0)}%`).join(" &middot; ");
  return `<div class="hint" style="margin:2px 0">
    Pixel ${r.footprint_km2.toFixed(1)} km2 (${r.bowtie_x.toFixed(1)}x nadir) at LST ${r.lst.toFixed(1)} h, diurnal factor ${r.diurnal_factor.toFixed(2)}.<br>
    FRP ${r.frp_obs.toFixed(1)} MW &rarr; harmonized P10/P50/P90 ${r.frp_harm_p10.toFixed(1)} / <b>${r.frp_harm_p50.toFixed(1)}</b> / ${r.frp_harm_p90.toFixed(1)} MW
    (90% CI ${r.frp_lo90.toFixed(1)}-${r.frp_hi90.toFixed(1)}).<br>
    Equivalent VIIRS hotspots ${r.n_p50.toFixed(1)} (${r.n_lo90.toFixed(1)}-${r.n_hi90.toFixed(1)}), scale factor ${r.scale_factor.toFixed(2)}.<br>
    Drivers: ${drivers}</div>`;
}

async function runRoi(pts, silent = false) {
  S.roiPoly = pts;
  $("btn-roi-finish").classList.add("hidden");
  $("roi-hint").textContent = `ROI with ${pts.length} vertices.`;
  if (!silent) $("detail").innerHTML = `<div class="detail-empty"><p>Analysing region of interest ...</p></div>`;
  try {
    const body = { polygon: pts, source: S.source, region: S.source === "benchmark" ? S.region : null, start: S.start, end: S.end, days: S.days };
    const d = await post("/api/roi", body);
    if (d.empty) {
      $("detail").innerHTML = `<h3>Region of interest</h3><div class="sub">${fmt(d.roi_area_km2)} km2 &middot; ${d.n_cells_in_roi} hexes</div><p class="hint">No detections inside the polygon for this window.</p>`;
      return;
    }
    const t = d.totals;
    const ci = t.harmonized_hi90 - t.harmonized_lo90 > 1e-6 ? `${fmt(t.harmonized_lo90, 1)} - ${fmt(t.harmonized_hi90, 1)}` : "VIIRS-native (no CI)";
    const biome = Object.entries(d.by_biome || {}).map(([b, v]) => ({ label: b, v: v.harmonized, text: `${fmt(v.harmonized, 1)} / ${fmt(v.raw)}` }));
    $("detail").innerHTML = `
      <h3>Region of interest</h3>
      <div class="sub">${fmt(d.roi_area_km2)} km2 &middot; ${d.n_cells_in_roi} H3 res-7 hexes &middot; ${d.static_cells} with persistent sources</div>
      <table class="tbl"><tbody>
        <tr><td class="l">Raw detections</td><td class="r">${fmt(t.raw_total)}</td></tr>
        <tr><td class="l">&nbsp;&nbsp;MODIS / VIIRS</td><td>${fmt(t.raw_modis)} / ${fmt(t.raw_viirs)}</td></tr>
        <tr><td class="l">Persistent-source / low-conf removed</td><td>${fmt(t.static_removed)} / ${fmt(t.lowconf_removed)}</td></tr>
        <tr><td class="l">Harmonized hotspots</td><td class="h">${fmt(t.harmonized, 1)}</td></tr>
        <tr><td class="l">&nbsp;&nbsp;90% CI</td><td>${ci}</td></tr>
        <tr><td class="l">Mean FRP raw / harmonized (MW)</td><td>${fmt(t.frp_raw_mean, 1)} / <span class="h">${fmt(t.frp_harm_mean, 1)}</span></td></tr>
      </tbody></table>
      <div class="sect">Biome (harmonized / raw)</div>${bars(biome, "var(--harm)")}
      <div class="sect">By satellite</div>${satTable(d.by_satellite)}
      <div class="sect">Time series</div><div class="mini-chart"><canvas id="roi-chart"></canvas></div>
      ${driverBars(d.drivers)}
      <button class="btn" style="margin-top:10px" id="roi-brief">Briefing for this ROI</button>`;
    charts.renderMiniSeries($("roi-chart"), d.timeseries);
    $("roi-brief").addEventListener("click", () => { switchTab("briefing"); runBriefing(); });
  } catch (e) {
    $("detail").innerHTML = `<div class="detail-empty"><p>${esc(e.message)}</p></div>`;
  }
}

// ------------------------------------------------------------------ structural break tab
async function loadBreak() {
  const rid = $("break-region").value;
  try {
    const [s, b] = await Promise.all([
      api(`/api/series?region=${rid}&freq=${S.breakFreq}`),
      api(`/api/structural-break?region=${rid}`),
    ]);
    S.loaded.break = true;
    charts.renderBreakChart($("chart-break"), s, S.breakFreq, b);
    const names = { raw_all: ["Raw as reported", "r"], raw_filtered: ["Raw, sources removed", "r"], harmonized: ["Harmonized", "h"], truth: ["Ground truth (SNPP)", "g"] };
    const cell = (p, label) => `<td>${fmtP(p)} <span class="badge ${p < 0.05 ? "bad" : "ok"}">${p < 0.05 ? "break" : "none"}</span></td>`;
    const rows = Object.entries(b.reports).map(([k, r]) => `<tr>
      <td class="l ${names[k][1]}">${names[k][0]}</td><td class="${r.step.step_pct > 25 ? "bad" : ""}">${r.step.step_pct > 0 ? "+" : ""}${fmt(r.step.step_pct, 1)}%</td>
      ${cell(r.pettitt_annual.p_value)}${cell(r.chow_annual.p_value)}${cell(r.chow_monthly.p_value)}${cell(r.pettitt_local.p_value)}</tr>`).join("");
    $("break-table").innerHTML = `<table class="tbl"><thead><tr><th class="l">Series</th><th>Step 2012</th><th>Pettitt (annual)</th><th>Chow @2012 (annual)</th><th>Chow @2012 (monthly)</th><th>Pettitt &plusmn;6 y</th></tr></thead><tbody>${rows}</tbody></table>
      <p class="hint">Entries are p-values: "break" means p &lt; 0.05. Chow is evaluated exactly at 2012-01-20; Pettitt searches for the change-point freely.</p>`;
    const v = b.verdict;
    $("break-verdict").innerHTML = `<p class="prose"><span class="badge ${v.raw_break_at_transition ? "bad" : "ok"}">raw: ${v.raw_break_at_transition ? "artificial break at sensor transition" : "no break"}</span>
      <span class="badge ${v.harmonized_step_eliminated ? "ok" : "bad"}">harmonized: ${v.harmonized_step_eliminated ? "step eliminated" : "step remains"}</span></p>
      <ul class="prose" style="padding-left:18px">${v.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>`;
  } catch (e) { banner("Structural-break data failed: " + e.message); }
}

// ------------------------------------------------------------------ calendar tab
let calData = null;
async function loadCalendar() {
  const rid = $("cal-region").value;
  try {
    calData = await api(`/api/calendar?region=${rid}&series=${S.calSeries}`);
    S.loaded.calendar = true;
    renderCalendarOnly();
    charts.renderEnvelope($("chart-env"), calData);
    const eps = calData.anomalies.episodes;
    $("cal-episodes").innerHTML = eps.length
      ? `<table class="tbl"><thead><tr><th class="l">Start</th><th class="l">End</th><th>Days</th><th>Peak z</th></tr></thead><tbody>${eps.map((e) => `<tr><td class="l">${e.start}</td><td class="l">${e.end}</td><td>${e.days}</td><td class="${e.peak_z > 0 ? "r" : "h"}">${e.peak_z > 0 ? "+" : ""}${e.peak_z.toFixed(1)}</td></tr>`).join("")}</tbody></table>
        <p class="hint">${calData.anomalies.n_days} anomalous days (|z| &ge; 2 on log scale, scale floor 0.35) of ${calData.target_year}: ${calData.anomalies.n_positive} above, ${calData.anomalies.n_negative} below the 2001-2025 day-of-year median.</p>`
      : `<p class="hint">No multi-day anomalies in ${calData.target_year}.</p>`;
  } catch (e) { banner("Calendar failed: " + e.message); }
}
function renderCalendarOnly() { if (calData) charts.renderCalendarHeatmap($("cal-heat"), calData, $("cal-hover")); }

// ------------------------------------------------------------------ explainer tab
async function loadExplain() {
  try {
    const [g, m] = await Promise.all([api("/api/geometry"), S.model ? Promise.resolve(S.model) : api("/api/model")]);
    S.model = m; S.loaded.explain = true;
    charts.renderGeometry($("chart-geom"), g, $("geom-note"), $("geom-table"));
    charts.renderDiurnal($("chart-diurnal"), m.diurnal);
    charts.renderDrift($("chart-drift"), m.diurnal);
  } catch (e) { banner("Explainer data failed: " + e.message); }
}

async function runHarmonize() {
  const f = new FormData($("hz-form"));
  const num = (k) => (f.get(k) === "" || f.get(k) === null ? null : Number(f.get(k)));
  const det = {
    latitude: num("latitude"), longitude: num("longitude"), acq_date: f.get("acq_date"), acq_time: num("acq_time"),
    satellite: f.get("satellite"), frp: num("frp"), scan: num("scan"), track: num("track"), confidence: num("confidence"),
  };
  $("hz-result").innerHTML = '<p class="hint">Harmonizing ...</p>';
  try {
    const r = await post("/api/harmonize", { detections: [det] });
    const d = r.detections[0];
    const flags = [d.is_static ? '<span class="badge warn">persistent source - excluded</span>' : "", d.low_confidence ? '<span class="badge warn">low confidence - excluded</span>' : ""].join(" ");
    $("hz-result").innerHTML = `
      <div class="sub" style="color:var(--muted);font:11px var(--mono)">${esc(d.satellite)} &middot; biome ${esc(d.biome)} &middot; H3 r7 ${esc(d.h3_res7)} &middot; r8 ${esc(d.h3_res8)} ${flags}</div>
      <div class="q-strip"><div><b>${fmt(d.frp_p10, 1)}</b><span>FRP P10 (MW)</span></div><div><b>${fmt(d.frp_p50, 1)}</b><span>FRP P50 harmonized</span></div><div><b>${fmt(d.frp_p90, 1)}</b><span>FRP P90 (MW)</span></div></div>
      <div class="q-strip"><div><b>${fmt(d.n_p50, 1)}</b><span>equivalent VIIRS hotspots</span></div><div><b>${fmt(d.n_lo90, 1)} - ${fmt(d.n_hi90, 1)}</b><span>90% CI</span></div><div><b>${fmt(d.footprint_km2, 1)}</b><span>footprint km2</span></div></div>
      <p class="hint">Local solar time ${fmt(d.local_solar_time, 1)} h &middot; diurnal factor ${fmt(d.diurnal_factor, 2)} &middot; diurnally normalized FRP ${fmt(d.frp_diurnal_normalized, 1)} MW &middot; observed ${fmt(d.frp_obs, 1)} MW.</p>
      <div style="position:relative;height:190px"><canvas id="hz-drivers"></canvas></div>`;
    charts.renderDriverChart($("hz-drivers"), d.drivers);
  } catch (e) { $("hz-result").innerHTML = `<p class="hint" style="color:var(--raw)">${esc(e.message)}</p>`; }
}

// ------------------------------------------------------------------ briefing tab
function mdToHtml(md) {
  const inline = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/`(.+?)`/g, "<code>$1</code>").replace(/_(.+?)_/g, "<em>$1</em>");
  const lines = md.split("\n");
  let out = "", i = 0, list = null;
  const close = () => { if (list) { out += `</${list}>`; list = null; } };
  while (i < lines.length) {
    const l = lines[i];
    if (/^\|/.test(l)) {
      close();
      const rows = [];
      while (i < lines.length && /^\|/.test(lines[i])) { rows.push(lines[i]); i++; }
      const cells = (r) => r.replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
      const head = cells(rows[0]), body = rows.slice(2).map(cells);
      out += `<table><thead><tr>${head.map((c) => `<th>${inline(c)}</th>`).join("")}</tr></thead><tbody>${body.map((r) => `<tr>${r.map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
      continue;
    }
    if (/^# /.test(l)) { close(); out += `<h1>${inline(l.slice(2))}</h1>`; }
    else if (/^## /.test(l)) { close(); out += `<h2>${inline(l.slice(3))}</h2>`; }
    else if (/^- /.test(l)) { if (list !== "ul") { close(); out += "<ul>"; list = "ul"; } out += `<li>${inline(l.slice(2))}</li>`; }
    else if (/^\d+\. /.test(l)) { if (list !== "ol") { close(); out += "<ol>"; list = "ol"; } out += `<li>${inline(l.replace(/^\d+\. /, ""))}</li>`; }
    else if (l.trim() === "") close();
    else { close(); out += `<p>${inline(l.trim())}</p>`; }
    i++;
  }
  close();
  return out.replace(/<strong>(EXTREME|ELEVATED|NORMAL|SUPPRESSED|UNRATED)<\/strong>/g, '<span class="lvl $1">$1</span>');
}

async function runBriefing() {
  $("brief-body").innerHTML = '<p class="hint">Generating briefing ...</p>';
  try {
    const b = await post("/api/briefing", {
      source: S.source, region: S.source === "benchmark" ? S.region : null, start: S.start, end: S.end,
      polygon: S.roiPoly, days: S.days,
    });
    S.briefing = b;
    $("brief-body").innerHTML = mdToHtml(b.markdown);
  } catch (e) { $("brief-body").innerHTML = `<p class="hint" style="color:var(--raw)">${esc(e.message)}</p>`; }
}

// ------------------------------------------------------------------ model tab
async function loadModel() {
  try {
    const [m, v, ss] = await Promise.all([S.model ? Promise.resolve(S.model) : api("/api/model"), api("/api/validation"), api("/api/static-sources")]);
    S.model = m; S.validation = v; S.loaded.model = true;
    const f = m.metrics.per_target.frp, n = m.metrics.per_target.n, mt = m.matchups;
    $("model-kpis").innerHTML =
      kpi("harm", "FRP error vs naive linear", `-${fmt(f.mae_improvement_pct, 0)}%`, `MAE(log) ${f.mae_log_model.toFixed(2)} vs ${f.mae_log_naive_linear.toFixed(2)}`) +
      kpi("harm", "Hotspot-count error vs naive", `-${fmt(n.mae_improvement_pct, 0)}%`, `MAE(log) ${n.mae_log_model.toFixed(2)} vs ${n.mae_log_naive_linear.toFixed(2)}`) +
      kpi("ok", "Conformal 90% coverage", `${(f.coverage_90_conformal * 100).toFixed(1)}% / ${(n.coverage_90_conformal * 100).toFixed(1)}%`, "FRP / count on hold-out years") +
      kpi("", "Training matchups", fmt(mt.n_matchups), `train ${mt.train_years.join("-")} &middot; calib ${mt.calib_years.join("-")} &middot; test ${mt.test_years.join("-")}`) +
      kpi("fire", "Aggregation variance factor", `${m.kappa.toFixed(2)}x`, "empirical CI inflation (correlated errors)") +
      kpi("", "MODIS with VIIRS counterpart", `${(mt.frac_modis_with_viirs_counterpart * 100).toFixed(0)}%`, "geometric footprint match");
    charts.renderImportance($("chart-imp"), m.importance.frp);

    const rows = [];
    for (const [rid, r] of Object.entries(v.regions)) {
      for (const [k, lab] of [["holdout_2022_2026", "Hold-out 2022-26"], ["counterfactual_2001_2011", "Counterfactual 2001-11"]]) {
        const x = r[k];
        rows.push(`<tr><td class="l">${esc(rid)}</td><td class="l">${lab}</td><td class="r">${fmt(x.rmse_raw)}</td><td class="h">${fmt(x.rmse_harmonized)}</td>
          <td class="r">${x.relative_bias_raw_pct > 0 ? "+" : ""}${x.relative_bias_raw_pct.toFixed(0)}%</td><td class="h">${x.relative_bias_harmonized_pct > 0 ? "+" : ""}${x.relative_bias_harmonized_pct.toFixed(1)}%</td>
          <td>${x.correlation_harmonized.toFixed(3)}</td><td>${(x.ci90_coverage * 100).toFixed(0)}%</td></tr>`);
      }
    }
    $("val-table").innerHTML = `<table class="tbl"><thead><tr><th class="l">Scope</th><th class="l">Test</th><th>RMSE raw</th><th>RMSE harm.</th><th>Bias raw</th><th>Bias harm.</th><th>r</th><th>CI cov.</th></tr></thead><tbody>${rows.join("")}</tbody></table>
      <p class="hint">Monthly MODIS-derived series vs the VIIRS-native reference. The pre-2012 reference is a hidden counterfactual SNPP record; the 2022-26 reference is real VIIRS. Raw bias is the MODIS-only count relative to VIIRS.</p>`;

    const sf = v.static_filter;
    $("static-table").innerHTML = `<table class="tbl"><thead><tr><th class="l">Region</th><th>Precision</th><th>Recall</th><th>True rows</th><th>False +</th></tr></thead><tbody>${
      Object.entries(sf).filter(([k]) => k !== "catalog_cells").map(([k, x]) => `<tr><td class="l">${esc(k)}</td><td>${(x.precision * 100).toFixed(2)}%</td><td>${(x.recall * 100).toFixed(1)}%</td><td>${fmt(x.true_static_rows)}</td><td>${fmt(x.false_positive_rows)}</td></tr>`).join("")}</tbody></table>
      <p class="hint">Rule: &gt; 90 distinct days/year, monthly CV &le; 0.6, &ge; 9 active months, flagged in &ge; 2 years; footprint-aware dilation for MODIS pixels.</p>`;
    $("static-list").innerHTML = `<table class="tbl"><thead><tr><th class="l">Region</th><th class="l">Source</th><th>Days/yr</th><th>Years</th><th class="l">Kind</th></tr></thead><tbody>${
      ss.detected.map((d) => `<tr><td class="l">${esc(d.region)}</td><td class="l">${esc(d.scenario_source || d.cell)}</td><td>${fmt(d.median_days_per_year)}</td><td>${d.years_flagged}</td><td class="l">${esc(d.kind)}</td></tr>`).join("")}</tbody></table>`;

    $("method-notes").innerHTML = `
      <p><b>Geometry.</b> Pixel footprints come from spherical-Earth slant-range geometry (MODIS 705 km, VIIRS 824 km). Each VIIRS pixel's detection mass is shared among overlapping MODIS footprints by true rectangle intersection, then joined through H3 res-8 neighbours (about 0.74 km2) and aggregated to res-7 (about 5.16 km2).</p>
      <p><b>Diurnal.</b> Local solar time = UTC + longitude/15 + equation of time. FRP is rescaled with a Gaussian diurnal curve (peak 14:30, trough ~04:00) to the SNPP 13:30 / 01:30 reference, including Terra's and Aqua's orbital drift.</p>
      <p><b>Model.</b> One HistGradientBoosting quantile model (P10/P50/P90) per biome and target, with conformal calibration on a disjoint period. Contributions are group-ablation effects.</p>
      <p><b>Honesty.</b> The benchmark is simulated with a known ground truth, so the reported errors are real for this simulator but are not a claim about NASA data. The aggregate monthly Pettitt test may still flag a later change-point (for example 2018); the ground-truth series shows the same, reflecting genuine scripted activity, not a sensor artefact.</p>`;
  } catch (e) { banner("Model data failed: " + e.message); }
}

boot();
