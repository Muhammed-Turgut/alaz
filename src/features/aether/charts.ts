import { Chart, registerables, type ChartConfiguration, type Plugin } from "chart.js";

import { fmt } from "./format";
import type {
  BreakPayload, CalendarPayload, DiurnalModel, GeometryPayload, SeriesPayload,
} from "./types";
import { alpha, makeRamp, viz } from "./viz";

Chart.register(...registerables);

function defaults() {
  const v = viz();
  Chart.defaults.color = v.muted;
  Chart.defaults.borderColor = v.border;
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  Chart.defaults.font.size = 11;
  Chart.defaults.animation = false;
  Chart.defaults.maintainAspectRatio = false;
  return v;
}

interface VLine { x: number; color: string; label: string; row?: number; align?: "left" | "right"; dash?: number[]; width?: number }

/** Dikey açıklama çizgileri (2012 geçişi, Pettitt kırılma tarihleri...). */
const vlines: Plugin<"line"> = {
  id: "vlines",
  afterDatasetsDraw(chart, _args, opts) {
    const lines = (opts as { lines?: VLine[] }).lines;
    if (!lines) return;
    const { ctx, chartArea, scales } = chart;
    ctx.save();
    for (const ln of lines) {
      const x = scales.x.getPixelForValue(ln.x);
      if (!Number.isFinite(x) || x < chartArea.left || x > chartArea.right) continue;
      ctx.strokeStyle = ln.color;
      ctx.lineWidth = ln.width ?? 1;
      ctx.setLineDash(ln.dash ?? [5, 4]);
      ctx.beginPath();
      ctx.moveTo(x, chartArea.top);
      ctx.lineTo(x, chartArea.bottom);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = ln.color;
      ctx.textAlign = ln.align ?? "left";
      ctx.fillText(ln.label, x + (ln.align === "right" ? -5 : 5), chartArea.top + 12 + (ln.row ?? 0) * 13);
    }
    ctx.restore();
  },
};

const hideInternal = (i: { text: string }) => !i.text.startsWith("_");
const cfg = (c: unknown) => c as ChartConfiguration;

// ------------------------------------------------------------------ yapısal kırılma
export function breakChart(canvas: HTMLCanvasElement, s: SeriesPayload, freq: "monthly" | "annual", b: BreakPayload | null): Chart {
  const v = defaults();
  const idx = s.labels.indexOf(freq === "monthly" ? "2012-01" : "2012");
  const lines: VLine[] = [{ x: idx, color: v.warn, label: "VIIRS 375 m hizmette (2012-01-20)", width: 1.5, dash: [], row: 0 }];
  if (freq === "monthly" && b) {
    const add = (d: string | undefined, color: string, tag: string, row: number) => {
      if (!d) return;
      const i = s.labels.indexOf(d.slice(0, 7));
      if (i >= 0) lines.push({ x: i, color, label: `Pettitt ${tag} ${d.slice(0, 7)}`, row, align: i > s.labels.length * 0.75 ? "right" : "left" });
    };
    add(b.reports.raw_filtered.pettitt_monthly.change_date, v.raw, "ham", 1);
    add(b.reports.harmonized.pettitt_monthly.change_date, v.harmonized, "harmonize", 2);
  }
  return new Chart(canvas, cfg({
    type: "line",
    data: {
      labels: s.labels,
      datasets: [
        { label: "Ham, raporlandığı gibi (MODIS+VIIRS)", data: s.raw_all, borderColor: v.raw, backgroundColor: v.raw, borderWidth: 1.6, pointRadius: 0 },
        { label: "Ham, kalıcı kaynaklar çıkarılmış", data: s.raw_filtered, borderColor: alpha(v.raw, 0.5), borderWidth: 1, borderDash: [3, 3], pointRadius: 0 },
        { label: "_lo", data: s.harmonized_lo, borderWidth: 0, pointRadius: 0, backgroundColor: alpha(v.harmonized, 0.16), fill: false },
        { label: "_hi", data: s.harmonized_hi, borderWidth: 0, pointRadius: 0, backgroundColor: alpha(v.harmonized, 0.16), fill: "-1" },
        { label: "Harmonize (VIIRS-eşdeğeri), %90 GA", data: s.harmonized, borderColor: v.harmonized, backgroundColor: v.harmonized, borderWidth: 1.8, pointRadius: 0 },
        { label: "Gerçek: her yerde SNPP (gizli)", data: s.truth_snpp_everywhere, borderColor: v.truth, borderWidth: 1.2, borderDash: [6, 3], pointRadius: 0 },
      ],
    },
    options: {
      interaction: { mode: "index", intersect: false },
      scales: {
        x: { ticks: { maxTicksLimit: 14, autoSkip: true }, grid: { display: false } },
        y: { title: { display: true, text: freq === "monthly" ? "sıcak nokta / ay" : "sıcak nokta / yıl" }, ticks: { callback: (x: unknown) => fmt(Number(x)) } },
      },
      plugins: {
        legend: { labels: { filter: hideInternal, usePointStyle: true, boxWidth: 8 } },
        tooltip: {
          filter: (i: { dataset: { label?: string } }) => !(i.dataset.label ?? "").startsWith("_"),
          callbacks: { label: (c: { dataset: { label?: string }; parsed: { y: number } }) => ` ${c.dataset.label}: ${fmt(c.parsed.y)}` },
        },
        vlines: { lines },
      },
    },
    plugins: [vlines],
  }));
}

