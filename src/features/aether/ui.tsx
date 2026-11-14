import type { ReactNode } from "react";
import { useEffect, useRef } from "react";
import type { Chart } from "chart.js";

import { usePrefs } from "@/lib/preferences";

import styles from "./aether.module.css";

export function cx(...p: Array<string | false | undefined | null>) {
  return p.filter(Boolean).join(" ");
}

export function Seg<T extends string>({
  value, options, onChange,
}: { value: T; options: { v: T; label: string }[]; onChange: (v: T) => void }) {
  return (
    <div className={styles.seg} role="group">
      {options.map((o) => (
        <button key={o.v} type="button" className={cx(o.v === value && styles.on)} onClick={() => onChange(o.v)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

export type Tone = "raw" | "harm" | "fire" | "ok" | "";

export function Kpi({ tone = "", label, value, sub }: { tone?: Tone; label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className={cx(styles.kpi, tone && styles["kpi_" + tone])}>
      <div className={styles.kpiK}>{label}</div>
      <div className={styles.kpiV}>{value}</div>
      {sub !== undefined && <div className={styles.kpiS}>{sub}</div>}
    </div>
  );
}

export function Card({ title, children, className }: { title?: string; children: ReactNode; className?: string }) {
  return (
    <div className={cx(styles.card, className)}>
      {title && <h3>{title}</h3>}
      {children}
    </div>
  );
}

export function Badge({ tone, children }: { tone?: "ok" | "warn" | "bad"; children: ReactNode }) {
  return <span className={cx(styles.badge, tone && styles["badge_" + tone])}>{children}</span>;
}

export function Hint({ children }: { children: ReactNode }) {
  return <p className={styles.hint}>{children}</p>;
}

/** ROI yokken ortak boş durum: mesaj + haritada poligon çizmeye dönüş. */
export function EmptyRoi({
  message,
  onDraw,
}: {
  message: string;
  onDraw?: () => void;
}) {
  return (
    <div className={styles.emptyRoi}>
      <p className={styles.banner}>{message}</p>
      {onDraw && (
        <button type="button" className={styles.emptyRoiBtn} onClick={onDraw}>
          Haritada poligon çiz
        </button>
      )}
    </div>
  );
}

/** Chart.js tuvali: `draw` değiştikçe grafik yeniden kurulur, çıkışta yok edilir. */
export function ChartBox({ draw, height = 300, title }: { draw: (c: HTMLCanvasElement) => Chart; height?: number; title?: string }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const { theme } = usePrefs();
  useEffect(() => {
    if (!ref.current) return;
    const chart = draw(ref.current);
    return () => chart.destroy();
  }, [draw, theme]);
  return (
    <div className={styles.card}>
      {title && <h3>{title}</h3>}
      <div className={styles.chartWrap} style={{ height }}>
        <canvas ref={ref} />
      </div>
    </div>
  );
}

/** Yalnız tuval (kartsız), ör. ROI mini grafiği. */
export function MiniChart({ draw, height = 130 }: { draw: (c: HTMLCanvasElement) => Chart; height?: number }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const { theme } = usePrefs();
  useEffect(() => {
    if (!ref.current) return;
    const chart = draw(ref.current);
    return () => chart.destroy();
  }, [draw, theme]);
  return (
    <div className={styles.chartWrap} style={{ height }}>
      <canvas ref={ref} />
    </div>
  );
}

export function BarRows({ items }: { items: { label: string; v: number; text: string }[] }) {
  const mx = Math.max(1e-9, ...items.map((i) => Math.abs(i.v)));
  return (
    <div className={styles.bars}>
      {items.map((i) => (
        <div key={i.label} className={styles.barRow}>
          <span>{i.label}</span>
          <span className={styles.track}>
            <span
              className={cx(styles.fill, i.v < 0 && styles.fillNeg)}
              style={{ left: `${i.v < 0 ? 50 - (50 * Math.abs(i.v)) / mx : 50}%`, width: `${(50 * Math.abs(i.v)) / mx}%` }}
            />
          </span>
          <span className={styles.val}>{i.text}</span>
        </div>
      ))}
    </div>
  );
}
