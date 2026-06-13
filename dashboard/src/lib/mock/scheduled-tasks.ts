import type { ScheduledTask } from "@/domain/scheduled-task";

export const mockScheduledTasks: ScheduledTask[] = [
  {
    id: "task-1",
    kind: "reminder",
    label: "Morning workspace brief",
    description: "Reads recent memory updates and summarizes risky stale context.",
    scheduleLabel: "08:30 daily",
    status: "pending",
    enabled: true,
  },
  {
    id: "task-2",
    kind: "action",
    label: "Integration drift check",
    description:
      "Compares installed skills and tools with registry permissions and flags mismatches.",
    scheduleLabel: "manual approval",
    status: "pending",
    enabled: true,
    requiresApproval: true,
  },
  {
    id: "task-3",
    kind: "action",
    label: "Browser session cleanup",
    description: "Clears ephemeral browser state after social auth work completes.",
    scheduleLabel: "on logout",
    status: "cancelled",
    enabled: false,
  },
];