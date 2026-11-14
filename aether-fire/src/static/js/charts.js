/* Chart.js builders + hand-drawn canvas visuals (calendar heatmap, bow-tie footprints). */
import { rampColor } from "./map_view.js";

const Chart = window.Chart;
const C = { raw: "#d6322a", rawSoft: "rgba(214,50,42,.5)", harm: "#0b8fa3", truth: "#1f9d55", fire: "#e8590c", muted: "#5d6a7a", grid: "#e6e9ee", warn: "#b7791f" };

Chart.defaults.color = C.muted;
Chart.defaults.borderColor = C.grid;
Chart.defaults.font.family = 'Inter, "Segoe UI", system-ui, sans-serif';
Chart.defaults.font.size = 11;
Chart.defaults.animation = false;
Chart.defaults.maintainAspectRatio = false;

function fresh(canvas) {
  const old = Chart.getChart(canvas);
  if (old) old.destroy();
  return canvas;
}

const fmt = (v, d = 0) => (v === null || v === undefined || Number.isNaN(v) ? "-" : Number(v).toLocaleString("en-US", { maximumFractionDigits: d }));

/* vertical annotation lines */
const vlines = {
  id: "vlines",
  afterDatasetsDraw(chart, _args, opts) {
    const { ctx, chartArea, scales } = chart;
    if (!opts || !opts.lines) return;
    ctx.save();
    for (const ln of opts.lines) {
      const x = scales.x.getPixelForValue(ln.x);
      if (!Number.isFinite(x) || x < chartArea.left || x > chartArea.right) continue;
      ctx.strokeStyle = ln.color;
      ctx.lineWidth = ln.width || 1;
      ctx.setLineDash(ln.dash || [5, 4]);
      ctx.beginPath(); ctx.moveTo(x, chartArea.top); ctx.lineTo(x, chartArea.bottom); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = ln.color;
      ctx.font = "11px Inter, sans-serif";
      ctx.textAlign = ln.align || "left";
      ctx.fillText(ln.label, x + (ln.align === "right" ? -5 : 5), chartArea.top + 12 + (ln.row || 0) * 13);
    }
    ctx.restore();
  },
};

// ---------------------------------------------------------------- structural break
export function renderBreakChart(canvas, s, freq, breakPayload) {
  fresh(canvas);
  const labels = s.labels;
  const idx = labels.indexOf(freq === "monthly" ? "2012-01" : "2012");
  const lines = [{ x: idx, color: C.warn, label: "VIIRS 375 m enters service (2012-01-20)", width: 1.5, dash: [], row: 0 }];
  if (freq === "monthly" && breakPayload && breakPayload.reports) {
    const r = breakPayload.reports;
    const add = (rep, color, tag, row) => {
      const d = rep && rep.pettitt_monthly && rep.pettitt_monthly.change_date;
      if (!d) return;
      const i = labels.indexOf(d.slice(0, 7));
      if (i >= 0) lines.push({ x: i, color, label: `Pettitt ${tag} ${d.slice(0, 7)}`, row, align: i > labels.length * 0.75 ? "right" : "left" });
    };
    add(r.raw_filtered || r.raw_all, C.raw, "raw", 1);
    add(r.harmonized, C.harm, "harmonized", 2);
  }
  const ds = [
    { label: "Raw as reported (MODIS+VIIRS)", data: s.raw_all, borderColor: C.raw, backgroundColor: C.raw, borderWidth: 1.6, pointRadius: 0, tension: 0 },
    { label: "Raw, persistent sources removed", data: s.raw_filtered, borderColor: C.rawSoft, borderWidth: 1, borderDash: [3, 3], pointRadius: 0, tension: 0 },
    { label: "_lo", data: s.harmonized_lo, borderWidth: 0, pointRadius: 0, backgroundColor: "rgba(11,143,163,.16)", fill: false },
    { label: "_hi", data: s.harmonized_hi, borderWidth: 0, pointRadius: 0, backgroundColor: "rgba(11,143,163,.16)", fill: "-1" },
    { label: "Harmonized (VIIRS-equivalent) with 90% CI", data: s.harmonized, borderColor: C.harm, backgroundColor: C.harm, borderWidth: 1.8, pointRadius: 0, tension: 0 },
    { label: "Ground truth: SNPP everywhere (hidden)", data: s.truth_snpp_everywhere, borderColor: C.truth, borderWidth: 1.2, borderDash: [6, 3], pointRadius: 0, tension: 0 },
  ];
  new Chart(canvas, {
    type: "line",
    data: { labels, datasets: ds },
    options: {
      interaction: { mode: "index", intersect: false },
      scales: {
        x: { ticks: { maxTicksLimit: 14, autoSkip: true }, grid: { display: false } },
        y: { title: { display: true, text: freq === "monthly" ? "hotspots / month" : "hotspots / year" }, ticks: { callback: (v) => fmt(v) } },
      },
      plugins: {
        legend: { labels: { filter: (i) => !i.text.startsWith("_"), usePointStyle: true, boxWidth: 8 } },
        tooltip: { filter: (i) => !i.dataset.label.startsWith("_"), callbacks: { label: (c) => ` ${c.dataset.label}: ${fmt(c.parsed.y)}` } },
        vlines: { lines },
      },
    },
    plugins: [vlines],
  });
}

