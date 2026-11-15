import type {
  AreaCalendarResult, AreaDensityResult, ArchiveStatus, BreakPayload, BriefingAudience, BriefingResult,
  CalendarPayload, GeometryPayload, Health, HarmonizedDetection, HexDetail, HexPayload, LatLng,
  ModelPayload, Region, RoiResult, Scope, SeriesPayload, StaticSources, ValidationPayload,
} from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, init);
  } catch (e) {
    throw new Error(`Ağ hatası: ${(e as Error).message}`);
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = (await res.json()) as { detail?: unknown };
      detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {
      /* gövde JSON değil */
    }
    throw new Error(`${res.status}: ${detail}`);
  }
  return (await res.json()) as T;
}

const post = <T>(path: string, body: unknown) =>
  request<T>(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

const memo = new Map<string, Promise<unknown>>();
function cached<T>(key: string, f: () => Promise<T>): Promise<T> {
  if (!memo.has(key)) {
    const p = f().catch((e) => {
      memo.delete(key);
      throw e;
    });
    memo.set(key, p);
  }
  return memo.get(key) as Promise<T>;
}

const scopeBody = (s: Scope) => ({
  source: s.source,
  region: null,
  start: s.start || null,
  end: s.end || null,
  days: s.days,
});

export const api = {
  health: () => request<Health>("/api/health"),
  regions: () => cached("regions", () => request<Region[]>("/api/regions")),
  model: () => cached("model", () => request<ModelPayload>("/api/model")),
  validation: () => cached("validation", () => request<ValidationPayload>("/api/validation")),
  geometry: () => cached("geometry", () => request<GeometryPayload>("/api/geometry")),
  staticSources: () => cached("static", () => request<StaticSources>("/api/static-sources")),
  series: (region: string, freq: "monthly" | "annual") =>
    request<SeriesPayload>(`/api/series?region=${region}&freq=${freq}`),
  structuralBreak: (region: string) => request<BreakPayload>(`/api/structural-break?region=${region}`),
  calendar: (region: string, series: "harmonized" | "raw") =>
    request<CalendarPayload>(`/api/calendar?region=${region}&series=${series}`),
  archiveStatus: (areaId = "tr") => request<ArchiveStatus>(`/api/archive/${areaId}`),
  areaCalendar: (opts: {
    polygon?: LatLng[] | null;
    bbox?: [number, number, number, number]; // west,south,east,north
    series: "harmonized" | "raw";
    target_year?: number;
    start_year?: number;
    end_year?: number;
    month?: number;
  }) =>
    post<AreaCalendarResult>("/api/area-calendar", {
      polygon: opts.polygon ?? null,
      bbox: opts.bbox ?? null,
      series: opts.series,
      target_year: opts.target_year ?? null,
      start_year: opts.start_year ?? null,
      end_year: opts.end_year ?? null,
      month: opts.month ?? null,
    }),
  areaSeries: (opts: {
    polygon?: LatLng[] | null;
    freq: "monthly" | "annual";
  }) =>
    post<SeriesPayload & { empty?: boolean; message?: string; meta?: AreaCalendarResult["meta"]; local_validation?: Record<string, number> }>(
      "/api/area-series",
      { polygon: opts.polygon ?? null, freq: opts.freq },
    ),
  areaBreak: (opts: { polygon?: LatLng[] | null }) =>
    post<BreakPayload & { empty?: boolean; message?: string; meta?: AreaCalendarResult["meta"]; local_validation?: Record<string, number> }>(
      "/api/area-break",
      { polygon: opts.polygon ?? null },
    ),
  areaDensity: (opts: {
    polygon?: LatLng[] | null;
    years: number;
    instrument?: "all" | "MODIS" | "VIIRS";
  }) =>
    post<AreaDensityResult>("/api/area-density", {
      polygon: opts.polygon ?? null,
      years: opts.years,
      instrument: opts.instrument ?? "all",
    }),
  hexes: (s: Scope, refresh = false): Promise<HexPayload> =>
    request(`/api/live?days=${s.days}${refresh ? "&refresh=true" : ""}`),
  hex: (cell: string, s: Scope) =>
    request<HexDetail>(`/api/hex/${cell}?source=live&days=${s.days}`),
  roi: (s: Scope) => post<RoiResult>("/api/roi", { ...scopeBody(s), polygon: s.roi }),
  briefing: (s: Scope, audience: BriefingAudience = "acil", lang: "tr" | "en" = "tr") =>
    post<BriefingResult>("/api/briefing", { ...scopeBody(s), polygon: s.roi, audience, lang }),
  exportHotspotsCsv: async (opts: {
    source: "live" | "archive";
    polygon: LatLng[];
    days?: number;
  }) => {
    const res = await fetch("/api/export-hotspots", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source: opts.source,
        polygon: opts.polygon,
        days: opts.days ?? 1,
      }),
    });
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const j = (await res.json()) as { detail?: unknown };
        detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
      } catch { /* ignore */ }
      throw new Error(`${res.status}: ${detail}`);
    }
    const text = await res.text();
    const n = Number(res.headers.get("X-Alaz-Rows") || "0");
    return { text, n, truncated: res.headers.get("X-Alaz-Truncated") === "1" };
  },
  interpret: (opts: {
    polygon: LatLng[];
    focus: "break" | "calendar" | "model" | "briefing";
    section?: string;
    question?: string;
    lang?: "tr" | "en";
  }) =>
    post<{
      available: boolean;
      empty?: boolean;
      message?: string;
      error?: string;
      markdown?: string;
      model?: string;
      used_numbers?: Record<string, unknown>;
      lang?: string;
    }>("/api/interpret", {
      polygon: opts.polygon,
      focus: opts.focus,
      section: opts.section || "overview",
      question: opts.question || null,
      lang: opts.lang || "tr",
    }),
  harmonize: (det: Record<string, unknown>) =>
    post<{ detections: HarmonizedDetection[]; model: string }>("/api/harmonize", { detections: [det] }),
};
