export type Source = "live" | "archive";
export type Sensor = "all" | "MODIS" | "VIIRS";
export type BBox = [number, number, number, number]; // south, west, north, east
export type LatLng = [number, number];

export interface Region {
  id: string;
  name: string;
  regime: string;
  bbox: BBox;
  center: LatLng;
  detections: number;
  static_cells: number;
  events: number;
  range: [string, string];
}

export interface HexCell {
  h3: string;
  boundary: LatLng[];
  raw_total: number;
  raw_modis: number;
  raw_viirs: number;
  static_n: number;
  static: boolean;
  harmonized: number;
  lo90: number;
  hi90: number;
  harm_modis: number;
  viirs_native: number;
  frp_raw_mean: number;
  frp_harm_mean: number;
}

export interface HexTotals {
  raw_total: number;
  raw_modis: number;
  raw_viirs: number;
  static_removed: number;
  lowconf_removed: number;
  harmonized: number;
  harmonized_lo90: number;
  harmonized_hi90: number;
  n_cells: number;
  static_cells: number;
}

export interface LiveMeta {
  tiers?: string[];
  live?: boolean;
  newest?: string | null;
  oldest?: string | null;
  rows?: number;
  key_configured?: boolean;
  warning?: string;
  platforms?: { MODIS: number; VIIRS: number };
}

export interface HexPayload {
  cells: HexCell[];
  truncated: boolean;
  totals: HexTotals | Record<string, never>;
  rule: string;
  source: Source;
  meta?: LiveMeta;
}

export interface DriverEffect {
  driver: string;
  mean_effect_pct: number;
  p10: number;
  p90: number;
}
export interface DriverSummary {
  n_rows: number;
  frp: DriverEffect[];
  n: DriverEffect[];
}

export interface DetectionExplain {
  time: string;
  satellite: string;
  lat: number;
  lon: number;
  frp_obs: number;
  footprint_km2: number;
  bowtie_x: number;
  lst: number;
  diurnal_factor: number;
  frp_harm_p10: number;
  frp_harm_p50: number;
  frp_harm_p90: number;
  frp_lo90: number;
  frp_hi90: number;
  n_p50: number;
  n_lo90: number;
  n_hi90: number;
  scale_factor: number;
  drivers: { driver: string; effect_pct: number }[];
}

export interface DiurnalSummary {
  mean_lst?: number;
  peak_hour?: number;
  reference_day?: number;
  reference_night?: number;
}

export type SatStat = number | { n: number; frp_mean: number; area_mean: number };

export interface HexDetail {
  h3: string;
  empty: boolean;
  lat: number;
  lon: number;
  area_km2: number;
  biome: string;
  totals: {
    raw_total: number; raw_modis: number; raw_viirs: number; static_n: number; lowconf_n: number;
    harmonized: number; lo: number; hi: number; harm_modis: number; viirs_native: number;
    frp_raw_mean: number; frp_harm_mean: number;
  };
  by_satellite: Record<string, SatStat>;
  diurnal: DiurnalSummary;
  top_modis_detections: DetectionExplain[];
  drivers: DriverSummary;
}

export interface RoiResult {
  empty: boolean;
  n_cells_in_roi: number;
  roi_area_km2: number;
  static_cells?: number;
  totals?: {
    raw_total: number; raw_modis: number; raw_viirs: number; static_removed: number; lowconf_removed: number;
    harmonized: number; harmonized_lo90: number; harmonized_hi90: number;
    frp_raw_mean: number | null; frp_harm_mean: number | null;
  };
  by_biome?: Record<string, { harmonized: number; raw: number }>;
  by_satellite?: Record<string, number>;
  timeseries?: { labels: string[]; raw: number[]; harmonized: number[] };
  drivers?: DriverSummary;
}

export interface SeriesPayload {
  labels: string[];
  raw_all: (number | null)[];
  raw_filtered: (number | null)[];
  raw_modis: (number | null)[];
  raw_viirs: (number | null)[];
  harmonized: (number | null)[];
  harmonized_lo: (number | null)[];
  harmonized_hi: (number | null)[];
  truth_snpp_everywhere: (number | null)[];
}

export interface TestResult { p_value: number; significant: boolean; change_date?: string }
export interface BreakReport {
  pettitt_monthly: TestResult;
  pettitt_local: TestResult;
  pettitt_annual: TestResult;
  chow_monthly: TestResult;
  chow_annual: TestResult;
  step: { pre_mean: number | null; post_mean: number | null; step_pct: number | null };
}
export interface BreakPayload {
  region: string;
  reports: Record<"raw_all" | "raw_filtered" | "harmonized" | "truth", BreakReport>;
  verdict: {
    notes: string[];
    raw_break_at_transition: boolean;
    harmonized_step_eliminated: boolean;
    pvalue_caveat?: string;
  };
  empty?: boolean;
  message?: string;
  meta?: AreaCalendarResult["meta"];
  local_validation?: Record<string, number | string | boolean | null>;
}

