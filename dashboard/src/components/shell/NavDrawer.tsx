import Link from "next/link";

export function NavDrawer() {
  return (
    <>
      <input className="drawer-toggle" id="nav-drawer" type="checkbox" />
      <label className="drawer-trigger" htmlFor="nav-drawer" aria-label="Open navigation" />
      <label className="drawer-scrim" htmlFor="nav-drawer" aria-label="Close navigation" />
      <aside className="rail">
        <div className="drawer-profile">
          <p className="drawer-user">luv</p>
          <div className="drawer-user-id">local agent</div>
        </div>
        <section className="drawer-section">
          <h2 className="drawer-section-title">setting</h2>
          <nav className="drawer-list">
            <Link href="#">profile</Link>
            <Link href="/memories/import">import memory</Link>
          </nav>
        </section>
        <section className="drawer-section">
          <h2 className="drawer-section-title">account</h2>
          <nav className="drawer-list">
            <Link href="#">privacy</Link>
          </nav>
        </section>
      </aside>
    </>
  );
}