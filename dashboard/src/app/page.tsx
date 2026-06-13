import { ApprovalQueue } from "@/components/approvals/ApprovalQueue";
import { TopBar } from "@/components/shell/TopBar";
import { StatCard } from "@/components/ui/StatCard";
import {
  getOverviewStats,
  getPendingApprovals,
} from "@/lib/data";

export default async function HomePage() {
  const [stats, approvals] = await Promise.all([
    getOverviewStats(),
    getPendingApprovals(),
  ]);

  return (
    <>
      <TopBar />
      <section className="grid cols-3">
        <StatCard
          href="/memories"
          label="Memories"
          value={stats.memoryCount}
          meta={`${stats.memoriesUpdatedThisWeek} updated this week`}
        />
        <StatCard
          href="/integrations"
          label="Integrations"
          value={stats.integrationCount}
          meta={`${stats.integrationsNeedingReview} require review`}
          dot="cyan"
        />
        <StatCard
          href="/scheduled-tasks"
          label="Scheduled tasks"
          value={stats.scheduledTaskCount}
          meta={`${stats.scheduledTasksGated} gated by approval`}
          dot="amber"
        />
      </section>
      <section style={{ marginTop: 16 }}>
        <ApprovalQueue initialApprovals={approvals} />
      </section>
    </>
  );
}