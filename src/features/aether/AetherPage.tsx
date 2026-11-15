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
import { DisclaimerBanner } from "./DisclaimerBanner";
import { DemoGuide } from "./DemoGuide";
import { Sidebar } from "./Sidebar";
import { useDataSource } from "./sourceContext";
import type { ArchiveStatus, BBox, LatLng, Scope, Sensor, Source } from "./types";
import { cx } from "./ui";
import { useT } from "@/lib/i18n";
import styles from "./aether.module.css";

type Tab = "map" | "break" | "calendar" | "explain" | "briefing" | "model";
const TAB_IDS: Tab[] = ["map", "break", "calendar", "explain", "briefing", "model"];

type Target = { kind: "hex"; id: string } | { kind: "roi" } | null;

export function AetherPage() {
  const t = useT();
  const en = t.lang === "en";
  const dataSource = useDataSource();
  const source = dataSource?.source ?? "archive";

  const [ready, setReady] = useState(false);
  const [bootError, setBootError] = useState("");
  const [offline, setOffline] = useState(false);

  const days = 1;
  const prevSource = useRef<Source>(source);
  const [sensor, setSensor] = useState<Sensor>("all");
  const [roi, setRoi] = useState<LatLng[] | null>(null);
  const [drawing, setDrawing] = useState(false);
  const [csvBusy, setCsvBusy] = useState(false);

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
      // World view — ROI may be drawn anywhere (local TR disk or remote FIRMS API).
      setFit({ id: fitId.current, bbox: null });
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

  // Üst bar kaynak değişince ROI / seçimi temizle
  useEffect(() => {
    if (prevSource.current === source) return;
    prevSource.current = source;
    mapRef.current?.clearRoi();
    setRoi(null);
    setDrawing(false);
    setTarget(null);
    setInspector({ kind: "empty" });
    fitNext.current = true;
  }, [source]);

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

  const exportHotspotsCsv = useCallback(async () => {
    if (!roi || roi.length < 3) return;
    if (source !== "live" && source !== "archive") return;
    setCsvBusy(true);
    try {
      const { text, n, truncated } = await api.exportHotspotsCsv({
        source,
        polygon: roi,
        days,
      });
      const url = URL.createObjectURL(new Blob([text], { type: "text/csv;charset=utf-8" }));
      const a = Object.assign(document.createElement("a"), {
        href: url,
        download: `alaz-hotspots-${source}-${n}.csv`,
      });
      a.click();
      URL.revokeObjectURL(url);
      if (truncated) {
        setInspector({
          kind: "info",
          message: `CSV ilk ${n.toLocaleString("tr-TR")} satırla sınırlandı.`,
        });
      }
    } catch (e) {
      setInspector({ kind: "error", message: (e as Error).message });
    } finally {
      setCsvBusy(false);
    }
  }, [roi, source, days]);

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
    if (!archive?.available && !archive?.remote_api_configured) {
      return { kind: "warn", text: "Yerel Türkiye arşivi yok ve FIRMS_MAP_KEY tanımlı değil — küresel ROI için anahtar gerekir. Yerel: python -m src.data.firms_archive ..." };
    }
    if (archive?.partial) return { kind: "info", text: `Yerel TR arşivi iniyor (${archive.done_chunks}/${archive.total_chunks}). Dünya geneli ROI çizilebilir (TR yerel hızlı; dışı uzaktan API).` };
    // Yönlendirme yalnızca harita sekmesinde; diğer sekmelerin kendi boş durumları var.
    if (!roi && tab === "map") {
      return { kind: "info", text: "Dünya geneli poligon çiz (≤ ~120.000 km²) veya enlem/boylam gir. TR yerel disk hızlı; dışı on-demand FIRMS API." };
    }
    if (!basemapOk) return { kind: "info", text: "Harita altlığı döşemelerine ulaşılamıyor (çevrimdışı). Altıgen katmanlar yine de çizilir." };
    return null;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isLive, isArchive, archive, roi, tab, live.status, live.loading, live.totalCount, live.meta, basemapOk]);

  const tierLabel = (t: string) => {
    if (t === "cache") return en ? "fresh cache" : "taze önbellek";
    if (t === "stale-cache") return en ? "stale cache" : "eski önbellek";
    if (t === "open-feed") return en ? "open feed" : "açık besleme";
    if (t === "api") return "API";
    if (t === "bundled-sample") return en ? "bundled sample" : "paketli örnek";
    return t;
  };
  const dataPill = isArchive
    ? {
        tone: (archive?.available && !archive.partial) || archive?.remote_api_configured ? "ok" : "warn",
        text: archive?.available
          ? `veri: FIRMS arşiv (TR yerel + küresel API) (${archive.done_chunks}/${archive.total_chunks})`
          : archive?.remote_api_configured
            ? "veri: küresel FIRMS API (yerel TR yok)"
            : "veri: arşiv/API yok",
      }
    : !liveMetas.length
      ? { tone: "warn", text: live.status.isError ? "veri: ALINAMADI" : "veri: NASA FIRMS çekiliyor..." }
      : liveOk
        ? { tone: "ok", text: `veri: CANLI NASA FIRMS (${liveTiers.map(tierLabel).join(", ")})` }
        : {
            tone: "warn",
            text: `veri: CANLI DEĞİL (${liveTiers.map(tierLabel).join(", ") || "yok"})`
              + (liveMetas[0]?.newest ? ` · en yeni gözlem ${liveMetas[0].newest}` : ""),
          };

  // No dashed TR coverage box — it looked like a hard selection limit.
  const regionBox: BBox | null = null;

  const roiHint = drawing
    ? "Çizim: köşe eklemek için tıkla, çift tıkla veya Bitir'e bas. Esc iptal."
    : roi
      ? `${roi.length} köşeli alan seçili.${isArchive ? " Analiz sekmeleri bu poligonu kullanır." : ""}`
      : isArchive
        ? "Dünya geneli poligon çiz (≤ ~120.000 km²) veya enlem/boylam/yarıçap gir. TR yerel disk varsa hızlı; dışı on-demand FIRMS API (MAP_KEY)."
        : "Köşe eklemek için haritaya tıkla, kapatmak için çift tıkla. Enlem/boylam da uygulanır.";

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
        roi={roi}
        csvBusy={csvBusy}
        onDraw={startDraw}
        onFinish={() => { mapRef.current?.finishRoi(); }}
        onClearRoi={clearRoi}
        onBriefing={requestBriefing}
        onRoiBox={(pts) => {
          goTab("map");
          setRoi(pts);
          setDrawing(false);
          setTarget({ kind: "roi" });
          // Harita bir sonraki boyamada hazır olsun
          queueMicrotask(() => mapRef.current?.setRoiPolygon(pts, { fit: true }));
        }}
        onExportCsv={() => void exportHotspotsCsv()}
      />
      <div className={styles.main}>
        <div className={styles.statusRow}>
          <span className={cx(styles.pill, styles.pillOk)}>
            {isLive ? t.pillLive : t.pillPilot}
          </span>
          <span className={styles.pill}>
            {isLive
              ? `canlı harita · ${live.totalCount.toLocaleString("tr-TR")} FIRMS noktası`
              : `arşiv · TR yerel ${archive?.done_chunks ?? "?"}/${archive?.total_chunks ?? "?"} · küresel ROI açık`}
          </span>
          <span className={cx(styles.pill, dataPill.tone === "ok" ? styles.pillOk : styles.pillWarn)}>{dataPill.text}</span>
        </div>
        <DisclaimerBanner />
        <DemoGuide
          sourceIsArchive={isArchive}
          onLoadDemoRoi={(pts) => {
            goTab("map");
            setRoi(pts);
            setDrawing(false);
            setTarget({ kind: "roi" });
            queueMicrotask(() => mapRef.current?.setRoiPolygon(pts, { fit: true }));
          }}
        />
        <nav className={styles.tabs} aria-label="AETHER-FIRE sections">
          {TAB_IDS.map((id) => (
            <button key={id} type="button" className={cx(tab === id && styles.on)} onClick={() => goTab(id)}>{t.tabs[id]}</button>
          ))}
        </nav>
        {banner && <div className={cx(styles.banner, banner.kind === "info" && styles.bannerInfo)}>{banner.text}</div>}

        <div hidden={tab !== "map"}>
          <MapTab
            mapRef={mapRef} live={isLive} archiveMode={isArchive} roi={roi}
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
            remoteMaxYears={archive?.remote_max_years ?? 10}
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
