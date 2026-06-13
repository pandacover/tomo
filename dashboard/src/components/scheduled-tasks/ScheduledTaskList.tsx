import type { ScheduledTask } from "@/domain/scheduled-task";
import { Badge } from "@/components/ui/Badge";

type ScheduledTaskListProps = {
  tasks: ScheduledTask[];
};

function statusDot(task: ScheduledTask): "green" | "cyan" | "amber" {
  if (task.status === "pending" && task.requiresApproval) return "amber";
  if (task.status === "pending") return "green";
  return "cyan";
}

export function ScheduledTaskList({ tasks }: ScheduledTaskListProps) {
  return (
    <div className="list">
      {tasks.map((task) => (
        <div className="record" key={task.id}>
          <div>
            <h3>{task.label}</h3>
            <p>{task.description}</p>
            <div className="row">
              <Badge dot={statusDot(task)}>{task.status}</Badge>
              <span className="meta">{task.scheduleLabel}</span>
              {task.requiresApproval ? <span className="meta">requires approval</span> : null}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
