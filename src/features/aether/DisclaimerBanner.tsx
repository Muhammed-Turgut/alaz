import { useT } from "@/lib/i18n";
import styles from "./aether.module.css";

export function DisclaimerBanner() {
  const t = useT();
  return (
    <aside className={styles.disclaimer} role="note">
      {t.disclaimer}
    </aside>
  );
}
