import Link from "next/link";
import { MemoryList } from "@/components/memories/MemoryList";
import { TopBar } from "@/components/shell/TopBar";
import { glossary } from "@/domain/glossary";
import { getMemories } from "@/lib/data";

export default async function MemoriesPage() {
  const entries = await getMemories();

  return (
    <>
      <TopBar crumbs={[{ label: "memories" }]} pageTitle={glossary.memories.title} />
      <section>
        <article className="panel">
          <div className="section-head">
            <p className="meta">{glossary.memories.subtitle}</p>
            <Link className="button primary" href="/memories/import">
              import memory
            </Link>
          </div>
          <MemoryList entries={entries} />
        </article>
      </section>
    </>
  );
}