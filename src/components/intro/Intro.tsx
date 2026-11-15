import { useCallback, useEffect, useRef, useState } from "react";

import logo from "@/assets/images/logo-transparent.png";
import introVideo from "@/assets/videos/intro.mp4";
import { siteConfig } from "@/config/site";
import styles from "./Intro.module.css";

const LEAVE_MS = 1300;          // Intro.module.css içindeki .leaving süresiyle aynı olmalı
const SAFETY_MS = 14000;        // video oynamaz ya da bitmezse giriş ekranda takılı kalmasın
const NO_VIDEO_HOLD_MS = 4500;  // video açılamazsa logo siyah zeminde bu kadar durur
const SEEN_KEY = "alaz-intro-seen-v2";

type Phase = "playing" | "leaving" | "done";

function alreadySeen(): boolean {
  try {
    return sessionStorage.getItem(SEEN_KEY) === "1";
  } catch {
    return false;
  }
}

function markSeen() {
  try {
    sessionStorage.setItem(SEEN_KEY, "1");
  } catch {
    /* private mode */
  }
}

/**
 * Açılış: arkada video oynar, logo alttan gelip ortada durur; video bitince giriş
 * kaybolur. Aynı tarayıcı oturumunda bir kez; Esc / tık ile atlanır.
 */
export function Intro() {
  const [phase, setPhase] = useState<Phase>(() => (alreadySeen() ? "done" : "playing"));
  const video = useRef<HTMLVideoElement>(null);
  const holdTimer = useRef<number | undefined>(undefined);

  const finish = useCallback(() => {
    markSeen();
    setPhase((p) => (p === "playing" ? "leaving" : p));
  }, []);

  useEffect(() => {
    if (phase !== "playing") return;
    const v = video.current;
    if (v) {
      v.muted = true;
      void v.play().catch(() => undefined);
    }
    const safety = window.setTimeout(finish, SAFETY_MS);
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") finish(); };
    window.addEventListener("keydown", onKey);
    return () => {
      window.clearTimeout(safety);
      window.clearTimeout(holdTimer.current);
      window.removeEventListener("keydown", onKey);
    };
  }, [phase, finish]);

  useEffect(() => {
    if (phase !== "leaving") return;
    const t = window.setTimeout(() => setPhase("done"), LEAVE_MS);
    return () => window.clearTimeout(t);
  }, [phase]);

  if (phase === "done") return null;

  return (
    <div
      className={`${styles.intro} ${phase === "leaving" ? styles.leaving : ""}`}
      onClick={finish}
      role="presentation"
    >
      <div className={styles.stage}>
        <video
          ref={video}
          className={styles.video}
          src={introVideo}
          autoPlay
          muted
          playsInline
          preload="auto"
          onEnded={finish}
          onError={() => { holdTimer.current = window.setTimeout(finish, NO_VIDEO_HOLD_MS); }}
        />
        <div className={styles.scrim} />
        <div className={styles.brand}>
          <img src={logo} alt={siteConfig.name} className={styles.logo} />
          <p className={styles.systemName}>Sistem başlatılıyor…</p>
        </div>
        <p className={styles.skipHint}>Geçmek için Esc&apos;ye basın</p>
      </div>
    </div>
  );
}
