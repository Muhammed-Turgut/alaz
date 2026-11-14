import logo from "@/assets/images/logo-transparent.png";
import { siteConfig } from "@/config/site";
import styles from "./BriefingLoading.module.css";

/** Komutan brifingi üretilirken: yalnızca logo markası + “üretim yapılıyor”. */
export function BriefingLoading() {
  return (
    <div className={styles.overlay} role="status" aria-live="polite" aria-busy="true" aria-label={`${siteConfig.name} üretim yapılıyor`}>
      <div className={styles.center}>
        <div className={styles.mark}>
          <img src={logo} alt="" className={styles.logo} />
        </div>
        <p className={styles.status}>Üretim yapılıyor</p>
      </div>
    </div>
  );
}
