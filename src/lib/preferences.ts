import { useSyncExternalStore } from "react";

export type Theme = "light" | "dark";
export type MapStyle = "auto" | "light" | "dark" | "satellite";
export type CalRamp = "default" | "colorblind";

export interface Prefs {
  theme: Theme;
  mapStyle: MapStyle;
  calRamp: CalRamp;
}

const KEY = "alaz.prefs";
const MAP_STYLES: MapStyle[] = ["auto", "light", "dark", "satellite"];
const CAL_RAMPS: CalRamp[] = ["default", "colorblind"];

function load(): Prefs {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) ?? "{}") as Partial<Prefs>;
    return {
      theme: raw.theme === "dark" ? "dark" : "light",
      mapStyle: MAP_STYLES.includes(raw.mapStyle as MapStyle) ? (raw.mapStyle as MapStyle) : "auto",
      calRamp: CAL_RAMPS.includes(raw.calRamp as CalRamp) ? (raw.calRamp as CalRamp) : "default",
    };
  } catch {
    return { theme: "light", mapStyle: "auto", calRamp: "default" };
  }
}

let prefs: Prefs = load();
const subs = new Set<() => void>();

function apply() {
  document.documentElement.dataset.theme = prefs.theme;
  document.documentElement.dataset.calRamp = prefs.calRamp;
}
apply(); // ilk boyamadan önce uygula (yanıp sönme olmasın)

export function setPrefs(patch: Partial<Prefs>) {
  prefs = { ...prefs, ...patch };
  try { localStorage.setItem(KEY, JSON.stringify(prefs)); } catch { /* gizli mod */ }
  apply();
  subs.forEach((f) => f());
}

export function usePrefs(): Prefs {
  return useSyncExternalStore(
    (cb) => { subs.add(cb); return () => { subs.delete(cb); }; },
    () => prefs,
  );
}

/** "auto" ise temaya göre Mapbox stil kimliği. */
export function mapStyleId(p: Prefs): "light-v11" | "dark-v11" | "satellite-streets-v12" {
  const s = p.mapStyle === "auto" ? p.theme : p.mapStyle;
  return s === "dark" ? "dark-v11" : s === "satellite" ? "satellite-streets-v12" : "light-v11";
}
