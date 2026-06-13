import type { ReactNode } from "react";
import { NavDrawer } from "@/components/shell/NavDrawer";

type AppShellProps = {
  children: ReactNode;
};

export function AppShell({ children }: AppShellProps) {
  return (
    <div className="shell">
      <NavDrawer />
      <main className="main">{children}</main>
    </div>
  );
}