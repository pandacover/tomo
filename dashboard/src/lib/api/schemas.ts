import { z } from "zod";

export const memoryEntrySchema = z.object({
  id: z.string(),
  timestamp: z.string(),
  text: z.string(),
  title: z.string(),
  status: z.enum(["active", "disabled"]),
  freshness: z.enum(["new", "updated", "stale"]),
  updatedLabel: z.string(),
});

export const integrationSchema = z.object({
  id: z.string(),
  name: z.string(),
  kind: z.enum(["skill", "tool", "gateway"]),
  description: z.string(),
  scopes: z.array(z.string()),
  enabled: z.boolean(),
  reviewRequired: z.boolean().optional(),
});

export const scheduledTaskSchema = z.object({
  id: z.string(),
  kind: z.enum(["reminder", "action"]),
  label: z.string(),
  description: z.string(),
  scheduleLabel: z.string(),
  status: z.enum(["pending", "fired", "cancelled", "error"]),
  enabled: z.boolean(),
  requiresApproval: z.boolean().optional(),
});

export const pendingApprovalSchema = z.object({
  id: z.string(),
  operation: z.string(),
  target: z.string(),
  reason: z.string(),
  channelId: z.string().optional().nullable(),
}).transform((approval) => ({
  ...approval,
  channelId: approval.channelId ?? undefined,
}));

export const overviewStatsSchema = z.object({
  memoryCount: z.number(),
  memoriesUpdatedThisWeek: z.number(),
  integrationCount: z.number(),
  integrationsNeedingReview: z.number(),
  scheduledTaskCount: z.number(),
  scheduledTasksGated: z.number(),
  pendingApprovalCount: z.number().optional(),
  toolCount: z.number().optional(),
  skillCount: z.number().optional(),
  gatewayCount: z.number().optional(),
  gatewaysNeedingReview: z.number().optional(),
});

export const memoriesResponseSchema = z.object({
  entries: z.array(memoryEntrySchema),
});

export const integrationsResponseSchema = z.object({
  integrations: z.array(integrationSchema),
});

export const scheduledTasksResponseSchema = z.object({
  tasks: z.array(scheduledTaskSchema),
});

export const approvalsResponseSchema = z.object({
  approvals: z.array(pendingApprovalSchema),
});

export const importMemoryResponseSchema = z.object({
  imported: z.number(),
  entries: z.array(memoryEntrySchema),
});

export const scheduledTaskResponseSchema = z.object({
  task: scheduledTaskSchema,
});
