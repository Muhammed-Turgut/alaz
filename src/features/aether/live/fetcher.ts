import type { Instrument } from "./config";

export interface FetchMeta {
  live: boolean;
  tiers: string[];
  rows: number;
  newest: string;
  oldest: string;
  keyConfigured: boolean;
}

export interface RawFetch {
  csv: string;
  meta: FetchMeta;
}

export class FireDataFetcher {
  constructor(private readonly opts: { apiEndpoint: string; timeoutMs: number }) {}

  async fetchRawCsv(args: { instrument: Instrument; dataMode: string; refresh?: boolean; signal?: AbortSignal }): Promise<RawFetch> {
    const params = new URLSearchParams({ instrument: args.instrument, mode: args.dataMode });
    if (args.refresh) params.set("refresh", "true");
    const url = `${this.opts.apiEndpoint}?${params.toString()}`;

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.opts.timeoutMs);
    args.signal?.addEventListener("abort", () => controller.abort(), { once: true });

    try {
      const res = await fetch(url, { cache: "no-store", signal: controller.signal });
      if (!res.ok) {
        let msg = "";
        try {
          const j = await res.clone().json();
          msg = typeof j?.detail === "string" ? j.detail : "";
        } catch {
          msg = (await res.text()).slice(0, 200);
        }
        if (res.status === 404 && (!msg || msg === "Not Found")) {
          throw new Error("/api/fires bulunamadı: Python sunucusu eski sürümle çalışıyor, `npm run api` sürecini durdurup yeniden başlat");
        }
        throw new Error(msg || `HTTP hata kodu: ${res.status}`);
      }
      const csv = await res.text();
      if (!csv.startsWith("latitude,longitude")) throw new Error("Sunucudan geçerli bir CSV başlığı alınamadı.");
      const h = res.headers;
      return {
        csv,
        meta: {
          live: h.get("X-Firms-Live") === "1",
          tiers: (h.get("X-Firms-Tiers") || "").split(",").filter(Boolean),
          rows: Number(h.get("X-Firms-Rows") || 0),
          newest: h.get("X-Firms-Newest") || "",
          oldest: h.get("X-Firms-Oldest") || "",
          keyConfigured: h.get("X-Firms-Key") === "1",
        },
      };
    } catch (e) {
      if ((e as Error).name === "AbortError") {
        if (args.signal?.aborted) throw e;
        throw new Error("NASA sunucusu zaman aşımına uğradı. Lütfen yeniden dene.");
      }
      throw e;
    } finally {
      clearTimeout(timer);
    }
  }
}