export interface CalendarPayload {
  years: number[];
  doy: number[];
  matrix: (number | null)[][];
  envelope: { p10: (number | null)[]; p50: (number | null)[]; p90: (number | null)[]; mean: (number | null)[] };
  target_year: number;
  target_series: (number | null)[];
  anomalies: {
    n_days: number; n_positive: number; n_negative: number; max_z: number | null;
    episodes: { start: string; end: string; days: number; peak_z: number }[];
  };
}

export interface MonthYearMatrix {
  years: number[];
  months: number[];
  matrix: (number | null)[][];
  totals_by_month: number[];
}

export interface MonthVsMedian {
  available: boolean;
  year?: number;
  month?: number;
  month_total?: number;
  historical_median?: number | null;
  ratio?: number | null;
  anomaly_pct?: number | null;
  percentile?: number | null;
  n_years?: number;
  historical_p10?: number;
  historical_p90?: number;
}

export interface HybridAlphaInfo {
  value: number;
  r2?: number | null;
  n_months: number;
  fallback_used: boolean;
  fallback_reason?: string | null;
  bounds?: [number, number];
  fallback_alpha?: number;
  fitted_alpha?: number;
  calib_window?: [string, string];
}

export interface HybridMonthlyRow {
  year: number;
  month: number;
  raw_modis: number;
  raw_viirs: number;
  applied_alpha: number;
  harmonized_activity: number;
  historical_median: number | null;
  anomaly_pct: number | null;
}

export interface AreaHybridResult {
  empty: boolean;
  message?: string;
  hygiene?: { raw_count: number; kept: number; dropped: number; filter?: string };
  grid?: string;
  alpha?: HybridAlphaInfo;
  monthly?: HybridMonthlyRow[];
  transition_chart?: { label: string; raw_modis: number; raw_viirs: number; harmonized: number }[];
  map?: {
    harmonized_cells: DensityPoint[];
    viirs_points: {
      lat: number; lon: number; ts: string; frp: number;
      confidence?: number | null; satellite?: string;
    }[];
    window?: [string, string];
  };
  anomaly_card?: {
    year?: number; month?: number;
    harmonized_activity?: number;
    historical_median?: number | null;
    anomaly_pct?: number | null;
  };
  fallback_badge?: boolean;
  fallback_message_tr?: string | null;
  fallback_message_en?: string | null;
  note?: string;
  meta?: AreaCalendarResult["meta"];
}

export interface AreaCalendarResult {
  empty: boolean;
  message?: string;
  series?: "harmonized" | "raw" | "hybrid";
  hybrid?: AreaHybridResult | null;
  meta?: {
    area_km2?: number;
    rows?: number;
    raw_count?: number;
    detections?: number;
    date_range?: [string | null, string | null];
    view_years?: [number, number];
    target_year?: number;
    state?: string;
    done_chunks?: number;
    total_chunks?: number;
    partial?: boolean;
    model?: string;
    model_note?: string;
    counts?: { raw_all: number; raw_modis: number; raw_viirs: number; static_removed: number };
    product_composition?: Record<string, number>;
    product_kind_counts?: Record<string, number>;
    satellites?: Record<string, number>;
    nrt_tail_note?: string | null;
    empty_reason?: string | null;
    times_utc?: boolean;
    timeline_markers?: { id: string; date: string; label: string }[];
  };
  calendar?: CalendarPayload;
  month_year?: MonthYearMatrix;
  current_month_vs_median?: MonthVsMedian;
  local_validation?: Record<string, number | string | boolean | null>;
}

export interface ArchiveStatus {
  area_id: string;
  name?: string;
  available: boolean;
  state: string;
  done_chunks: number;
  total_chunks: number;
  failed_chunks?: number;
  bbox?: { west: number; south: number; east: number; north: number };
  max_area_km2?: number;
  rows_cached?: number;
  partial?: boolean;
  /** FIRMS_MAP_KEY present — on-demand global ROI outside local TR disk. */
  remote_api_configured?: boolean;
  remote_max_years?: number;
  global_roi?: boolean;
  local_coverage_note?: string;
}

/** Archive multi-year FIRMS hotspot density (H3 res-7 polygons). */
export interface DensityPoint {
  lat: number;
  lon: number;
  n: number;
  frp_sum: number;
  w: number;
  h3: string;
  /** [[lat, lon], ...] ring for polygon fill. */
  boundary?: LatLng[];
  platforms?: Record<string, number>;
  instruments?: Record<string, number>;
}

