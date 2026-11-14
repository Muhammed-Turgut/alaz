import { Outlet, ScrollRestoration } from "react-router-dom";

import { Intro } from "@/components/intro/Intro";
import { Header } from "./Header";
import styles from "./Layout.module.css";

export function Layout() {
  return (
    <div className={styles.shell}>
      <Intro />
      <Header />
      <main className={styles.main}>
        <Outlet />
      </main>
      <ScrollRestoration />
    </div>
  );
}
