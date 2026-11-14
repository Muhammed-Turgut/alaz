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
import type { ArchiveStatus, BBox, LatLng, Scope, Sensor, Source } from "./types";
import { cx } from "./ui";
import styles from "./aether.module.css";

/** Türkiye kapsama kutusu (south, west, north, east) — arşiv area.json ile aynı. */
const ARCHIVE_TR_BBOX: BBox = [35.8, 25.5, 42.3, 45.0];

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

  const [source, setSource] = useState<Source>("archive");
  const days = 1;
  const [sensor, setSensor] = useState<Sensor>("all");
  const [roi, setRoi] = useState<LatLng[] | null>(null);
  const [drawing, setDrawing] = useState(false);

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
  const [archive, setArchive] = useState<ArchiveStatus | null>(null);

  const scope: Scope = useMemo(
    () => ({
      source,
      region: null,
      start: "",
      end: "",
      days,
      roi,
    }),
    [source, days, roi],
  );

  useEffect(() => {
    let alive = true;
    (async () => {
      let fails = 0;
      for (let i = 0; i < 200 && alive; i++) {
        let h: Awaited<ReturnType<typeof api.health>> | null = null;
        try {
          h = await api.health();
        } catch {
          h = null;
        }
        fails = h ? 0 : fails + 1;
        if (alive) setOffline(fails >= 3);
        if (h?.status === "error") {
          if (alive) setBootError("Motor başlatılamadı (`npm run api` terminalindeki günlüğe bak).");
          return;
        }
        if (h?.ready) {
          const arch = await api.archiveStatus("tr").catch(() => null);
          if (!alive) return;
          setArchive(arch);
          setReady(true);
          return;
        }
        await new Promise((res) => setTimeout(res, 1500));
      }
      if (alive) setBootError("Backend'e ulaşılamadı. `npm run api` ile Python sunucusunu başlat (port 8000).");
    })();
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    if (!ready || source !== "archive") return;
    let alive = true;
    const tick = () => {
      api.archiveStatus("tr")
        .then((a) => alive && setArchive(a))
        .catch(() => undefined);
    };
    tick();
    const id = window.setInterval(tick, 30_000);
    return () => { alive = false; window.clearInterval(id); };
  }, [ready, source]);

  useEffect(() => {
    if (!ready) return;
    if (fitNext.current) {
      fitNext.current = false;
      fitId.current += 1;
      setFit({ id: fitId.current, bbox: source === "archive" ? ARCHIVE_TR_BBOX : null });
    }
  }, [ready, source]);

  useEffect(() => {
    if (!ready || !target) { setInspector({ kind: "empty" }); return; }
    if (source === "archive") {
      if (roi) {
        setInspector({
          kind: "info",
          message: `${roi.length} köşeli alan seçildi. Takvim, kırılma, brifing ve model sekmeleri bu poligon üzerinden hesaplanır.`,
        });
      } else {
        setInspector({ kind: "empty" });
      }
      return;
    }
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
  }, [ready, target, roi, source, days]);

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
    setInspector({ kind: "empty" });
    fitNext.current = true;
    setSource(s);
  };
  const goTab = useCallback((t: Tab) => {
    setTab(t);
    setVisited((v) => new Set(v).add(t));
  }, []);
  const startDraw = useCallback(() => {
    goTab("map");
    // startRoi bir sonraki boyamada hazır olsun
    queueMicrotask(() => mapRef.current?.startRoi());
  }, [goTab]);
  const requestBriefing = () => {
    goTab("briefing");
    setBriefTick((n) => n + 1);
  };

  const isLive = source === "live";
  const isArchive = source === "archive";
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
    if (!archive?.available) return { kind: "warn", text: "Türkiye arşivi henüz yok. İndirme: backend içinde python -m src.data.firms_archive ..." };
    if (archive.partial) return { kind: "info", text: `Gerçek FIRMS arşivi iniyor (${archive.done_chunks}/${archive.total_chunks}). Poligon çizerek kısmi geçmişi analiz edebilirsin.` };
    // Yönlendirme yalnızca harita sekmesinde; diğer sekmelerin kendi boş durumları var.
    if (!roi && tab === "map") {
      return { kind: "info", text: "Türkiye kutusu içinde poligon çiz (≤ ~50.000 km²), ardından takvim / kırılma / brifing sekmelerine geç." };
    }
    if (!basemapOk) return { kind: "info", text: "Harita altlığı döşemelerine ulaşılamıyor (çevrimdışı). Altıgen katmanlar yine de çizilir." };
    return null;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isLive, isArchive, archive, roi, tab, live.status, live.loading, live.totalCount, live.meta, basemapOk]);

  const dataPill = isArchive
    ? {
        tone: archive?.available && !archive.partial ? "ok" : "warn",
        text: archive?.available
          ? `veri: GERÇEK FIRMS arşiv TR (${archive.done_chunks}/${archive.total_chunks})`
          : "veri: arşiv yok",
      }
    : !liveMetas.length
      ? { tone: "warn", text: live.status.isError ? "veri: ALINAMADI" : "veri: NASA FIRMS çekiliyor..." }
      : liveOk
        ? { tone: "ok", text: `veri: GERÇEK NASA FIRMS (${liveTiers.map((t) => (t === "cache" ? "önbellek ≤3 sa" : t)).join(", ")})` }
        : { tone: "warn", text: `veri: CANLI DEĞİL (${liveTiers.join(", ") || "yok"})` };

  const regionBox: BBox | null = isArchive ? ARCHIVE_TR_BBOX : null;

  const roiHint = drawing
    ? "Çizim: köşe eklemek için tıkla, çift tıkla veya Bitir'e bas. Esc iptal."
    : roi
      ? `${roi.length} köşeli alan seçili.${isArchive ? " Analiz sekmeleri bu poligonu kullanır." : ""}`
      : isArchive
        ? "Türkiye kutusu içinde poligon çiz (≤ ~50.000 km²)."
        : "Köşe eklemek için haritaya tıkla, kapatmak için çift tıkla.";

  if (!ready) {
    return (
      <div className={styles.boot}>
        <h2>AETHER-FIRE</h2>
        <p className={bootError ? styles.errText : styles.hint}>
          {bootError
            || (offline
              ? "Python sunucusuna ulaşılamıyor. Ayrı bir terminalde `npm run api` çalıştır (port 8000); sunucu açılınca bu sayfa kendiliğinden devam eder."
              : "Motor yükleniyor: arşiv ve model hazırlanıyor (~15 sn)...")}
        </p>
      </div>
    );
  }

  return (
    <div className={styles.page}>
      <Sidebar
        source={source}
        roiHint={roiHint} drawing={drawing} live={live} archive={archive}
        onSource={changeSource}
        onDraw={startDraw}
        onFinish={() => { mapRef.current?.finishRoi(); }}
        onClearRoi={clearRoi}
        onBriefing={requestBriefing}
      />
      <div className={styles.main}>
        <div className={styles.statusRow}>
          <span className={styles.pill}>
            {isLive
              ? `canlı harita · ${live.totalCount.toLocaleString("tr-TR")} FIRMS noktası (son 24 sa)`
              : `arşiv TR · ${archive?.done_chunks ?? "?"}/${archive?.total_chunks ?? "?"} dilim`}
          </span>
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
            mapRef={mapRef} live={isLive} archiveMode={isArchive}
            points={live.points} liveTotal={live.totalCount}
            livePerInstrument={live.perInstrument} sensor={sensor} onSensor={setSensor}
            payload={null} regionBox={regionBox} fit={fit}
            selected={target?.kind === "hex" ? target.id : null} loading={isLive ? live.loading : false} visible={tab === "map"}
            inspector={<Inspector state={inspector} onBriefing={requestBriefing} />}
            hasSelection={inspector.kind !== "empty"}
            onClose={() => (target?.kind === "roi" ? clearRoi() : setTarget(null))}
            onHexClick={(id) => setTarget({ kind: "hex", id })}
            onRoi={(pts) => { setRoi(pts); setDrawing(false); setTarget({ kind: "roi" }); }}
            onDrawing={setDrawing}
            onBasemap={setBasemapOk}
            onDrawRoi={startDraw}
          />
        </div>
        {visited.has("break") && (
          <div hidden={tab !== "break"}>
            <BreakTab
              key={`break-${source}-${roi?.map((p) => p.join(",")).join(";") ?? "noroi"}`}
              source={source}
              roi={roi}
              onDraw={startDraw}
            />
          </div>
        )}
        {visited.has("calendar") && (
          <div hidden={tab !== "calendar"}>
            <CalendarTab
              key={`cal-${source}-${roi?.map((p) => p.join(",")).join(";") ?? "noroi"}`}
              visible={tab === "calendar"}
              source={source}
              roi={roi}
              onDraw={startDraw}
            />
          </div>
        )}
        {visited.has("explain") && (
          <div hidden={tab !== "explain"}>
            <ExplainTab
              key={`explain-${source}-${roi?.map((p) => p.join(",")).join(";") ?? "noroi"}`}
              visible={tab === "explain"}
              source={source}
              roi={roi}
            />
          </div>
        )}
        {visited.has("briefing") && (
          <div hidden={tab !== "briefing"}>
            <BriefingTab scope={scope} tick={briefTick} onDraw={startDraw} />
          </div>
        )}
        {visited.has("model") && (
          <div hidden={tab !== "model"}>
            <ModelTab
              key={`model-${source}-${roi?.map((p) => p.join(",")).join(";") ?? "noroi"}`}
              source={source}
              roi={roi}
              onDraw={startDraw}
            />
          </div>
        )}
      </div>
    </div>
  );
}