export interface DensityLegendBin {
  level: string;
  weight?: number;
  min_n?: number;
  max_n?: number;
  approx_n_per_cell?: number;
  label_tr: string;
  label_en: string;
}

export interface AreaDensityResult {
  empty: boolean;
  message?: string;
  /** Effective years used for window/legend (may be capped for remote ROI). */
  years: number;
  requested_years?: number;
  effective_years?: number;
  window_start?: string | null;
  window_end?: string | null;
  year_note?: string | null;
  instrument: string;
  window?: [string | null, string | null];
  archive_span?: [string | null, string | null];
  n_detections?: number;
  n_raw_in_window?: number;
  excluded_lowconf_or_static?: number;
  n_cells?: number;
  n_max_per_cell?: number;
  cell_area_km2_approx?: number;
  aggregation?: string;
  render?: "h3_polygon" | "kernel_heatmap";
  meaning?: string;
  points: DensityPoint[];
  legend: {
    title_tr: string;
    title_en: string;
    unit_tr: string;
    unit_en: string;
    not_tr: string;
    not_en: string;
    window?: [string | null, string | null];
    n_max_per_cell?: number;
    n_total?: number;
    render?: string;
    bins: DensityLegendBin[];
  };
  meta?: AreaCalendarResult["meta"] & {
    partial?: boolean;
    done_chunks?: number;
    total_chunks?: number;
    failed_chunks?: number;
    chunks_fetched_now?: number;
    chunks_from_cache?: number;
    truncated?: boolean;
    local_archive?: boolean;
    history_years_cap?: number;
    first_download?: boolean;
    cache_complete?: boolean;
  };
}

export interface DiurnalModel {
  params: { peak_hour: number; sigma_hours: number; floor: number; day_reference_hour: number; night_reference_hour: number; clip: number[] };
  curve: { lst: number[]; relative_intensity: number[] };
  overpass_drift: {
    years: number[];
    Terra: number[];
    Aqua: number[];
    SNPP: number[];
    NOAA20: number[];
    NOAA21?: number[];
  };
}

interface TargetMetrics {
  mae_log_model: number; mae_log_naive_linear: number; mae_improvement_pct: number; coverage_90_conformal: number;
}
export interface ModelPayload {
  version: string;
  metrics: { n_test: number; per_target: { n: TargetMetrics; frp: TargetMetrics } };
  importance: { frp: Record<string, number>; n: Record<string, number> };
  matchups: {
    n_matchups: number; train_years: number[]; calib_years: number[]; test_years: number[];
    frac_modis_with_viirs_counterpart: number;
  };
  kappa: number;
  diurnal: DiurnalModel;
}

export interface ValidationBlock {
  rmse_raw: number; rmse_harmonized: number; relative_bias_raw_pct: number; relative_bias_harmonized_pct: number;
  correlation_harmonized: number; ci90_coverage: number;
}
export interface StaticFilterStats {
  precision: number; recall: number; true_static_rows: number; false_positive_rows: number;
}
export interface ValidationPayload {
  kappa: number;
  regions: Record<string, {
    synthetic_holdout_2022_2026?: ValidationBlock;
    synthetic_counterfactual_2001_2011?: ValidationBlock;
    holdout_2022_2026?: ValidationBlock;
    counterfactual_2001_2011?: ValidationBlock;
  }>;
  static_filter: Record<string, StaticFilterStats | Record<string, number>>;
}

export interface StaticSources {
  detected: { region: string; cell: string; kind: string; median_days_per_year: number; years_flagged: number; scenario_source: string | null }[];
}

export interface GeometryPayload {
  scan_angle_deg: number[];
  modis: { scan_km: number[]; track_km: number[]; area_km2: number[]; expansion: number[]; half_swath_km: number };
  viirs: { scan_km: number[]; track_km: number[]; area_km2: number[]; expansion: number[]; half_swath_km: number };
  h3: { res7_km2: number; res8_km2: number };
  note: string;
}

export interface HarmonizedDetection {
  satellite: string; h3_res7: string; h3_res8: string; biome: string; footprint_km2: number;
  local_solar_time: number; diurnal_factor: number; frp_diurnal_normalized: number;
  is_static: boolean; low_confidence: boolean; frp_obs: number;
  frp_p10: number; frp_p50: number; frp_p90: number; n_p50: number; n_lo90: number; n_hi90: number;
  drivers: { driver: string; effect_pct: number }[];
}

export interface BriefingResult {
  markdown: string;
  json: Record<string, unknown>;
}

export interface Health {
  status: string; ready: boolean; model: string | null; detections: number; regions: string[];
}

export type BriefingAudience = "acil" | "erken_uyari" | "arazi" | "bilim" | "itfaiye";

export interface Scope {
  source: Source;
  region: string | null;
  start: string;
  end: string;
  days: number;
  roi: LatLng[] | null;
}
