import { ConnectionList } from "@/components/connections/ConnectionList";
import { TopBar } from "@/components/shell/TopBar";
import { glossary } from "@/domain/glossary";
import { getConnections } from "@/lib/data";

export default async function ConnectionsPage() {
  const connections = await getConnections();

  return (
    <>
      <TopBar crumbs={[{ label: "connections" }]} pageTitle={glossary.connections.title} />
      <section>
        <article className="panel">
          <p className="meta" style={{ marginBottom: 12 }}>
            {glossary.connections.subtitle}
          </p>
          <ConnectionList connections={connections} />
        </article>
      </section>
    </>
  );
}
