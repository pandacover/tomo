"use client";

import Link from "next/link";
import { useState } from "react";
import { ConnectionList } from "@/components/connections/ConnectionList";
import type { Connection } from "@/domain/connection";

type DrawerView = "menu" | "connections";

type NavDrawerPanelProps = {
  connections: Connection[];
};

export function NavDrawerPanel({ connections }: NavDrawerPanelProps) {
  const [view, setView] = useState<DrawerView>("menu");

  return (
    <aside className="rail">
      {view === "menu" ? (
        <>
          <div className="drawer-profile">
            <p className="drawer-user">luv</p>
            <div className="drawer-user-id">local agent</div>
          </div>
          <section className="drawer-section">
            <h2 className="drawer-section-title">setting</h2>
            <nav className="drawer-list">
              <Link href="#">profile</Link>
              <button type="button" onClick={() => setView("connections")}>
                <span>connections</span>
                <span className="meta">{connections.length}</span>
              </button>
              <Link href="/memories/import">import memory</Link>
            </nav>
          </section>
          <section className="drawer-section">
            <h2 className="drawer-section-title">account</h2>
            <nav className="drawer-list">
              <Link href="#">privacy</Link>
            </nav>
          </section>
        </>
      ) : (
        <section className="drawer-panel drawer-connections">
          <button className="drawer-back" type="button" onClick={() => setView("menu")}>
            back
          </button>
          <div className="drawer-panel-head">
            <h2>connections</h2>
            <p className="meta">chat surfaces, socials, apps, and custom connectors</p>
          </div>
          <ConnectionList connections={connections} />
        </section>
      )}
    </aside>
  );
}
