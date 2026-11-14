/** Canlı NASA FIRMS akışının sabitleri. Renkler burada TUTULMAZ: tüm renkler `tokens.css`'ten okunur. */
export const EVENTS = Object.freeze({
  APP_STATUS: "app:status",
  DATASET_LOADED: "dataset:loaded",
  FILTER_UPDATED: "filter:updated",
  UI_TOGGLE_CONFIDENCE: "ui:toggleConfidence",
  UI_CHANGE_TEMP_RANGE: "ui:changeTempRange",
  UI_CHANGE_INSTRUMENT: "ui:changeInstrument",
  UI_RESET_FILTERS: "ui:resetFilters",
});

export type Instrument = "VIIRS" | "MODIS";
export type ConfidenceKey = "HIGH" | "NOMINAL" | "LOW" | "UNKNOWN";

export const APP_CONFIG = Object.freeze({
  DATA_SOURCE: {
    API_ENDPOINT: "/api/fires",
    INSTRUMENTS: ["VIIRS", "MODIS"] as Instrument[],
    DEFAULT_DATA_MODE: "REALTIME",
    FETCH_TIMEOUT_MS: 60000,
    REFRESH_INTERVAL_MS: 10 * 60 * 1000,
    MAX_RECORDS: null as number | null,
  },
  PHYSICS: { KELVIN_TO_CELSIUS_OFFSET: 273.15 },
  CONFIDENCE_LABELS: {
    HIGH: "Yüksek (High)",
    NOMINAL: "Normal (Nominal)",
    LOW: "Düşük (Low)",
    UNKNOWN: "Bilinmiyor",
  } as Record<ConfidenceKey, string>,
  FILTER: {
    ACTIVE_CONFIDENCE_KEYS: ["HIGH", "NOMINAL", "LOW"] as ConfidenceKey[],
    TEMP_STEP_CELSIUS: 0.5,
  },
  MAP: {
    CLUSTER: { MAX_RADIUS: 50, MAX_ZOOM: 9, MEDIUM_THRESHOLD: 25, LARGE_THRESHOLD: 150 },
    LABEL_MIN_ZOOM: 8,
  },
});
