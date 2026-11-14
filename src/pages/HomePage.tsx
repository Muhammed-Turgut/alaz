import { ButtonLink } from "@/components/ui/Button";
import { siteConfig } from "@/config/site";
import styles from "./HomePage.module.css";

export function HomePage() {
  return (
    <section className={styles.hero}>
      <div className="container">
        <h1 className={styles.title}>{siteConfig.name}</h1>
        <p className={styles.lead}>
          Proje çekirdeği hazır. Renk paleti ve içerik eklenmeyi bekliyor.
        </p>
        <div className={styles.actions}>
          <ButtonLink to="/hakkimizda">Hakkımızda</ButtonLink>
          <ButtonLink to="/" variant="secondary">
            Daha fazla
          </ButtonLink>
        </div>
      </div>
    </section>
  );
}