export function envelopeChart(canvas: HTMLCanvasElement, cal: CalendarPayload): Chart {
  const v = defaults();
  const env = cal.envelope;
  return new Chart(canvas, cfg({
    type: "line",
    data: {
      labels: cal.doy,
      datasets: [
        { label: "_p10", data: env.p10, borderWidth: 0, pointRadius: 0, fill: false },
        { label: "P10-P90 zarfı (2001-2025)", data: env.p90, borderWidth: 0, pointRadius: 0, backgroundColor: alpha(v.fire, 0.16), fill: "-1" },
        { label: "P50 iklimsel", data: env.p50, borderColor: v.fire, borderWidth: 1.5, pointRadius: 0 },
        { label: String(cal.target_year), data: cal.target_series, borderColor: v.harmonized, borderWidth: 1.8, pointRadius: 0 },
      ],
    },
    options: {
      interaction: { mode: "index", intersect: false },
      scales: {
        x: { ticks: { maxTicksLimit: 12 }, grid: { display: false }, title: { display: true, text: "yılın günü" } },
        y: { title: { display: true, text: "sıcak nokta / gün" }, ticks: { callback: (x: unknown) => fmt(Number(x)) } },
      },
      plugins: {
        legend: { labels: { filter: hideInternal, usePointStyle: true, boxWidth: 8 } },
        tooltip: { filter: (i: { dataset: { label?: string } }) => !(i.dataset.label ?? "").startsWith("_") },
      },
    },
  }));
}

