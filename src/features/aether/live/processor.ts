import type { ConfidenceKey, Instrument } from "./config";

export interface FirePoint {
  latitude: number;
  longitude: number;
  temperatureCelsius: number;
  confidenceCode: ConfidenceKey;
  confidenceLabel: string;
  frp: number;
  instrument: Instrument;
  dataMode: string;
  satellite: string;
  acquiredAt: string;
}

export interface ProcessResult {
  points: FirePoint[];
  totalCsvRows: number;
}

interface ProcessorOptions {
  instrument: Instrument;
  /** Parlaklık sıcaklığı (Kelvin) sütunu: VIIRS = bright_ti4, MODIS = brightness */
  temperatureColumn: string;
  kelvinOffset: number;
  confidenceLabels: Record<ConfidenceKey, string>;
  maxRecords: number | null;
}

/** VIIRS ve MODIS için ortak CSV işleyici; yalnızca sıcaklık sütunu ve cihaz adı farklıdır. */
export class FireCsvProcessor {
  constructor(private readonly o: ProcessorOptions) {}

  process(rawCsv: string, { dataMode = "REALTIME" } = {}): ProcessResult {
    const lines = rawCsv.trim().split(/\r?\n/);
    if (lines.length < 2) return { points: [], totalCsvRows: 0 };

    const headers = parseRow(lines[0]);
    const idx = {
      lat: headers.indexOf("latitude"),
      lon: headers.indexOf("longitude"),
      temp: headers.indexOf(this.o.temperatureColumn),
      confidence: headers.indexOf("confidence"),
      frp: headers.indexOf("frp"),
      date: headers.indexOf("acq_date"),
      time: headers.indexOf("acq_time"),
      satellite: headers.indexOf("source_satellite"),
    };
    if (idx.lat === -1 || idx.lon === -1 || idx.temp === -1 || idx.confidence === -1) {
      throw new Error(`Beklenen ${this.o.instrument} sütunları bulunamadı. Gelen başlık: ${lines[0]}`);
    }

    const totalCsvRows = lines.length - 1;
    const hasLimit = Number.isFinite(this.o.maxRecords) && (this.o.maxRecords as number) > 0;
    const limit = hasLimit ? Math.min(lines.length, (this.o.maxRecords as number) + 1) : lines.length;

    const points: FirePoint[] = [];
    for (let i = 1; i < limit; i++) {
      const cols = parseRow(lines[i]);
      const latitude = parseFloat(cols[idx.lat]);
      const longitude = parseFloat(cols[idx.lon]);
      const kelvin = parseFloat(cols[idx.temp]);
      if (Number.isNaN(latitude) || Number.isNaN(longitude) || Number.isNaN(kelvin)) continue;

      const key = normalizeConfidence(cols[idx.confidence]);
      points.push({
        latitude,
        longitude,
        temperatureCelsius: Number((kelvin - this.o.kelvinOffset).toFixed(1)),
        confidenceCode: key,
        confidenceLabel: this.o.confidenceLabels[key] || this.o.confidenceLabels.UNKNOWN,
        frp: parseFloat(cols[idx.frp]) || 0,
        instrument: this.o.instrument,
        dataMode,
        satellite: idx.satellite !== -1 ? cols[idx.satellite] || this.o.instrument : this.o.instrument,
        acquiredAt: `${cols[idx.date] || ""} ${formatUtcTime(cols[idx.time])}`.trim(),
      });
    }
    return { points, totalCsvRows };
  }
}

function parseRow(row: string): string[] {
  return row.split(",").map((c) => c.trim().replace(/^["']+|["']+$/g, ""));
}

function normalizeConfidence(raw: string | undefined): ConfidenceKey {
  if (raw === undefined || raw === null) return "UNKNOWN";
  const c = String(raw).trim().replace(/^["']+|["']+$/g, "").toLowerCase();
  if (c === "h" || c === "high") return "HIGH";
  if (c === "n" || c === "nominal") return "NOMINAL";
  if (c === "l" || c === "low") return "LOW";
  const score = Number(c);
  if (!Number.isNaN(score) && c !== "") {
    if (score >= 80) return "HIGH";
    if (score >= 30) return "NOMINAL";
    if (score >= 0) return "LOW";
  }
  return "UNKNOWN";
}

function formatUtcTime(raw: string | undefined): string {
  if (!raw || raw.length < 4) return "";
  const p = raw.padStart(4, "0");
  return `${p.slice(0, 2)}:${p.slice(2, 4)} UTC`;
}

export class SensorProcessorRegistry {
  private processors = new Map<string, FireCsvProcessor>();
  register(name: string, p: FireCsvProcessor) {
    this.processors.set(name.toUpperCase(), p);
  }
  get(name: string): FireCsvProcessor {
    const p = this.processors.get(name.toUpperCase());
    if (!p) throw new Error(`Kayıtlı sensör işleyicisi bulunamadı: ${name}`);
    return p;
  }
}
