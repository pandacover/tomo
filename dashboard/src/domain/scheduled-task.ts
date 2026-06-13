export type ScheduledTaskKind = "reminder" | "action";
export type ScheduledTaskStatus = "pending" | "fired" | "cancelled" | "error";

export type ScheduledTask = {
  id: string;
  kind: ScheduledTaskKind;
  label: string;
  description: string;
  scheduleLabel: string;
  status: ScheduledTaskStatus;
  enabled: boolean;
  requiresApproval?: boolean;
};