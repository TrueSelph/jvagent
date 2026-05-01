import { createContext, useContext, type ReactNode } from "react";

const OpenAppGraphContext = createContext<(() => void) | null>(null);

export function AppGraphProvider({
  children,
  openGraph,
}: {
  children: ReactNode;
  openGraph: () => void;
}) {
  return (
    <OpenAppGraphContext.Provider value={openGraph}>
      {children}
    </OpenAppGraphContext.Provider>
  );
}

export function useOpenAppGraph(): () => void {
  const fn = useContext(OpenAppGraphContext);
  return fn ?? (() => {});
}
