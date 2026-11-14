import { useMemo, useState } from "react";
import { Outlet, ScrollRestoration } from "react-router-dom";

import { Intro } from "@/components/intro/Intro";
import { SourceProvider } from "@/features/aether/sourceContext";
import type { Source } from "@/features/aether/types";
import { Header } from "./Header";
import styles from "./Layout.module.css";

export function Layout() {
  const [source, setSource] = useState<Source>("archive");
  const sourceValue = useMemo(
    () => ({ source, onSource: setSource }),
    [source],
  );

  return (
    <SourceProvider value={sourceValue}>
      <div className={styles.shell}>
        <Intro />
        <Header />
        <main className={styles.main}>
          <Outlet />
        </main>
        <ScrollRestoration />
      </div>
    </SourceProvider>
  );
}
