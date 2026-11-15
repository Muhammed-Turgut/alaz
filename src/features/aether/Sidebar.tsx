import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/Button";
import { APP_CONFIG, type ConfidenceKey } from "./live/config";
import type { LiveFires } from "./live/useLiveFires";
import type { ArchiveStatus, LatLng, Source } from "./types";
import { Glossary } from "./Glossary";
import { viz } from "./viz";
import styles from "./aether.module.css";

/** Arşiv üst sınırı ile uyumlu (km²) — archive_store.MAX_AREA_KM2 */
const MAX_ROI_KM2 = 120_000;

interface Props {
  source: Source;
  roiHint: string;
  drawing: boolean;
  live: LiveFires;
  archive: ArchiveStatus | null;
  roi: LatLng[] | null;
  csvBusy?: boolean;
  onDraw: () => void;
  onFinish: () => void;
  onClearRoi: () => void;
  onBriefing: () => void;
  onRoiBox: (pts: LatLng[]) => void;
  /** Haritada seçili poligona git (lat/lon varsayılanını dayatma). */
  onFocusRoi?: (pts: LatLng[]) => void;
  onExportCsv: () => void;
}

/** Merkez + yarıçap (km) → kapalı dikdörtgen poligon [[lat,lon],...] */
export function boxFromCenter(lat: number, lon: number, radiusKm: number): LatLng[] {
  const dLat = radiusKm / 111.32;
  const cos = Math.cos((lat * Math.PI) / 180);
  const dLon = radiusKm / (111.32 * Math.max(0.2, Math.abs(cos)));
  return [
    [lat - dLat, lon - dLon],
    [lat - dLat, lon + dLon],
    [lat + dLat, lon + dLon],
    [lat + dLat, lon - dLon],
    [lat - dLat, lon - dLon],
  ];
}

export function approxBoxKm2(lat: number, _lon: number, radiusKm: number): number {
  const dLat = radiusKm / 111.32;
  const cos = Math.cos((lat * Math.PI) / 180);
  const dLon = radiusKm / (111.32 * Math.max(0.2, Math.abs(cos)));
  const dy = 2 * dLat * 111.32;
  const dx = 2 * dLon * 111.32 * Math.max(0.05, Math.abs(cos));
  return Math.abs(dx * dy);
}

/** En fazla ~√(MAX) / 2 kenar — TR enleminde güvenli yarıçap */
export function maxRadiusKm(lat: number): number {
  const cos = Math.max(0.2, Math.abs(Math.cos((lat * Math.PI) / 180)));
  // kaba: alan ≈ (2r)² → r ≈ √(MAX)/2
  const r = Math.sqrt(MAX_ROI_KM2) / 2;
  return Math.floor(Math.min(180, r * Math.sqrt(cos)));
}

