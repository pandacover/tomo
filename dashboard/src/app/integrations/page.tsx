import { IntegrationList } from "@/components/integrations/IntegrationList";
import { TopBar } from "@/components/shell/TopBar";
import { glossary } from "@/domain/glossary";
import { getIntegrations } from "@/lib/data";

export default async function IntegrationsPage() {
  const integrations = await getIntegrations();

  return (
    <>
      <TopBar crumbs={[{ label: "integrations" }]} pageTitle={glossary.integrations.title} />
      <section>
        <article className="panel">
          <p className="meta" style={{ marginBottom: 12 }}>
            {glossary.integrations.subtitle}
          </p>
          <IntegrationList integrations={integrations} />
        </article>
      </section>
    </>
  );
}