// ---------------------------------------------------------------- calendar heatmap
export function renderCalendarHeatmap(canvas, cal, hoverEl) {
  const dpr = window.devicePixelRatio || 1;
  const years = cal.years, nd = cal.doy.length;
  const cssW = canvas.parentElement.clientWidth - 28;
  const left = 46, top = 22, right = 8, bottom = 6;
  const cellH = 16;
  const cssH = top + years.length * cellH + bottom;
  canvas.style.width = cssW + "px"; canvas.style.height = cssH + "px";
  canvas.width = Math.round(cssW * dpr); canvas.height = Math.round(cssH * dpr);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cssW, cssH);
  const cw = (cssW - left - right) / nd;

  const vals = [];
  for (const row of cal.matrix) for (const v of row) if (v !== null && v > 0) vals.push(v);
  vals.sort((a, b) => a - b);
  const vmax = vals.length ? vals[Math.floor(0.985 * (vals.length - 1))] : 1;

  ctx.font = "10px ui-monospace, monospace";
  ctx.textBaseline = "middle";
  years.forEach((y, r) => {
    ctx.fillStyle = y === cal.target_year ? "#b7791f" : "#5d6a7a";
    ctx.textAlign = "right";
    ctx.fillText(String(y), left - 6, top + r * cellH + cellH / 2);
    const row = cal.matrix[r];
    for (let d = 0; d < nd; d++) {
      const v = row[d];
      ctx.fillStyle = v === null ? "#eceff3" : v <= 0 ? "#f4f6f8" : rampColor(Math.sqrt(v / vmax));
      ctx.fillRect(left + d * cw, top + r * cellH, Math.ceil(cw) + 0.5, cellH - 1);
    }
  });
  // month ticks
  const mdays = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334];
  const mn = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  ctx.fillStyle = "#5d6a7a"; ctx.textAlign = "left"; ctx.textBaseline = "alphabetic";
  mdays.forEach((d, i) => ctx.fillText(mn[i], left + d * cw + 2, top - 7));
  ctx.strokeStyle = "rgba(30,40,55,.15)";
  mdays.forEach((d) => { ctx.beginPath(); ctx.moveTo(left + d * cw, top - 3); ctx.lineTo(left + d * cw, top + years.length * cellH); ctx.stroke(); });
  // 2012 transition rule
  const ri = years.indexOf(2012);
  if (ri >= 0) {
    ctx.strokeStyle = "#1d4ed8"; ctx.setLineDash([4, 3]); ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(left, top + ri * cellH); ctx.lineTo(cssW - right, top + ri * cellH); ctx.stroke();
    ctx.setLineDash([]);
  }

  canvas.onmousemove = (e) => {
    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left, y = e.clientY - rect.top;
    const d = Math.floor((x - left) / cw), r = Math.floor((y - top) / cellH);
    if (d < 0 || d >= nd || r < 0 || r >= years.length) { hoverEl.textContent = "Hover a cell: year, day of year, activity."; return; }
    const v = cal.matrix[r][d];
    const dt = new Date(Date.UTC(2001, 0, 1 + d));
    hoverEl.textContent = `${years[r]}  day ${d + 1} (${mn[dt.getUTCMonth()]} ${dt.getUTCDate()})  ->  ${v === null ? "no data" : fmt(v, 1) + " hotspots/day (7-day smoothed)"}`;
  };
  canvas.onmouseleave = () => { hoverEl.textContent = "Hover a cell: year, day of year, activity."; };
}

