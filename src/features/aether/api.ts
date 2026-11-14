import type {
  BreakPayload, BriefingResult, CalendarPayload, GeometryPayload, Health, HarmonizedDetection, HexDetail,
  HexPayload, ModelPayload, Region, RoiResult, Scope, SeriesPayload, StaticSources, ValidationPayload,
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
  region: s.source === "benchmark" ? s.region : null,
  start: s.start,
  end: s.end,
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
  hexes: (s: Scope, refresh = false): Promise<HexPayload> =>
    s.source === "benchmark"
      ? request(`/api/hexes?region=${s.region}&start=${s.start}&end=${s.end}`)
      : request(`/api/live?days=${s.days}${refresh ? "&refresh=true" : ""}`),
  hex: (cell: string, s: Scope) =>
    request<HexDetail>(
      s.source === "benchmark"
        ? `/api/hex/${cell}?source=benchmark&region=${s.region}&start=${s.start}&end=${s.end}`
        : `/api/hex/${cell}?source=live&days=${s.days}`,
    ),
  roi: (s: Scope) => post<RoiResult>("/api/roi", { ...scopeBody(s), polygon: s.roi }),
  briefing: (s: Scope) => post<BriefingResult>("/api/briefing", { ...scopeBody(s), polygon: s.roi }),
  harmonize: (det: Record<string, unknown>) =>
    post<{ detections: HarmonizedDetection[]; model: string }>("/api/harmonize", { detections: [det] }),
};
