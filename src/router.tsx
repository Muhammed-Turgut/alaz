import { createBrowserRouter, Navigate } from "react-router-dom";

import { Layout } from "@/components/layout/Layout";
import { NotFoundPage } from "@/pages/NotFoundPage";

// Ana sayfa = AETHER-FIRE uygulamasının kendisi (ayrı sekme yok).
const aetherLazy = async () => ({
  Component: (await import("@/features/aether/AetherPage")).AetherPage,
});

export const router = createBrowserRouter([
  {
    element: <Layout />,
    children: [
      { path: "/", lazy: aetherLazy },
      { path: "/aether", element: <Navigate to="/" replace /> },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
]);