export function renderEnvelope(canvas, cal) {
  fresh(canvas);
  const labels = cal.doy;
  const env = cal.envelope;
  new Chart(canvas, {
    type: "line",
    data: {
      labels,
      datasets: [
        { label: "_p10", data: env.p10, borderWidth: 0, pointRadius: 0, fill: false },
        { label: "P10-P90 envelope (2001-2025)", data: env.p90, borderWidth: 0, pointRadius: 0, backgroundColor: "rgba(232,89,12,.16)", fill: "-1" },
        { label: "P50 climatology", data: env.p50, borderColor: C.fire, borderWidth: 1.5, pointRadius: 0 },
        { label: `${cal.target_year}`, data: cal.target_series, borderColor: C.harm, borderWidth: 1.8, pointRadius: 0, spanGaps: false },
      ],
    },
    options: {
      interaction: { mode: "index", intersect: false },
      scales: { x: { ticks: { maxTicksLimit: 12 }, grid: { display: false }, title: { display: true, text: "day of year" } }, y: { title: { display: true, text: "hotspots / day" }, ticks: { callback: (v) => fmt(v) } } },
      plugins: { legend: { labels: { filter: (i) => !i.text.startsWith("_"), usePointStyle: true, boxWidth: 8 } }, tooltip: { filter: (i) => !i.dataset.label.startsWith("_") } },
    },
  });
}

// ---------------------------------------------------------------- diurnal + drift
const SAT_COLORS = { Terra: "#c27803", Aqua: "#c2410c", SNPP: "#0b8fa3", NOAA20: "#1f9d55" };

export function renderDiurnal(canvas, d) {
  fresh(canvas);
  const { lst, relative_intensity: ri } = d.curve;
  const at = (h) => { const i = Math.round((((h % 24) + 24) % 24) / 0.25); return ri[Math.min(i, ri.length - 1)]; };
  const dr = d.overpass_drift;
  const yi = dr.years.findIndex((y) => y >= 2026.5);
  const k = yi < 0 ? dr.years.length - 1 : yi;
  const sets = [{
    type: "line", label: "Relative fire intensity D(t)", data: lst.map((h, i) => ({ x: h, y: ri[i] })),
    borderColor: C.fire, backgroundColor: "rgba(232,89,12,.12)", fill: true, pointRadius: 0, borderWidth: 2,
  }];
  for (const s of ["Terra", "Aqua", "SNPP"]) {
    const day = dr[s][k], night = (day + 12) % 24;
    sets.push({ type: "scatter", label: `${s} day ${day.toFixed(1)} h / night ${night.toFixed(1)} h (2026)`, data: [{ x: day, y: at(day) }, { x: night, y: at(night) }], borderColor: SAT_COLORS[s], backgroundColor: SAT_COLORS[s], pointRadius: 6, pointStyle: s === "Terra" ? "triangle" : s === "Aqua" ? "rectRot" : "circle" });
  }
  sets.push({ type: "scatter", label: `Reference ${d.params.day_reference_hour} h / ${d.params.night_reference_hour} h`, data: [{ x: d.params.day_reference_hour, y: at(d.params.day_reference_hour) }, { x: d.params.night_reference_hour, y: at(d.params.night_reference_hour) }], borderColor: "#111827", backgroundColor: "rgba(0,0,0,0)", pointRadius: 9, borderWidth: 2, pointStyle: "circle" });
  new Chart(canvas, {
    type: "line",
    data: { datasets: sets },
    options: {
      scales: {
        x: { type: "linear", min: 0, max: 24, ticks: { stepSize: 3, callback: (v) => `${v}:00` }, title: { display: true, text: "local solar time" }, grid: { color: C.grid } },
        y: { min: 0, max: 1.05, title: { display: true, text: "relative intensity" } },
      },
      plugins: { legend: { labels: { usePointStyle: true, boxWidth: 8, font: { size: 10 } } }, tooltip: { callbacks: { label: (c) => ` ${c.dataset.label}: ${c.parsed.y.toFixed(2)} at ${c.parsed.x.toFixed(1)} h` } } },
    },
  });
}

