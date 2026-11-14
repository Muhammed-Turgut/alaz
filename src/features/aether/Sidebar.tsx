import { Button } from "@/components/ui/Button";
import { APP_CONFIG, type ConfidenceKey } from "./live/config";
import type { LiveFires } from "./live/useLiveFires";
import type { ArchiveStatus, Source } from "./types";
import { Seg } from "./ui";
import { viz } from "./viz";
import styles from "./aether.module.css";

interface Props {
  source: Source;
  roiHint: string;
  drawing: boolean;
  live: LiveFires;
  archive: ArchiveStatus | null;
  onSource: (s: Source) => void;
  onDraw: () => void;
  onFinish: () => void;
  onClearRoi: () => void;
  onBriefing: () => void;
}

export function Sidebar(p: Props) {
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
        <Seg
          value={p.source}
          onChange={p.onSource}
          options={[
            { v: "live", label: "Canlı NASA FIRMS" },
            { v: "archive", label: "Gerçek arşiv (TR)" },
          ]}
        />
        <p className={styles.hint}>
          {p.source === "archive"
            ? "Türkiye kapsamasındaki gerçek NASA FIRMS MODIS+VIIRS geçmişi. Haritada alan çiz → Yanma takvimi."
            : "NASA FIRMS yakın-gerçek-zamanlı (son 24 saat) MODIS + VIIRS tespitleri. Çevrimdışıyken önbellek / paketli gerçek örneğe düşer; sentetik veri gösterilmez."}
        </p>
      </section>

      {p.source === "live" && (
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

      {p.source === "archive" && (
        <section className={styles.ctl}>
          <h4>Gerçek arşiv</h4>
          <div className={styles.metaBox}>
            {p.archive?.available
              ? `${p.archive.name ?? "Türkiye"}\n${p.archive.partial ? "indiriliyor" : "hazır"} · ${p.archive.done_chunks}/${p.archive.total_chunks} dilim\nmaks. alan ≈ ${(p.archive.max_area_km2 ?? 50000).toLocaleString("tr-TR")} km²`
              : "Arşiv henüz yok veya yüklenemedi."}
          </div>
          <p className={styles.hint}>
            Haritada poligon çiz. Takvim, kırılma, brifing ve model bu alan üzerinden hesaplanır.
          </p>
        </section>
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
        <label>Parlaklık sıcaklığı alt (°C, K−273)
          <input type="range" min={f.bounds.minTemp} max={f.bounds.maxTemp} step={step} value={lo}
            onChange={(ev) => live.setTempRange(Number(ev.target.value), hi)} />
        </label>
        <label>Üst sınır (°C)
          <input type="range" min={f.bounds.minTemp} max={f.bounds.maxTemp} step={step} value={hi}
            onChange={(ev) => live.setTempRange(lo, Number(ev.target.value))} />
        </label>
        <div className={styles.rangeVals}><span>{lo.toFixed(1)} &deg;C</span><span>{hi.toFixed(1)} &deg;C</span></div>
      </div>
      <Button variant="secondary" className={styles.full} onClick={live.resetFilters}>Süzgeçleri sıfırla</Button>
    </section>
  );
}
