export function fmt(v: number | null | undefined, d = 0): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "-";
  return Number(v).toLocaleString("tr-TR", { maximumFractionDigits: d, minimumFractionDigits: 0 });
}

/** p-değerini okunaklı göster: çok küçükse "< 0,001". */
export function fmtP(p: number | null | undefined): string {
  if (p === null || p === undefined) return "-";
  if (p < 0.001) return "< 0,001";
  return p.toLocaleString("tr-TR", { minimumFractionDigits: 3, maximumFractionDigits: 3 });
}

export function signed(v: number | null | undefined, d = 0): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "-";
  return `${v > 0 ? "+" : ""}${v.toFixed(d)}`;
}

const pad = (n: number) => String(n).padStart(2, "0");

/** Bölgeye göre tipik yangın tepe penceresi: [ay, gün] başlangıç / bitiş. */
const PEAK: Record<string, [[number, number], [number, number]]> = {
  california: [[8, 10], [8, 25]],
  anatolia: [[7, 20], [8, 5]],
  amazon: [[8, 20], [9, 5]],
  australia: [[1, 5], [1, 20]],
};

export interface Preset { label: string; start: string; end: string }

export function presetsFor(region: string): Preset[] {
  const [a, b] = PEAK[region] ?? [[8, 1], [8, 15]];
  const mk = (y: number, label: string): Preset => ({
    label,
    start: `${y}-${pad(a[0])}-${pad(a[1])}`,
    end: `${y}-${pad(b[0])}-${pad(b[1])}`,
  });
  return [
    mk(2025, "Tepe penceresi 2025 (VIIRS dönemi)"),
    { label: "Arşivin son 14 günü", start: "2026-11-01", end: "2026-11-14" },
    mk(2020, "Tepe penceresi 2020 (kayan Terra/Aqua)"),
    mk(2012, "Tepe penceresi 2012 (sensör geçiş yılı)"),
    mk(2005, "Tepe penceresi 2005 (yalnız MODIS)"),
    mk(2002, "Tepe penceresi 2002 (yalnız Terra ağırlıklı)"),
  ];
}