export function renderDrift(canvas, d) {
  fresh(canvas);
  const dr = d.overpass_drift;
  const mk = (s, extra = {}) => ({ label: s, data: dr.years.map((y, i) => ({ x: y, y: dr[s][i] })), borderColor: SAT_COLORS[s], backgroundColor: SAT_COLORS[s], pointRadius: 0, borderWidth: 2, ...extra });
  const sets = [mk("Terra"), mk("Aqua"), mk("SNPP", { borderDash: [5, 4], borderWidth: 1.2 })];
  new Chart(canvas, {
    type: "line",
    data: { datasets: sets },
    options: {
      scales: {
        x: { type: "linear", min: 2012, max: 2027, ticks: { stepSize: 2, callback: (v) => String(v) }, grid: { display: false } },
        y: { min: 9, max: 17, title: { display: true, text: "daytime local solar time (h)" } },
      },
      plugins: {
        legend: { labels: { usePointStyle: true, boxWidth: 8 } },
        tooltip: { callbacks: { label: (c) => ` ${c.dataset.label}: ${c.parsed.y.toFixed(2)} h` } },
        vlines: { lines: [{ x: 2021, color: C.warn, label: "Terra drift starts", row: 0 }, { x: 2022, color: "#c2410c", label: "Aqua drift starts", row: 1 }] },
      },
    },
    plugins: [vlines],
  });
}

// ---------------------------------------------------------------- importance / mini
export function renderImportance(canvas, imp) {
  fresh(canvas);
  const ent = Object.entries(imp).sort((a, b) => b[1] - a[1]).slice(0, 10);
  new Chart(canvas, {
    type: "bar",
    data: { labels: ent.map((e) => e[0]), datasets: [{ data: ent.map((e) => e[1]), backgroundColor: C.harm, borderRadius: 3 }] },
    options: {
      indexAxis: "y",
      scales: { x: { title: { display: true, text: "increase in log-MAE when permuted" }, grid: { color: C.grid } }, y: { grid: { display: false } } },
      plugins: { legend: { display: false } },
    },
  });
}

export function renderMiniSeries(canvas, ts) {
  fresh(canvas);
  new Chart(canvas, {
    type: "line",
    data: {
      labels: ts.labels,
      datasets: [
        { label: "raw", data: ts.raw, borderColor: C.raw, borderWidth: 1.4, pointRadius: 0 },
        { label: "harmonized", data: ts.harmonized, borderColor: C.harm, borderWidth: 1.6, pointRadius: 0 },
      ],
    },
    options: {
      interaction: { mode: "index", intersect: false },
      scales: { x: { ticks: { maxTicksLimit: 4, font: { size: 9 } }, grid: { display: false } }, y: { ticks: { maxTicksLimit: 4, font: { size: 9 } }, beginAtZero: true } },
      plugins: { legend: { labels: { boxWidth: 8, font: { size: 10 } } } },
    },
  });
}

export function renderDriverChart(canvas, drivers) {
  fresh(canvas);
  const labels = drivers.map((d) => d.driver);
  const vals = drivers.map((d) => d.effect_pct);
  new Chart(canvas, {
    type: "bar",
    data: { labels, datasets: [{ data: vals, backgroundColor: vals.map((v) => (v >= 0 ? C.fire : C.harm)), borderRadius: 3 }] },
    options: {
      indexAxis: "y",
      scales: { x: { title: { display: true, text: "effect on harmonized FRP (%)" }, grid: { color: C.grid } }, y: { grid: { display: false } } },
      plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => ` ${c.parsed.x > 0 ? "+" : ""}${c.parsed.x.toFixed(1)}%` } } },
    },
  });
}