export function Sidebar(p: Props) {
  const lv = p.live;
  const metas = [lv.meta.VIIRS, lv.meta.MODIS].filter((x) => x !== null);
  const tiers = [...new Set(metas.flatMap((x) => x.tiers))];
  const newest = metas.map((x) => x.newest).filter(Boolean).sort().at(-1);
  const oldest = metas.map((x) => x.oldest).filter(Boolean).sort()[0];
  const keyOn = metas.some((x) => x.keyConfigured);
  const canCsv = Boolean(p.roi && p.roi.length >= 3);

  // Empty defaults — do NOT pre-fill Ankara/central TR (was overwriting drawn polygons).
  const [lat, setLat] = useState("");
  const [lon, setLon] = useState("");
  const [radius, setRadius] = useState("25");
  const [roiErr, setRoiErr] = useState("");
  const [roiOk, setRoiOk] = useState("");
  const [touched, setTouched] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastApplied = useRef("");
  const hasDrawnRoi = Boolean(p.roi && p.roi.length >= 3);

  // Poligon çizildiyse enlem/boylam hatasını temizle — koordinat zorunlu değil
  useEffect(() => {
    if (!hasDrawnRoi) return;
    setRoiErr("");
    setRoiOk(`${p.roi!.length} köşeli poligon hazır — enlem/boylam gerekmez.`);
  }, [hasDrawnRoi]); // eslint-disable-line react-hooks/exhaustive-deps

  const applyLatLon = (opts?: { silent?: boolean }) => {
    const la = Number(lat);
    const lo = Number(lon);
    const r = Number(radius);
    if (lat.trim() === "" || lon.trim() === "") {
      if (!opts?.silent) {
        if (hasDrawnRoi) {
          setRoiErr("");
          setRoiOk("Poligon zaten seçili. Enlem/boylam yalnızca alternatif yöntem.");
        } else {
          setRoiErr("Enlem/boylam boş. Haritada poligon çizmen yeterli — koordinat zorunlu değil.");
          setRoiOk("");
        }
      }
      return false;
    }
    if (!Number.isFinite(la) || la < -90 || la > 90) {
      setRoiErr("Geçerli enlem gir (−90…90).");
      setRoiOk("");
      return false;
    }
    if (!Number.isFinite(lo) || lo < -180 || lo > 180) {
      setRoiErr("Geçerli boylam gir (−180…180).");
      setRoiOk("");
      return false;
    }
    if (!Number.isFinite(r) || r < 1) {
      setRoiErr("Yarıçap en az 1 km olmalı.");
      setRoiOk("");
      return false;
    }
    const cap = maxRadiusKm(la);
    const useR = Math.min(r, cap);
    const km2 = approxBoxKm2(la, lo, useR);
    if (km2 > MAX_ROI_KM2) {
      setRoiErr(`Alan ~${Math.round(km2).toLocaleString("tr-TR")} km² — üst sınır ${MAX_ROI_KM2.toLocaleString("tr-TR")} km². Yarıçapı ≤ ${cap} km yap.`);
      setRoiOk("");
      return false;
    }
    const key = `${la.toFixed(4)},${lo.toFixed(4)},${useR}`;
    if (key === lastApplied.current && opts?.silent) return true;
    lastApplied.current = key;
    setRoiErr("");
    if (useR < r) {
      setRadius(String(useR));
      setRoiOk(`Yarıçap ${useR} km’ye indirildi (alan sınırı). ROI haritada güncellendi.`);
    } else {
      setRoiOk(`ROI uygulandı (~${Math.round(km2).toLocaleString("tr-TR")} km²).`);
    }
    p.onRoiBox(boxFromCenter(la, lo, useR));
    return true;
  };

  const goToDrawnRoi = () => {
    if (!p.roi || p.roi.length < 3) {
      setRoiErr("Önce haritada poligon çiz (çift tıkla veya Bitir).");
      setRoiOk("");
      return;
    }
    setRoiErr("");
    setRoiOk("Poligon kullanılıyor — haritada odaklanıldı.");
    p.onFocusRoi?.(p.roi);
  };

  // Kullanıcı alanları değiştirdikten sonra otomatik uygula (yalnızca dokunulduysa)
  useEffect(() => {
    if (!touched) return;
    if (lat.trim() === "" || lon.trim() === "") return;
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      applyLatLon({ silent: true });
    }, 450);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lat, lon, radius, touched]);

  return (
    <aside className={styles.sidebar}>
      <section className={styles.ctl}>
        <h4>Veri kaynağı</h4>
        <p className={styles.hint}>
          Kaynağı üst sağdan seç. <b>Tarihsel arşiv:</b> dünya geneli ROI (TR = yerel disk; dışı FIRMS API).
          {" "}
          <b>Canlı harita:</b> dünya geneli son 24 saat FIRMS.
          {p.source === "archive"
            ? " Şu an arşiv — ROI ile takvim / kırılma / brifing."
            : " Şu an canlı — global noktalar."}
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
              ? `${p.archive.name ?? "Türkiye"} (yerel)\n${p.archive.partial ? "indiriliyor" : "hazır"} · ${p.archive.done_chunks}/${p.archive.total_chunks} dilim\nmaks. alan ≈ ${(p.archive.max_area_km2 ?? MAX_ROI_KM2).toLocaleString("tr-TR")} km²\nküresel ROI: ${p.archive.remote_api_configured ? "API hazır" : "MAP_KEY yok"}${p.archive.remote_max_years ? ` · uzaktan ≤${p.archive.remote_max_years}y` : ""}`
              : p.archive?.remote_api_configured
                ? `Yerel TR yok — küresel ROI FIRMS API ile (≤${p.archive.remote_max_years ?? 10} yıl)`
                : "Yerel arşiv yok; küresel ROI için FIRMS_MAP_KEY gerekli."}
          </div>
          <p className={styles.hint}>
            Haritada <b>dünya geneli</b> poligon çizmen yeterli (enlem/boylam şart değil).
            Türkiye yerel disk arşivi varsa o ROI hızlı yüklenir; dışı on-demand FIRMS API (MAP_KEY gerekir).
          </p>
        </section>
      )}

      <section className={styles.ctl}>
        <h4>İlgi alanı (ROI)</h4>
        <p className={styles.hint}>
          Ana yol: haritada <b>poligon çiz</b> → bitince analiz sekmeleri onu kullanır.
          Enlem/boylam yalnızca istersen alternatif.
        </p>
        <div className={styles.row2}>
          <Button variant="secondary" onClick={p.onDraw}>Poligon çiz</Button>
          <Button variant="secondary" onClick={p.onClearRoi}>Temizle</Button>
        </div>
        {p.drawing && <Button variant="secondary" className={styles.full} onClick={p.onFinish}>Poligonu bitir (veya çift tıkla)</Button>}
        <p className={styles.hint}>{p.roiHint}</p>
        {hasDrawnRoi && (
          <>
            <Button className={styles.full} onClick={goToDrawnRoi}>
              Poligon hazır — haritada göster
            </Button>
            <p className={styles.hint}>
              Koordinat girmen gerekmez. Takvim / kırılma / brifing bu poligonu kullanır.
            </p>
          </>
        )}
        <details className={styles.roiAlt} open={!hasDrawnRoi}>
          <summary>Alternatif: enlem / boylam / yarıçap</summary>
          <div className={styles.latLonBox}>
            <label>
              Enlem
              <input
                type="number"
                step="0.01"
                value={lat}
                onChange={(e) => { setTouched(true); setLat(e.target.value); }}
                onKeyDown={(e) => { if (e.key === "Enter") applyLatLon(); }}
              />
            </label>
            <label>
              Boylam
              <input
                type="number"
                step="0.01"
                value={lon}
                onChange={(e) => { setTouched(true); setLon(e.target.value); }}
                onKeyDown={(e) => { if (e.key === "Enter") applyLatLon(); }}
              />
            </label>
            <label>
              Yarıçap km
              <input
                type="number"
                step="1"
                min={1}
                max={200}
                value={radius}
                onChange={(e) => { setTouched(true); setRadius(e.target.value); }}
                onKeyDown={(e) => { if (e.key === "Enter") applyLatLon(); }}
              />
            </label>
          </div>
          <Button
            variant="secondary"
            className={styles.full}
            onClick={() => applyLatLon()}
          >
            Koordinat ile uygula
          </Button>
        </details>
        {roiErr && <p className={styles.errText}>{roiErr}</p>}
        {roiOk && !roiErr && <p className={styles.hint}>{roiOk}</p>}
      </section>

      <section className={styles.ctl}>
        <h4>Rapor</h4>
        <Button className={styles.full} onClick={p.onBriefing}>Brifing üret</Button>
        <Button
          variant="secondary"
          className={styles.full}
          disabled={!canCsv || p.csvBusy}
          onClick={p.onExportCsv}
        >
          {p.csvBusy ? "CSV hazırlanıyor..." : "Hotspot CSV indir"}
        </Button>
        <p className={styles.hint}>
          Brifing: acil müdahale, erken uyarı, arazi, bilim, itfaiye. CSV: ROI içi FIRMS noktaları.
        </p>
      </section>

      <Glossary />

      <p className={styles.foot}>
        Hotspot ≠ yangın sayısı ≠ yanmış alan. Harmonize: VIIRS-eşdeğer ölçek (sentetik benchmark eğitimi;
        gerçek arşivde aylık MODIS→VIIRS kontrolü). VIIRS mutlak yer-gerçeği değildir. Brifing = ön tarama, talimat değil.
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