// ------------------------------------------------------------------ diurnal + kayma
export function diurnalChart(canvas: HTMLCanvasElement, d: DiurnalModel): Chart {
  const v = defaults();
  const { lst, relative_intensity: ri } = d.curve;
  const at = (h: number) => ri[Math.min(Math.round((((h % 24) + 24) % 24) / 0.25), ri.length - 1)];
  const dr = d.overpass_drift;
  const yi = dr.years.findIndex((y) => y >= 2026.5);
  const k = yi < 0 ? dr.years.length - 1 : yi;
  const datasets: unknown[] = [{
    type: "line", label: "Göreli yangın şiddeti D(t)", data: lst.map((h, i) => ({ x: h, y: ri[i] })),
    borderColor: v.fire, backgroundColor: alpha(v.fire, 0.12), fill: true, pointRadius: 0, borderWidth: 2,
  }];
  (["Terra", "Aqua", "SNPP"] as const).forEach((s) => {
    const day = dr[s][k];
    const night = (day + 12) % 24;
    datasets.push({
      type: "scatter", label: `${s} gündüz ${day.toFixed(1)} sa / gece ${night.toFixed(1)} sa (2026)`,
      data: [{ x: day, y: at(day) }, { x: night, y: at(night) }],
      borderColor: v.sat[s], backgroundColor: v.sat[s], pointRadius: 6,
      pointStyle: s === "Terra" ? "triangle" : s === "Aqua" ? "rectRot" : "circle",
    });
  });
  const dRef = d.params.day_reference_hour;
  const nRef = d.params.night_reference_hour;
  datasets.push({
    type: "scatter", label: `Referans ${dRef} sa / ${nRef} sa`, data: [{ x: dRef, y: at(dRef) }, { x: nRef, y: at(nRef) }],
    borderColor: v.selected, backgroundColor: alpha(v.selected, 0), pointRadius: 9, borderWidth: 2,
  });
  return new Chart(canvas, cfg({
    type: "line",
    data: { datasets },
    options: {
      scales: {
        x: { type: "linear", min: 0, max: 24, ticks: { stepSize: 3, callback: (x: unknown) => `${x}:00` }, title: { display: true, text: "yerel güneş saati" } },
        y: { min: 0, max: 1.05, title: { display: true, text: "göreli şiddet" } },
      },
      plugins: {
        legend: { labels: { usePointStyle: true, boxWidth: 8, font: { size: 10 } } },
        tooltip: { callbacks: { label: (c: { dataset: { label?: string }; parsed: { x: number; y: number } }) => ` ${c.dataset.label}: ${c.parsed.y.toFixed(2)} (${c.parsed.x.toFixed(1)} sa)` } },
      },
    },
  }));
}

export function driftChart(canvas: HTMLCanvasElement, d: DiurnalModel): Chart {
  const v = defaults();
  const dr = d.overpass_drift;
  const mk = (s: "Terra" | "Aqua" | "SNPP", extra: object = {}) => ({
    label: s, data: dr.years.map((y, i) => ({ x: y, y: dr[s][i] })),
    borderColor: v.sat[s], backgroundColor: v.sat[s], pointRadius: 0, borderWidth: 2, ...extra,
  });
  return new Chart(canvas, cfg({
    type: "line",
    data: { datasets: [mk("Terra"), mk("Aqua"), mk("SNPP", { borderDash: [5, 4], borderWidth: 1.2 })] },
    options: {
      scales: {
        x: { type: "linear", min: 2012, max: 2027, ticks: { stepSize: 2, callback: (x: unknown) => String(x) }, grid: { display: false } },
        y: { min: 9, max: 17, title: { display: true, text: "gündüz yerel güneş saati (sa)" } },
      },
      plugins: {
        legend: { labels: { usePointStyle: true, boxWidth: 8 } },
        tooltip: { callbacks: { label: (c: { dataset: { label?: string }; parsed: { y: number } }) => ` ${c.dataset.label}: ${c.parsed.y.toFixed(2)} sa` } },
        vlines: { lines: [
          { x: 2021, color: v.warn, label: "Terra kayması başlar", row: 0 },
          { x: 2022, color: v.sat.Aqua, label: "Aqua kayması başlar", row: 1 },
        ] },
      },
    },
    plugins: [vlines],
  }));
}

// ------------------------------------------------------------------ önem / mini / sürücüler
export function importanceChart(canvas: HTMLCanvasElement, imp: Record<string, number>): Chart {
  const v = defaults();
  const ent = Object.entries(imp).sort((a, b) => b[1] - a[1]).slice(0, 10);
  return new Chart(canvas, cfg({
    type: "bar",
    data: { labels: ent.map((e) => e[0]), datasets: [{ data: ent.map((e) => e[1]), backgroundColor: v.harmonized, borderRadius: 3 }] },
    options: {
      indexAxis: "y",
      scales: { x: { title: { display: true, text: "karıştırılınca log-MAE artışı" } }, y: { grid: { display: false } } },
      plugins: { legend: { display: false } },
    },
  }));
}

