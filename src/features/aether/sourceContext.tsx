import { createContext, useContext, type ReactNode } from "react";

import type { Source } from "./types";

export interface SourceContextValue {
  source: Source;
  onSource: (s: Source) => void;
}

const SourceContext = createContext<SourceContextValue | null>(null);

export function SourceProvider({
  value,
  children,
}: {
  value: SourceContextValue;
  children: ReactNode;
}) {
  return <SourceContext.Provider value={value}>{children}</SourceContext.Provider>;
}

export function useDataSource(): SourceContextValue | null {
  return useContext(SourceContext);
}
