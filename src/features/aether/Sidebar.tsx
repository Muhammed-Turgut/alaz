import { useEffect, useState } from "react";

import { Button } from "@/components/ui/Button";
import { presetsFor } from "./format";
import { APP_CONFIG, type ConfidenceKey } from "./live/config";
import type { LiveFires } from "./live/useLiveFires";
import type { Region, Source } from "./types";
import { Seg } from "./ui";
import { viz } from "./viz";
import styles from "./aether.module.css";

interface Props {
  regions: Region[];
  source: Source;
  region: string;
  start: string;
  end: string;
  roiHint: string;
  drawing: boolean;
  live: LiveFires;
  onSource: (s: Source) => void;
  onRegion: (r: string) => void;
  onWindow: (start: string, end: string) => void;
  onDraw: () => void;
  onFinish: () => void;
  onClearRoi: () => void;
  onBriefing: () => void;
}

export function Sidebar(p: Props) {
  const [s, setS] = useState(p.start);
  const [e, setE] = useState(p.end);
  const [err, setErr] = useState("");
  useEffect(() => { setS(p.start); setE(p.end); }, [p.start, p.end]);
  const presets = presetsFor(p.region);

  const apply = () => {
    if (!s || !e) return setErr("Başlangıç ve bitiş tarihini seç.");
    if (s > e) return setErr("Başlangıç, bitişten sonra olamaz.");
    setErr("");
    p.onWindow(s, e);
  };

  const lv = p.live;
  const metas = [lv.meta.VIIRS, lv.meta.MODIS].filter((x) => x !== null);
  const tiers = [...new Set(metas.flatMap((x) => x.tiers))];
  const newest = metas.map((x) => x.newest).filter(Boolean).sort().at(-1);
  const oldest = metas.map((x) => x.oldest).filter(Boolean).sort()[0];
  const keyOn = metas.some((x) => x.keyConfigured);
  return (
    <aside className={styles.sidebar}>
      <section className={styles.ctl}>
        <h4>Veri kaynağı</h4>
        <Seg value={p.source} onChange={p.onSource}
          options={[{ v: "live", label: "Canlı NASA FIRMS" }, { v: "benchmark", label: "Benchmark 2001-2026" }]} />
        <p className={styles.hint}>
          {p.source === "benchmark"
            ? "Bilinen gerçeğe sahip, fiziksel olarak simüle edilmiş arşiv. NASA gözlemi değildir."
            : "NASA FIRMS yakın-gerçek-zamanlı (son 24 saat) MODIS + VIIRS tespitleri. Çevrimdışıyken önbellek / paketli gerçek örneğe düşer ve bu her zaman belirtilir; sentetik veri gösterilmez."}
        </p>
      </section>

      {p.source === "benchmark" ? (
        <section className={styles.ctl}>
          <h4>Bölge ve pencere</h4>
          <label>Bölge
            <select value={p.region} onChange={(ev) => p.onRegion(ev.target.value)}>
              {p.regions.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
            </select>
          </label>
          <label>Hazır pencere
            <select value="" onChange={(ev) => { const pr = presets[Number(ev.target.value)]; if (pr) p.onWindow(pr.start, pr.end); }}>
              <option value="" disabled>Seç...</option>
              {presets.map((pr, i) => <option key={pr.label} value={i}>{pr.label}</option>)}
            </select>
          </label>
          <div className={styles.row2}>
            <label>Başlangıç<input type="date" value={s} min="2001-01-01" max="2026-11-14" onChange={(ev) => setS(ev.target.value)} /></label>
            <label>Bitiş<input type="date" value={e} min="2001-01-01" max="2026-11-14" onChange={(ev) => setE(ev.target.value)} /></label>
          </div>
          <Button variant="secondary" className={styles.full} onClick={apply}>Pencereyi uygula</Button>
          {err && <p className={styles.errText}>{err}</p>}
        </section>
      ) : (
        <>
          <section className={styles.ctl}>
            <h4>Canlı veri</h4>
            <p className={`${styles.statusLine} ${lv.status.isError ? styles.statusErr : ""}`} role="status">{lv.status.message || "-"}</p>
            <Button variant="secondary" className={styles.full} onClick={() => lv.reload(true)} disabled={lv.loading}>
              {lv.loading ? "Çekiliyor..." : "NASA FIRMS'ten yenile"}
            </Button>
            <div className={styles.metaBox}>
              {metas.length
                ? `katman: ${tiers.join(", ") || "yok"}\nen yeni gözlem: ${newest ?? "-"} UTC\nen eski gözlem: ${oldest ?? "-"} UTC\nanahtar: ${keyOn ? "tanımlı" : "yok"}\notomatik yenileme: ${Math.round(APP_CONFIG.DATA_SOURCE.REFRESH_INTERVAL_MS / 60000)} dk`
                : "-"}
            </div>
          </section>
          <LiveFilters live={lv} />
        </>
      )}

      <section className={styles.ctl}>
        <h4>İlgi alanı (ROI)</h4>
        <div className={styles.row2}>
          <Button variant="secondary" onClick={p.onDraw}>Poligon çiz</Button>
          <Button variant="secondary" onClick={p.onClearRoi}>Temizle</Button>
        </div>
        {p.drawing && <Button variant="secondary" className={styles.full} onClick={p.onFinish}>Poligonu bitir (veya çift tıkla)</Button>}
        <p className={styles.hint}>{p.roiHint}</p>
      </section>

      <section className={styles.ctl}>
        <h4>Rapor</h4>
        <Button className={styles.full} onClick={p.onBriefing}>Komutan brifingi üret</Button>
      </section>

      <p className={styles.foot}>
        Ham sayımlar her platformun tespitlerini içerir. Harmonize değerler, platform başına VIIRS-375 m eşdeğeri sıcak noktalardır; kalıcı endüstriyel / volkanik kaynaklar çıkarılmıştır.
      </p>
    </aside>
  );
}

const CONF_ROWS: ConfidenceKey[] = ["HIGH", "NOMINAL", "LOW"];

function LiveFilters({ live }: { live: LiveFires }) {
  const f = live.filter;
  const c = viz();
  const step = APP_CONFIG.FILTER.TEMP_STEP_CELSIUS;
  if (!f) return null;
  const { minTemp: lo, maxTemp: hi } = f.currentRange;
  return (
    <section className={styles.ctl}>
      <h4>Süzgeçler</h4>
      {CONF_ROWS.map((k) => (
        <label key={k} className={styles.confRow}>
          <input type="checkbox" checked={f.enabledConfidences.has(k)} onChange={(ev) => live.toggleConfidence(k, ev.target.checked)} />
          <span className={styles.swatch} style={{ background: c.conf[k] }} />
          {APP_CONFIG.CONFIDENCE_LABELS[k]}
          <span className={styles.confN}>{live.counts[k].toLocaleString("tr-TR")}</span>
        </label>
      ))}
      <div className={styles.rangeBox}>
        <label>Parlaklık sıcaklığı alt sınırı
          <input type="range" min={f.bounds.minTemp} max={f.bounds.maxTemp} step={step} value={lo}
            onChange={(ev) => live.setTempRange(Number(ev.target.value), hi)} />
        </label>
        <label>Üst sınır
          <input type="range" min={f.bounds.minTemp} max={f.bounds.maxTemp} step={step} value={hi}
            onChange={(ev) => live.setTempRange(lo, Number(ev.target.value))} />
        </label>
        <div className={styles.rangeVals}><span>{lo.toFixed(1)} &deg;C</span><span>{hi.toFixed(1)} &deg;C</span></div>
      </div>
      <Button variant="secondary" className={styles.full} onClick={live.resetFilters}>Süzgeçleri sıfırla</Button>
    </section>
  );
}