// ---------------------------------------------------------------- bow-tie geometry
export function renderGeometry(canvas, g, noteEl, tableEl) {
  const dpr = window.devicePixelRatio || 1;
  const cssW = canvas.parentElement.clientWidth - 28, cssH = 260;
  canvas.style.width = cssW + "px"; canvas.style.height = cssH + "px";
  canvas.width = Math.round(cssW * dpr); canvas.height = Math.round(cssH * dpr);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cssW, cssH);
  const picks = [0, 15, 30, 45, 55];
  const idx = picks.map((a) => g.scan_angle_deg.indexOf(a));
  const maxScan = Math.max(...idx.map((i) => g.modis.scan_km[i]));
  const scale = Math.min((cssW / picks.length - 24) / maxScan, 40);
  const colW = cssW / picks.length;
  ctx.textAlign = "center";
  idx.forEach((i, k) => {
    const cx = colW * k + colW / 2, cy = 118;
    const ms = g.modis.scan_km[i] * scale, mt = g.modis.track_km[i] * scale;
    ctx.fillStyle = "rgba(214,50,42,.16)"; ctx.strokeStyle = "#d6322a"; ctx.lineWidth = 1.5;
    ctx.fillRect(cx - ms / 2, cy - mt / 2, ms, mt); ctx.strokeRect(cx - ms / 2, cy - mt / 2, ms, mt);
    const vs = g.viirs.scan_km[i] * scale, vt = g.viirs.track_km[i] * scale;
    ctx.fillStyle = "rgba(11,143,163,.45)"; ctx.strokeStyle = "#0b8fa3";
    ctx.fillRect(cx - vs / 2, cy - vt / 2, vs, vt); ctx.strokeRect(cx - vs / 2, cy - vt / 2, vs, vt);
    ctx.fillStyle = "#1b2431"; ctx.font = "12px ui-monospace, monospace";
    ctx.fillText(`${picks[k]} deg`, cx, 28);
    ctx.fillStyle = "#d6322a"; ctx.fillText(`${g.modis.scan_km[i].toFixed(2)} x ${g.modis.track_km[i].toFixed(2)} km`, cx, 192);
    ctx.fillStyle = "#5d6a7a"; ctx.fillText(`${g.modis.area_km2[i].toFixed(1)} km2  (${g.modis.expansion[i].toFixed(1)}x nadir)`, cx, 210);
    ctx.fillStyle = "#0b8fa3"; ctx.fillText(`VIIRS ${g.viirs.scan_km[i].toFixed(2)} km`, cx, 228);
  });
  ctx.fillStyle = "#5d6a7a"; ctx.font = "11px Inter, sans-serif"; ctx.textAlign = "left";
  ctx.fillText("scan angle from nadir ->  (all footprints drawn to the same scale)", 8, 250);
  noteEl.textContent = `MODIS: ${g.modis.half_swath_km} km half-swath; VIIRS 375 m: ${g.viirs.half_swath_km} km. H3 res-7 = ${g.h3.res7_km2} km2, res-8 = ${g.h3.res8_km2} km2. ${g.note}`;
  const rows = g.scan_angle_deg.map((a, i) => `<tr><td>${a}</td><td class="r">${g.modis.scan_km[i].toFixed(2)} x ${g.modis.track_km[i].toFixed(2)}</td><td class="r">${g.modis.area_km2[i].toFixed(2)}</td><td class="h">${g.viirs.scan_km[i].toFixed(3)}</td></tr>`).join("");
  tableEl.innerHTML = `<table class="tbl" style="margin-top:8px"><thead><tr><th>Angle</th><th>MODIS scan x track (km)</th><th>MODIS area (km2)</th><th>VIIRS pixel (km)</th></tr></thead><tbody>${rows}</tbody></table>`;
}