export function miniSeriesChart(canvas: HTMLCanvasElement, ts: { labels: string[]; raw: number[]; harmonized: number[] }): Chart {
  const v = defaults();
  return new Chart(canvas, cfg({
    type: "line",
    data: {
      labels: ts.labels,
      datasets: [
        { label: "ham", data: ts.raw, borderColor: v.raw, borderWidth: 1.4, pointRadius: 0 },
        { label: "harmonize", data: ts.harmonized, borderColor: v.harmonized, borderWidth: 1.6, pointRadius: 0 },
      ],
    },
    options: {
      interaction: { mode: "index", intersect: false },
      scales: {
        x: { ticks: { maxTicksLimit: 4, font: { size: 9 } }, grid: { display: false } },
        y: { ticks: { maxTicksLimit: 4, font: { size: 9 } }, beginAtZero: true },
      },
      plugins: { legend: { labels: { boxWidth: 8, font: { size: 10 } } } },
    },
  }));
}

export function driverChart(canvas: HTMLCanvasElement, drivers: { driver: string; effect_pct: number }[]): Chart {
  const v = defaults();
  const vals = drivers.map((d) => d.effect_pct);
  return new Chart(canvas, cfg({
    type: "bar",
    data: { labels: drivers.map((d) => d.driver), datasets: [{ data: vals, backgroundColor: vals.map((x) => (x >= 0 ? v.fire : v.harmonized)), borderRadius: 3 }] },
    options: {
      indexAxis: "y",
      scales: { x: { title: { display: true, text: "harmonize FRP üzerindeki etki (%)" } }, y: { grid: { display: false } } },
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: (c: { parsed: { x: number } }) => ` ${c.parsed.x > 0 ? "+" : ""}${c.parsed.x.toFixed(1)}%` } },
      },
    },
  }));
}

// ------------------------------------------------------------------ elle çizilen canvaslar
function setupCanvas(canvas: HTMLCanvasElement, cssW: number, cssH: number): CanvasRenderingContext2D {
  const dpr = window.devicePixelRatio || 1;
  canvas.style.width = `${cssW}px`;
  canvas.style.height = `${cssH}px`;
  canvas.width = Math.round(cssW * dpr);
  canvas.height = Math.round(cssH * dpr);
  const ctx = canvas.getContext("2d")!;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cssW, cssH);
  return ctx;
}

const MONTH_DAYS = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334];
const MONTHS = ["Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara"];

export interface HeatmapGeom { left: number; top: number; cw: number; cellH: number }

/** 25 yıl x 365 gün takvim ısı haritası. Fare üstü için geometriyi döndürür. */
export function drawCalendar(canvas: HTMLCanvasElement, cal: CalendarPayload, cssW: number): HeatmapGeom {
  const v = viz();
  const ramp = makeRamp(v.ramp);
  const years = cal.years;
  const nd = cal.doy.length;
  const left = 46, top = 22, right = 8, bottom = 6, cellH = 16;
  const cssH = top + years.length * cellH + bottom;
  const ctx = setupCanvas(canvas, cssW, cssH);
  const cw = (cssW - left - right) / nd;

  const vals: number[] = [];
  for (const row of cal.matrix) for (const x of row) if (x !== null && x > 0) vals.push(x);
  vals.sort((a, b) => a - b);
  const vmax = vals.length ? vals[Math.floor(0.985 * (vals.length - 1))] : 1;

  ctx.font = "10px ui-monospace, monospace";
  ctx.textBaseline = "middle";
  years.forEach((y, r) => {
    ctx.fillStyle = y === cal.target_year ? v.warn : v.muted;
    ctx.textAlign = "right";
    ctx.fillText(String(y), left - 6, top + r * cellH + cellH / 2);
    const row = cal.matrix[r];
    for (let d = 0; d < nd; d++) {
      const x = row[d];
      ctx.fillStyle = x === null ? v.border : x <= 0 ? v.subtle : ramp(Math.sqrt(x / vmax));
      ctx.fillRect(left + d * cw, top + r * cellH, Math.ceil(cw) + 0.5, cellH - 1);
    }
  });
  ctx.fillStyle = v.muted;
  ctx.textAlign = "left";
  ctx.textBaseline = "alphabetic";
  MONTH_DAYS.forEach((d, i) => ctx.fillText(MONTHS[i], left + d * cw + 2, top - 7));
  ctx.strokeStyle = alpha(v.text, 0.15);
  MONTH_DAYS.forEach((d) => {
    ctx.beginPath();
    ctx.moveTo(left + d * cw, top - 3);
    ctx.lineTo(left + d * cw, top + years.length * cellH);
    ctx.stroke();
  });
  const ri = years.indexOf(2012);
  if (ri >= 0) {
    ctx.strokeStyle = v.roi;
    ctx.setLineDash([4, 3]);
    ctx.beginPath();
    ctx.moveTo(left, top + ri * cellH);
    ctx.lineTo(cssW - right, top + ri * cellH);
    ctx.stroke();
    ctx.setLineDash([]);
  }
  return { left, top, cw, cellH };
}

