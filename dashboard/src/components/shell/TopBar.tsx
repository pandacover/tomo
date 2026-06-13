import Link from "next/link";

type Crumb = {
  label: string;
  href?: string;
};

type TopBarProps = {
  crumbs?: Crumb[];
  pageTitle?: string;
};

export function TopBar({ crumbs, pageTitle }: TopBarProps) {
  return (
    <section className="topbar">
      <div>
        <h1 className="compact-wordmark">
          {crumbs && crumbs.length > 0 ? (
            <>
              <Link className="crumb-home" href="/">
                tomo
              </Link>
              {crumbs.map((crumb, index) => (
                <span key={crumb.label}>
                  <span className="crumb"> / </span>
                  {crumb.href ? (
                    <Link className="crumb-home" href={crumb.href}>
                      {crumb.label}
                    </Link>
                  ) : (
                    <span className={index === crumbs.length - 1 ? "crumb-current" : "crumb-home"}>
                      {crumb.label}
                    </span>
                  )}
                </span>
              ))}
            </>
          ) : (
            "tomo"
          )}
        </h1>
        {pageTitle ? <div className="page-title">{pageTitle}</div> : null}
      </div>
    </section>
  );
}