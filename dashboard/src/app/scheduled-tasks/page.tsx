import { ScheduledTaskList } from "@/components/scheduled-tasks/ScheduledTaskList";
import { TopBar } from "@/components/shell/TopBar";
import { glossary } from "@/domain/glossary";
import { getScheduledTasks } from "@/lib/data";

export default async function ScheduledTasksPage() {
  const tasks = await getScheduledTasks();

  return (
    <>
      <TopBar
        crumbs={[{ label: "scheduled tasks" }]}
        pageTitle={glossary.scheduledTasks.title}
      />
      <section>
        <article className="panel">
          <p className="meta" style={{ marginBottom: 12 }}>
            {glossary.scheduledTasks.subtitle}
          </p>
          <ScheduledTaskList tasks={tasks} />
        </article>
      </section>
    </>
  );
}