export function calendarHoverText(cal: CalendarPayload, g: HeatmapGeom, x: number, y: number): string {
  const d = Math.floor((x - g.left) / g.cw);
  const r = Math.floor((y - g.top) / g.cellH);
  if (d < 0 || d >= cal.doy.length || r < 0 || r >= cal.years.length) return "Bir hücrenin üstüne gel: yıl, yılın günü, etkinlik.";
  const val = cal.matrix[r][d];
  const dt = new Date(Date.UTC(2001, 0, 1 + d));
  return `${cal.years[r]}  gün ${d + 1} (${MONTHS[dt.getUTCMonth()]} ${dt.getUTCDate()})  ->  ${val === null ? "veri yok" : `${fmt(val, 1)} sıcak nokta/gün (7 gün yumuşatılmış)`}`;
}

/** MODIS bow-tie piksel ayak izi (aynı ölçekte) + VIIRS 375 m karşılaştırması. */
export function drawGeometry(canvas: HTMLCanvasElement, g: GeometryPayload, cssW: number) {
  const v = viz();
  const cssH = 285;
  const ctx = setupCanvas(canvas, cssW, cssH);
  const picks = [0, 15, 30, 45, 55];
  const idx = picks.map((a) => g.scan_angle_deg.indexOf(a));
  const maxScan = Math.max(...idx.map((i) => g.modis.scan_km[i]));
  const scale = Math.min((cssW / picks.length - 24) / maxScan, 40);
  const colW = cssW / picks.length;
  ctx.textAlign = "center";
  idx.forEach((i, k) => {
    const cx = colW * k + colW / 2, cy = 118;
    const ms = g.modis.scan_km[i] * scale, mt = g.modis.track_km[i] * scale;
    ctx.fillStyle = alpha(v.raw, 0.16); ctx.strokeStyle = v.raw; ctx.lineWidth = 1.5;
    ctx.fillRect(cx - ms / 2, cy - mt / 2, ms, mt);
    ctx.strokeRect(cx - ms / 2, cy - mt / 2, ms, mt);
    const vs = g.viirs.scan_km[i] * scale, vt = g.viirs.track_km[i] * scale;
    ctx.fillStyle = alpha(v.harmonized, 0.45); ctx.strokeStyle = v.harmonized;
    ctx.fillRect(cx - vs / 2, cy - vt / 2, vs, vt);
    ctx.strokeRect(cx - vs / 2, cy - vt / 2, vs, vt);
    ctx.font = "12px ui-monospace, monospace";
    ctx.fillStyle = v.text; ctx.fillText(`${picks[k]}°`, cx, 28);
    ctx.font = "11px ui-monospace, monospace";
    ctx.fillStyle = v.raw; ctx.fillText(`${g.modis.scan_km[i].toFixed(2)} x ${g.modis.track_km[i].toFixed(2)} km`, cx, 192);
    ctx.fillStyle = v.muted; ctx.fillText(`${g.modis.area_km2[i].toFixed(1)} km²`, cx, 209);
    ctx.fillText(`(${g.modis.expansion[i].toFixed(1)}x nadir)`, cx, 225);
    ctx.fillStyle = v.harmonized; ctx.fillText(`VIIRS ${g.viirs.scan_km[i].toFixed(2)} km`, cx, 242);
  });
  ctx.fillStyle = v.muted; ctx.textAlign = "left"; ctx.font = "11px sans-serif";
  ctx.fillText("nadirden tarama açısı ->  (tüm ayak izleri aynı ölçekte)", 8, 272);
}
