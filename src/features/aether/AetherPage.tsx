import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api } from "./api";
import { BreakTab } from "./BreakTab";
import { BriefingTab } from "./BriefingTab";
import { CalendarTab } from "./CalendarTab";
import { ExplainTab } from "./ExplainTab";
import { Inspector, type InspectorState } from "./Inspector";
import { MapTab, type FitCommand } from "./MapTab";
import { useLiveFires } from "./live/useLiveFires";
import type { MapView } from "./mapView";
import { ModelTab } from "./ModelTab";
import { Sidebar } from "./Sidebar";
import { presetsFor } from "./format";
import type { BBox, HexPayload, LatLng, Region, Scope, Sensor, Source } from "./types";
import { cx } from "./ui";
import styles from "./aether.module.css";

type Tab = "map" | "break" | "calendar" | "explain" | "briefing" | "model";
const TABS: { id: Tab; label: string }[] = [
  { id: "map", label: "Görev haritası" },
  { id: "break", label: "Yapısal kırılma" },
  { id: "calendar", label: "Yanma takvimi" },
  { id: "explain", label: "Sıcak nokta açıklayıcı" },
  { id: "briefing", label: "Brifing" },
  { id: "model", label: "Model ve doğrulama" },
];

type Target = { kind: "hex"; id: string } | { kind: "roi" } | null;

