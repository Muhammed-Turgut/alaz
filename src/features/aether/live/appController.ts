import { EVENTS, type ConfidenceKey, type Instrument } from "./config";
import type { EventBus } from "./eventBus";
import type { FetchMeta, FireDataFetcher } from "./fetcher";
import type { FireDataFilter, FilterState, InstrumentFilter } from "./filterEngine";
import type { FirePoint, SensorProcessorRegistry } from "./processor";

export interface FilterUpdate {
  points: FirePoint[];
  filterState: FilterState;
  filteredCount: number;
  totalCount: number;
  perInstrument: Record<Instrument, number>;
}

export interface DatasetLoaded {
  filterState: FilterState;
  confidenceCounts: Record<ConfidenceKey, number>;
  filteredCount: number;
  totalCount: number;
  meta: Record<Instrument, FetchMeta | null>;
}

export interface AppStatus {
  message: string;
  isError: boolean;
}

interface Deps {
  eventBus: EventBus;
  fetcher: FireDataFetcher;
  processorRegistry: SensorProcessorRegistry;
  filterEngine: FireDataFilter;
  instruments: Instrument[];
  activeDataMode: string;
}

/**
 * Akışın orkestratörü: NASA FIRMS'ten (her cihaz için ayrı istek) CSV çek -> işle (K -> °C) -> süz -> yayınla.
 * Kullanıcı arayüzü yalnızca olay yolu (EventBus) ile konuşur.
 */
export class FireMapAppController {
  private allPoints: FirePoint[] = [];
  private meta: Record<Instrument, FetchMeta | null> = { VIIRS: null, MODIS: null };
  private abort: AbortController | null = null;
  private unsubs: Array<() => void> = [];
  private started = false;

  constructor(private readonly d: Deps) {}

  async start() {
    this.subscribe();
    await this.loadDataset({ refresh: false });
  }

  destroy() {
    this.abort?.abort();
    this.unsubs.forEach((u) => u());
    this.unsubs = [];
  }

  async loadDataset({ refresh }: { refresh: boolean }) {
    const { eventBus, fetcher, processorRegistry, filterEngine, instruments, activeDataMode: mode } = this.d;
    this.abort?.abort();
    const ctl = new AbortController();
    this.abort = ctl;
    const names = instruments.join(" + ");

    eventBus.emit<AppStatus>(EVENTS.APP_STATUS, { message: `1/3 NASA FIRMS (${names} - ${mode}) verisi çekiliyor...`, isError: false });
    try {
      const results = await Promise.all(
        instruments.map((instrument) => fetcher.fetchRawCsv({ instrument, dataMode: mode, refresh, signal: ctl.signal })),
      );
      if (ctl.signal.aborted) return;

      eventBus.emit<AppStatus>(EVENTS.APP_STATUS, { message: "2/3 Veriler işleniyor (Kelvin -> °C)...", isError: false });
      // Ana iş parçacığının nefes alması için bir tur bırak (durum satırı çizilsin).
      await new Promise((r) => setTimeout(r, 0));

      const all: FirePoint[] = [];
      let totalCsvRows = 0;
      const meta: Record<Instrument, FetchMeta | null> = { VIIRS: null, MODIS: null };
      instruments.forEach((instrument, i) => {
        const r = processorRegistry.get(instrument).process(results[i].csv, { dataMode: mode });
        for (const p of r.points) all.push(p);
        totalCsvRows += r.totalCsvRows;
        meta[instrument] = results[i].meta;
      });
      this.allPoints = all;
      this.meta = meta;
      filterEngine.initializeFromDataset(all);

      const filterState = filterEngine.getState();
      const filtered = filterEngine.apply(all);
      eventBus.emit<DatasetLoaded>(EVENTS.DATASET_LOADED, {
        filterState,
        confidenceCounts: filterEngine.computeConfidenceCounts(all),
        filteredCount: filtered.length,
        totalCount: filterEngine.scope(all).length,
        meta,
      });
      this.publish(filtered, filterState);
      eventBus.emit<AppStatus>(EVENTS.APP_STATUS, {
        message: `${names} (${mode}) — ${filtered.length} / ${totalCsvRows} nokta gösteriliyor`,
        isError: false,
      });
    } catch (error) {
      if ((error as Error).name === "AbortError") return;
      console.error("Veri akışı hatası:", error);
      eventBus.emit<AppStatus>(EVENTS.APP_STATUS, { message: `Hata: ${(error as Error).message}`, isError: true });
    }
  }

  private subscribe() {
    if (this.started) return;
    this.started = true;
    const { eventBus, filterEngine } = this.d;
    this.unsubs.push(
      eventBus.on<{ confidenceKey: ConfidenceKey; isEnabled: boolean }>(EVENTS.UI_TOGGLE_CONFIDENCE, ({ confidenceKey, isEnabled }) => {
        filterEngine.setConfidenceEnabled(confidenceKey, isEnabled);
        this.republish();
      }),
      eventBus.on<{ minTemp: number; maxTemp: number }>(EVENTS.UI_CHANGE_TEMP_RANGE, (r) => {
        filterEngine.setTemperatureRange(r);
        this.republish();
      }),
      eventBus.on<{ instrument: InstrumentFilter }>(EVENTS.UI_CHANGE_INSTRUMENT, ({ instrument }) => {
        filterEngine.setInstrument(instrument);
        this.republish(true);
      }),
      eventBus.on(EVENTS.UI_RESET_FILTERS, () => {
        filterEngine.reset();
        this.republish();
      }),
    );
  }

  private republish(counts = false) {
    const { filterEngine, eventBus } = this.d;
    const filtered = filterEngine.apply(this.allPoints);
    const filterState = filterEngine.getState();
    if (counts) {
      eventBus.emit<DatasetLoaded>(EVENTS.DATASET_LOADED, {
        filterState,
        confidenceCounts: filterEngine.computeConfidenceCounts(this.allPoints),
        filteredCount: filtered.length,
        totalCount: filterEngine.scope(this.allPoints).length,
        meta: this.meta,
      });
    }
    this.publish(filtered, filterState);
    eventBus.emit<AppStatus>(EVENTS.APP_STATUS, {
      message: `${this.d.instruments.join(" + ")} (${this.d.activeDataMode}) — ${filtered.length} / ${filterEngine.scope(this.allPoints).length} nokta gösteriliyor`,
      isError: false,
    });
  }

  private publish(filtered: FirePoint[], filterState: FilterState) {
    const perInstrument: Record<Instrument, number> = { VIIRS: 0, MODIS: 0 };
    for (const p of filtered) perInstrument[p.instrument] += 1;
    this.d.eventBus.emit<FilterUpdate>(EVENTS.FILTER_UPDATED, {
      points: filtered,
      filterState,
      filteredCount: filtered.length,
      totalCount: this.d.filterEngine.scope(this.allPoints).length,
      perInstrument,
    });
  }
}
