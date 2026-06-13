import { MemoryPreview } from "@/components/memories/MemoryPreview";
import { TopBar } from "@/components/shell/TopBar";
import { StatCard } from "@/components/ui/StatCard";
import { getMemories, getOverviewStats } from "@/lib/data";

export default async function HomePage() {
  const [stats, memories] = await Promise.all([getOverviewStats(), getMemories()]);

  return (
    <>
      <TopBar />
      <section className="grid cols-2">
        <StatCard
          href="/sessions"
          label="Sessions"
          value={stats.sessionCount}
          meta="saved chat sessions"
          dot="cyan"
        />
        <StatCard
          href="/scheduled-tasks"
          label="Scheduled tasks"
          value={stats.scheduledTaskCount}
          meta="Read-only routines"
          dot="amber"
        />
      </section>
      <section className="home-memory-section">
        <MemoryPreview entries={memories} />
      </section>
    </>
  );
}
