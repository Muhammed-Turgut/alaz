import type { ConfidenceKey, Instrument } from "./config";
import type { FirePoint } from "./processor";

export type InstrumentFilter = "all" | Instrument;

export interface FilterState {
  enabledConfidences: Set<ConfidenceKey>;
  bounds: { minTemp: number; maxTemp: number };
  currentRange: { minTemp: number; maxTemp: number };
  instrument: InstrumentFilter;
}

export class FireDataFilter {
  private defaultConfidences: ConfidenceKey[];
  private enabledConfidences: Set<ConfidenceKey>;
  private bounds = { minTemp: 0, maxTemp: 200 };
  private currentRange = { minTemp: 0, maxTemp: 200 };
  private instrument: InstrumentFilter = "all";

  constructor(opts: { allowedConfidences: ConfidenceKey[] }) {
    this.defaultConfidences = [...opts.allowedConfidences];
    this.enabledConfidences = new Set(opts.allowedConfidences);
  }

  initializeFromDataset(points: FirePoint[]) {
    this.bounds = computeBounds(points);
    this.currentRange = { ...this.bounds };
    this.enabledConfidences = new Set(this.defaultConfidences);
  }

  setConfidenceEnabled(key: ConfidenceKey, on: boolean) {
    if (on) this.enabledConfidences.add(key);
    else this.enabledConfidences.delete(key);
  }

  setInstrument(i: InstrumentFilter) {
    this.instrument = i;
  }

  setTemperatureRange({ minTemp, maxTemp }: { minTemp: number; maxTemp: number }) {
    const lo = Math.max(this.bounds.minTemp, Math.min(minTemp, this.bounds.maxTemp));
    const hi = Math.min(this.bounds.maxTemp, Math.max(maxTemp, this.bounds.minTemp));
    this.currentRange = { minTemp: Math.min(lo, hi), maxTemp: Math.max(lo, hi) };
  }

  reset() {
    this.enabledConfidences = new Set(this.defaultConfidences);
    this.currentRange = { ...this.bounds };
  }

  /** Sensör (cihaz) süzgeci uygulanmış küme: güven sayaçları ve nokta sayıları bunun üzerinden hesaplanır. */
  scope(points: FirePoint[]): FirePoint[] {
    return this.instrument === "all" ? points : points.filter((p) => p.instrument === this.instrument);
  }

  apply(points: FirePoint[]): FirePoint[] {
    const { minTemp, maxTemp } = this.currentRange;
    return this.scope(points).filter(
      (p) => this.enabledConfidences.has(p.confidenceCode) && p.temperatureCelsius >= minTemp && p.temperatureCelsius <= maxTemp,
    );
  }

  computeConfidenceCounts(points: FirePoint[]): Record<ConfidenceKey, number> {
    const counts: Record<ConfidenceKey, number> = { HIGH: 0, NOMINAL: 0, LOW: 0, UNKNOWN: 0 };
    for (const p of this.scope(points)) counts[p.confidenceCode] += 1;
    return counts;
  }

  getState(): FilterState {
    return {
      enabledConfidences: new Set(this.enabledConfidences),
      bounds: { ...this.bounds },
      currentRange: { ...this.currentRange },
      instrument: this.instrument,
    };
  }
}

function computeBounds(points: FirePoint[]) {
  if (!points.length) return { minTemp: 0, maxTemp: 100 };
  let min = Infinity;
  let max = -Infinity;
  for (const p of points) {
    if (p.temperatureCelsius < min) min = p.temperatureCelsius;
    if (p.temperatureCelsius > max) max = p.temperatureCelsius;
  }
  return { minTemp: Math.floor(min * 10) / 10, maxTemp: Math.ceil(max * 10) / 10 };
}
