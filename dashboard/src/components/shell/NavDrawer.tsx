import { NavDrawerPanel } from "@/components/shell/NavDrawerPanel";
import { getConnections } from "@/lib/data";

export async function NavDrawer() {
  const connections = await getConnections();

  return (
    <>
      <input className="drawer-toggle" id="nav-drawer" type="checkbox" />
      <label className="drawer-trigger" htmlFor="nav-drawer" aria-label="Open navigation" />
      <label className="drawer-scrim" htmlFor="nav-drawer" aria-label="Close navigation" />
      <NavDrawerPanel connections={connections} />
    </>
  );
}