export function AetherPage() {
  const [ready, setReady] = useState(false);
  const [bootError, setBootError] = useState("");
  const [offline, setOffline] = useState(false);
  const [regions, setRegions] = useState<Region[]>([]);
  const [engine, setEngine] = useState("");

  const first = presetsFor("california")[0];
  const [source, setSource] = useState<Source>("benchmark");
  const [region, setRegion] = useState("california");
  const [start, setStart] = useState(first.start);
  const [end, setEnd] = useState(first.end);
  const days = 1; // canlı pencere: NASA FIRMS NRT son 24 saat
  const [sensor, setSensor] = useState<Sensor>("all");
  const [roi, setRoi] = useState<LatLng[] | null>(null);
  const [drawing, setDrawing] = useState(false);

  const [payload, setPayload] = useState<HexPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [basemapOk, setBasemapOk] = useState(true);
  const [fit, setFit] = useState<FitCommand | null>(null);
  const fitNext = useRef(true);
  const fitId = useRef(0);

  const [target, setTarget] = useState<Target>(null);
  const [inspector, setInspector] = useState<InspectorState>({ kind: "empty" });
  const [tab, setTab] = useState<Tab>("map");
  const [visited, setVisited] = useState<Set<Tab>>(new Set(["map"]));
  const [briefTick, setBriefTick] = useState(0);
  const mapRef = useRef<MapView | null>(null);
  const live = useLiveFires(ready && source === "live", sensor);

  const scope: Scope = useMemo(
    () => ({ source, region, start, end, days, roi }),
    [source, region, start, end, days, roi],
  );

  // ---------------------------------------------------------------- açılış
  useEffect(() => {
    let alive = true;
    (async () => {
      let fails = 0;
      for (let i = 0; i < 200 && alive; i++) {
        let h: Awaited<ReturnType<typeof api.health>> | null = null;
        try {
          h = await api.health();
        } catch {
          h = null; // backend henüz ayakta değil: beklemeye devam
        }
        fails = h ? 0 : fails + 1;
        if (alive) setOffline(fails >= 3);
        if (h?.status === "error") {
          if (alive) setBootError("Motor başlatılamadı (aether-fire günlüğüne bak).");
          return;
        }
        if (h?.ready) {
          const r = await api.regions();
          if (!alive) return;
          setRegions(r);
          setEngine(`${h.model} · ${(h.detections / 1e6).toFixed(2)} M tespit`);
          setReady(true);
          return;
        }
        await new Promise((res) => setTimeout(res, 1500));
      }
      if (alive) setBootError("Backend'e ulaşılamadı. `python run.py` ile aether-fire sunucusunu başlat (port 8000).");
    })();
    return () => { alive = false; };
  }, []);

  // ---------------------------------------------------------------- harita verisi
  useEffect(() => {
    if (!ready) return;
    if (source === "live") {
      // canlı mod H3 altıgen yüklemez: noktalar /api/fires üzerinden (useLiveFires) gelir
      setLoading(false);
      setLoadError("");
      if (fitNext.current) {
        fitNext.current = false;
        fitId.current += 1;
        setFit({ id: fitId.current, bbox: null });
      }
      return;
    }
    let alive = true;
    setLoading(true);
    api.hexes(scope, false)
      .then((p) => {
        if (!alive) return;
        setPayload(p);
        setLoadError("");
        if (fitNext.current) {
          fitNext.current = false;
          const reg = regions.find((r) => r.id === region);
          fitId.current += 1;
          setFit({ id: fitId.current, bbox: reg ? (reg.bbox as BBox) : null });
        }
      })
      .catch((e: Error) => alive && setLoadError(`Harita verisi yüklenemedi: ${e.message}`))
      .finally(() => alive && setLoading(false));
    return () => { alive = false; };
    // scope alanları ayrı ayrı bağımlılık; roi pencere yüklemesini etkilemez
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, source, region, start, end]);

  // ---------------------------------------------------------------- denetçi
  useEffect(() => {
    if (!ready || !target) { setInspector({ kind: "empty" }); return; }
    let alive = true;
    if (target.kind === "hex") {
      setInspector({ kind: "loading", label: `${target.id} yükleniyor...` });
      api.hex(target.id, scope)
        .then((d) => alive && setInspector({ kind: "hex", data: d }))
        .catch((e: Error) => alive && setInspector({ kind: "error", message: e.message }));
    } else if (roi) {
      setInspector({ kind: "loading", label: "İlgi alanı analiz ediliyor..." });
      api.roi(scope)
        .then((d) => alive && setInspector({ kind: "roi", data: d }))
        .catch((e: Error) => alive && setInspector({ kind: "error", message: e.message }));
    }
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, target, roi, source, region, start, end, days]);

  // ---------------------------------------------------------------- olaylar
  const clearRoi = useCallback(() => {
    mapRef.current?.clearRoi();
    setRoi(null);
    setDrawing(false);
    setTarget((t) => (t && t.kind === "roi" ? null : t));
  }, []);

  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === "Escape") clearRoi(); };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [clearRoi]);

  const changeSource = (s: Source) => {
    clearRoi();
    setTarget(null);
    fitNext.current = true;
    setSource(s);
  };
  const changeRegion = (r: string) => {
    clearRoi();
    setTarget(null);
    fitNext.current = true;
    const p = presetsFor(r)[0];
    setRegion(r);
    setStart(p.start);
    setEnd(p.end);
  };
  const goTab = (t: Tab) => {
    setTab(t);
    setVisited((v) => new Set(v).add(t));
  };
  const requestBriefing = () => {
    goTab("briefing");
    setBriefTick((n) => n + 1);
  };

  const isLive = source === "live";
  const liveMetas = [live.meta.VIIRS, live.meta.MODIS].filter((m) => m !== null);
  const liveTiers = [...new Set(liveMetas.flatMap((m) => m.tiers))];
  const liveOk = liveMetas.length > 0 && liveMetas.every((m) => m.live);
  const banner = useMemo(() => {
    if (isLive) {
      if (live.status.isError) return { kind: "warn", text: `${live.status.message}. Python sunucusu (npm run api) açık mı ve internet var mı?` };
      if (liveMetas.length && !liveOk) return { kind: "warn", text: `NASA FIRMS'e şu an ulaşılamıyor; ${liveTiers.join(", ") || "yedek"} katmanındaki GERÇEK veri gösteriliyor (canlı değil).` };
      if (!live.loading && live.status.message && live.totalCount === 0 && liveMetas.length) return { kind: "info", text: "Seçili sensör için son 24 saatte tespit yok." };
      return null;
    }
    if (loadError) return { kind: "warn", text: loadError };
    if (payload && payload.cells.length === 0) return { kind: "info", text: "Bu pencerede tespit yok. Başka bir hazır pencere veya bölge dene." };
    if (payload?.truncated) {
      const n = (payload.totals as { n_cells?: number }).n_cells ?? 0;
      return { kind: "info", text: `En yoğun ${payload.cells.length.toLocaleString("en-US")} altıgen gösteriliyor; KPI'lar tüm ${n.toLocaleString("en-US")} aktif altıgeni kapsar.` };
    }
    if (!basemapOk) return { kind: "info", text: "Harita altlığı döşemelerine ulaşılamıyor (çevrimdışı). Altıgen katmanlar yine de çizilir." };
    return null;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadError, isLive, live.status, live.loading, live.totalCount, live.meta, payload, basemapOk]);

  const dataPill = !isLive
    ? { tone: "warn", text: "veri: SİMÜLE benchmark" }
    : !liveMetas.length
      ? { tone: "warn", text: live.status.isError ? "veri: ALINAMADI" : "veri: NASA FIRMS çekiliyor..." }
      : liveOk
        ? { tone: "ok", text: `veri: GERÇEK NASA FIRMS (${liveTiers.map((t) => (t === "cache" ? "önbellek ≤3 sa" : t)).join(", ")})` }
        : { tone: "warn", text: `veri: CANLI DEĞİL (${liveTiers.join(", ") || "yok"})` };

  const regionBox: BBox | null = useMemo(() => {
    if (source !== "benchmark") return null;
    const r = regions.find((x) => x.id === region);
    return r ? (r.bbox as BBox) : null;
  }, [source, region, regions]);

  const roiHint = drawing
    ? "Çizim: köşe eklemek için tıkla, çift tıkla veya Bitir'e bas. Esc iptal."
    : roi ? `${roi.length} köşeli ROI.` : "Köşe eklemek için haritaya tıkla, kapatmak için çift tıkla.";

  if (!ready) {
    return (
      <div className={styles.boot}>
        <h2>AETHER-FIRE</h2>
        <p className={bootError ? styles.errText : styles.hint}>
          {bootError
            || (offline
              ? "Python sunucusuna ulaşılamıyor. Ayrı bir terminalde `npm run api` çalıştır (port 8000); sunucu açılınca bu sayfa kendiliğinden devam eder."
              : "Motor yükleniyor: arşivler, model ve kalıcı kaynak kataloğu hazırlanıyor (~15 sn)...")}
        </p>
      </div>
    );
  }

  return (
    <div className={styles.page}>
      <Sidebar
        regions={regions} source={source} region={region} start={start} end={end}
        roiHint={roiHint} drawing={drawing} live={live}
        onSource={changeSource} onRegion={changeRegion}
        onWindow={(s, e) => { setStart(s); setEnd(e); }}
        onDraw={() => { goTab("map"); mapRef.current?.startRoi(); }}
        onFinish={() => { mapRef.current?.finishRoi(); }}
        onClearRoi={clearRoi}
        onBriefing={requestBriefing}
      />
      <div className={styles.main}>
        <div className={styles.statusRow}>
          <span className={styles.pill}>motor: hazır · {engine}</span>
          <span className={cx(styles.pill, dataPill.tone === "ok" ? styles.pillOk : styles.pillWarn)}>{dataPill.text}</span>
        </div>
        <nav className={styles.tabs} aria-label="AETHER-FIRE bölümleri">
          {TABS.map((t) => (
            <button key={t.id} type="button" className={cx(tab === t.id && styles.on)} onClick={() => goTab(t.id)}>{t.label}</button>
          ))}
        </nav>
        {banner && <div className={cx(styles.banner, banner.kind === "info" && styles.bannerInfo)}>{banner.text}</div>}

        <div hidden={tab !== "map"}>
          <MapTab
            mapRef={mapRef} live={isLive} points={live.points} liveTotal={live.totalCount}
            livePerInstrument={live.perInstrument} sensor={sensor} onSensor={setSensor}
            payload={payload} regionBox={regionBox} fit={fit}
            selected={target?.kind === "hex" ? target.id : null} loading={isLive ? live.loading : loading} visible={tab === "map"}
            inspector={<Inspector state={inspector} onBriefing={requestBriefing} />}
            hasSelection={inspector.kind !== "empty"}
            onClose={() => (target?.kind === "roi" ? clearRoi() : setTarget(null))}
            onHexClick={(id) => setTarget({ kind: "hex", id })}
            onRoi={(pts) => { setRoi(pts); setDrawing(false); setTarget({ kind: "roi" }); }}
            onDrawing={setDrawing}
            onBasemap={setBasemapOk}
          />
        </div>
        {visited.has("break") && <div hidden={tab !== "break"}><BreakTab regions={regions} /></div>}
        {visited.has("calendar") && <div hidden={tab !== "calendar"}><CalendarTab regions={regions} visible={tab === "calendar"} /></div>}
        {visited.has("explain") && <div hidden={tab !== "explain"}><ExplainTab visible={tab === "explain"} /></div>}
        {visited.has("briefing") && <div hidden={tab !== "briefing"}><BriefingTab scope={scope} tick={briefTick} /></div>}
        {visited.has("model") && <div hidden={tab !== "model"}><ModelTab /></div>}
      </div>
    </div>
  );
}
