import { useCallback, useEffect, useRef, useState } from "react";

import { FireMapAppController, type AppStatus, type DatasetLoaded, type FilterUpdate } from "./appController";
import { APP_CONFIG, EVENTS, type ConfidenceKey, type Instrument } from "./config";
import { EventBus } from "./eventBus";
import { FireDataFetcher, type FetchMeta } from "./fetcher";
import { FireDataFilter, type FilterState, type InstrumentFilter } from "./filterEngine";
import { FireCsvProcessor, SensorProcessorRegistry, type FirePoint } from "./processor";

export interface LiveFires {
  status: AppStatus;
  loading: boolean;
  points: FirePoint[];
  filter: FilterState | null;
  counts: Record<ConfidenceKey, number>;
  totalCount: number;
  filteredCount: number;
  perInstrument: Record<Instrument, number>;
  meta: Record<Instrument, FetchMeta | null>;
  loadedAt: Date | null;
  reload: (refresh: boolean) => void;
  toggleConfidence: (key: ConfidenceKey, on: boolean) => void;
  setTempRange: (minTemp: number, maxTemp: number) => void;
  resetFilters: () => void;
}

const ZERO_COUNTS: Record<ConfidenceKey, number> = { HIGH: 0, NOMINAL: 0, LOW: 0, UNKNOWN: 0 };

function buildRegistry() {
  const base = {
    kelvinOffset: APP_CONFIG.PHYSICS.KELVIN_TO_CELSIUS_OFFSET,
    confidenceLabels: APP_CONFIG.CONFIDENCE_LABELS,
    maxRecords: APP_CONFIG.DATA_SOURCE.MAX_RECORDS,
  };
  const reg = new SensorProcessorRegistry();
  reg.register("VIIRS", new FireCsvProcessor({ ...base, instrument: "VIIRS", temperatureColumn: "bright_ti4" }));
  reg.register("MODIS", new FireCsvProcessor({ ...base, instrument: "MODIS", temperatureColumn: "brightness" }));
  return reg;
}

/**
 * NASA FIRMS gerçek zamanlı akışını React'e bağlar. `enabled` kapalıyken ağ isteği ve zamanlayıcı yoktur.
 * Sensör (hepsi / MODIS / VIIRS) seçimi istemci tarafında süzgeç olarak uygulanır; yeniden indirme gerektirmez.
 */
export function useLiveFires(enabled: boolean, instrument: InstrumentFilter): LiveFires {
  const [status, setStatus] = useState<AppStatus>({ message: "", isError: false });
  const [points, setPoints] = useState<FirePoint[]>([]);
  const [filter, setFilter] = useState<FilterState | null>(null);
  const [counts, setCounts] = useState(ZERO_COUNTS);
  const [totalCount, setTotal] = useState(0);
  const [perInstrument, setPer] = useState<Record<Instrument, number>>({ VIIRS: 0, MODIS: 0 });
  const [meta, setMeta] = useState<Record<Instrument, FetchMeta | null>>({ VIIRS: null, MODIS: null });
  const [loadedAt, setLoadedAt] = useState<Date | null>(null);

  const busRef = useRef<EventBus | null>(null);
  const ctlRef = useRef<FireMapAppController | null>(null);
  const instrumentRef = useRef(instrument);
  instrumentRef.current = instrument;

  useEffect(() => {
    if (!enabled) return;
    const bus = new EventBus();
    const engine = new FireDataFilter({ allowedConfidences: APP_CONFIG.FILTER.ACTIVE_CONFIDENCE_KEYS });
    engine.setInstrument(instrumentRef.current);
    const ctl = new FireMapAppController({
      eventBus: bus,
      fetcher: new FireDataFetcher({
        apiEndpoint: APP_CONFIG.DATA_SOURCE.API_ENDPOINT,
        timeoutMs: APP_CONFIG.DATA_SOURCE.FETCH_TIMEOUT_MS,
      }),
      processorRegistry: buildRegistry(),
      filterEngine: engine,
      instruments: APP_CONFIG.DATA_SOURCE.INSTRUMENTS,
      activeDataMode: APP_CONFIG.DATA_SOURCE.DEFAULT_DATA_MODE,
    });
    busRef.current = bus;
    ctlRef.current = ctl;

    const offs = [
      bus.on<AppStatus>(EVENTS.APP_STATUS, setStatus),
      bus.on<DatasetLoaded>(EVENTS.DATASET_LOADED, (d) => {
        setCounts(d.confidenceCounts);
        setMeta(d.meta);
        setFilter(d.filterState);
        setLoadedAt(new Date());
      }),
      bus.on<FilterUpdate>(EVENTS.FILTER_UPDATED, (u) => {
        setPoints(u.points);
        setFilter(u.filterState);
        setTotal(u.totalCount);
        setPer(u.perInstrument);
      }),
    ];
    void ctl.start();
    const timer = window.setInterval(() => void ctl.loadDataset({ refresh: false }), APP_CONFIG.DATA_SOURCE.REFRESH_INTERVAL_MS);

    return () => {
      window.clearInterval(timer);
      offs.forEach((o) => o());
      ctl.destroy();
      busRef.current = null;
      ctlRef.current = null;
    };
  }, [enabled]);

  useEffect(() => {
    busRef.current?.emit(EVENTS.UI_CHANGE_INSTRUMENT, { instrument });
  }, [instrument, enabled]);

  const reload = useCallback((refresh: boolean) => void ctlRef.current?.loadDataset({ refresh }), []);
  const toggleConfidence = useCallback(
    (confidenceKey: ConfidenceKey, isEnabled: boolean) => busRef.current?.emit(EVENTS.UI_TOGGLE_CONFIDENCE, { confidenceKey, isEnabled }),
    [],
  );
  const setTempRange = useCallback((minTemp: number, maxTemp: number) => busRef.current?.emit(EVENTS.UI_CHANGE_TEMP_RANGE, { minTemp, maxTemp }), []);
  const resetFilters = useCallback(() => busRef.current?.emit(EVENTS.UI_RESET_FILTERS), []);

  const loading = !status.isError && /^[12]\/3/.test(status.message);
  const filteredCount = points.length;
  return { status, loading, points, filter, counts, totalCount, filteredCount, perInstrument, meta, loadedAt, reload, toggleConfidence, setTempRange, resetFilters };
}
