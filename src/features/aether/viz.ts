/*
 * Görselleştirme renkleri: HİÇBİR renk kodu burada yazılmaz.
 * Hepsi src/styles/tokens.css içindeki değişkenlerden okunur; palet değişince otomatik güncellenir.
 */

let probe: CanvasRenderingContext2D | null = null;

/** color-mix() gibi tarayıcıya özgü ifadeleri düz #rrggbb / rgba() biçimine çevirir (Mapbox/Canvas için). */
function normalize(value: string): string {
  if (!value || value.startsWith("#") || value.startsWith("rgb")) return value;
  if (!probe) {
    const c = document.createElement("canvas");
    c.width = c.height = 1;
    probe = c.getContext("2d", { willReadFrequently: true });
  }
  if (!probe) return value;
  probe.clearRect(0, 0, 1, 1);
  probe.fillStyle = value;
  probe.fillRect(0, 0, 1, 1);
  const [r, g, b] = probe.getImageData(0, 0, 1, 1).data;
  return `rgb(${r},${g},${b})`;
}

function cssVar(name: string): string {
  return normalize(getComputedStyle(document.documentElement).getPropertyValue(name).trim());
}

export interface VizColors {
  raw: string; harmonized: string; truth: string; fire: string; warn: string; roi: string; selected: string;
  atmosLow: string; atmosHigh: string; space: string;
  text: string; muted: string; surface: string; subtle: string; border: string; mapBg: string;
  ramp: string[];
  calRamp: string[];
  conf: { HIGH: string; NOMINAL: string; LOW: string; UNKNOWN: string };
  cluster: { small: string; medium: string };
  sat: { Terra: string; Aqua: string; SNPP: string; NOAA20: string; NOAA21: string };
}

export function viz(): VizColors {
  const calPrefix =
    document.documentElement.dataset.calRamp === "colorblind" ? "--viz-cal-cb-" : "--viz-cal-";
  return {
    raw: cssVar("--viz-raw"),
    harmonized: cssVar("--viz-harmonized"),
    truth: cssVar("--viz-truth"),
    fire: cssVar("--viz-fire"),
    warn: cssVar("--viz-warn"),
    roi: cssVar("--viz-roi"),
    selected: cssVar("--viz-selected"),
    space: cssVar("--viz-space"),
    atmosLow: cssVar("--viz-atmos-low"),
    atmosHigh: cssVar("--viz-atmos-high"),
    text: cssVar("--color-text"),
    muted: cssVar("--color-text-muted"),
    surface: cssVar("--color-surface"),
    subtle: cssVar("--color-bg-subtle"),
    border: cssVar("--color-border"),
    mapBg: cssVar("--viz-map-bg"),
    conf: {
      HIGH: cssVar("--viz-conf-high"),
      NOMINAL: cssVar("--viz-conf-nominal"),
      LOW: cssVar("--viz-conf-low"),
      UNKNOWN: cssVar("--viz-conf-unknown"),
    },
    cluster: { small: cssVar("--viz-cluster-small"), medium: cssVar("--viz-cluster-medium") },
    calRamp: [0, 1, 2, 3, 4].map((i) => cssVar(`${calPrefix}${i}`)),
    ramp: [0, 1, 2, 3, 4].map((i) => cssVar(`--viz-ramp-${i}`)),
    sat: {
      Terra: cssVar("--viz-sat-terra"),
      Aqua: cssVar("--viz-sat-aqua"),
      SNPP: cssVar("--viz-sat-snpp"),
      NOAA20: cssVar("--viz-sat-noaa20"),
      NOAA21: cssVar("--viz-sat-noaa21"),
    },
  };
}

export function hexToRgb(hex: string): [number, number, number] {
  let h = hex.replace("#", "").trim();
  if (h.length === 3) h = h.split("").map((c) => c + c).join("");
  const n = parseInt(h, 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

/** Herhangi bir token renginden (hex) saydam varyant üretir. */
export function alpha(hex: string, a: number): string {
  const [r, g, b] = hexToRgb(hex);
  return `rgba(${r},${g},${b},${a})`;
}

/** Sürekli rampa: t in [0,1] -> rgb. Tokenlardan bir kez ayrıştırılır. */
export function makeRamp(colors = viz().ramp): (t: number) => string {
  const stops = colors.map(hexToRgb);
  const last = stops.length - 1;
  return (t: number) => {
    const x = Math.max(0, Math.min(1, t)) * last;
    const i = Math.min(Math.floor(x), last - 1);
    const u = x - i;
    const c = stops[i].map((v, k) => Math.round(v + (stops[i + 1][k] - v) * u));
    return `rgb(${c[0]},${c[1]},${c[2]})`;
  };
}

export function rampGradientCss(colors = viz().ramp): string {
  return `linear-gradient(90deg, ${colors.join(", ")})`;
}
