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

export const connectionSchema = z.object({
  id: z.string(),
  name: z.string(),
  category: z.enum(["chat", "app", "social", "custom"]),
  description: z.string(),
  status: z.enum(["connected", "available", "needs_setup", "disabled", "unknown"]),
  enabled: z.boolean(),
  reviewRequired: z.boolean().optional(),
  metadata: z.record(z.string(), z.union([z.string(), z.number(), z.boolean(), z.null()])).optional(),
});

export const legacyIntegrationSchema = z.object({
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

export const sessionSummarySchema = z.object({
  id: z.string(),
  name: z.string(),
  createdDate: z.string(),
  updatedDate: z.string(),
  messageCount: z.number(),
});

export const overviewStatsSchema = z
  .object({
    memoryCount: z.number(),
    memoriesUpdatedThisWeek: z.number(),
    connectionCount: z.number().optional(),
    connectionsNeedingReview: z.number().optional(),
    sessionCount: z.number().optional(),
    integrationCount: z.number().optional(),
    integrationsNeedingReview: z.number().optional(),
    scheduledTaskCount: z.number(),
  })
  .transform((stats) => ({
    memoryCount: stats.memoryCount,
    memoriesUpdatedThisWeek: stats.memoriesUpdatedThisWeek,
    sessionCount: stats.sessionCount ?? 0,
    connectionCount: stats.connectionCount ?? stats.integrationCount ?? 0,
    connectionsNeedingReview: stats.connectionsNeedingReview ?? stats.integrationsNeedingReview ?? 0,
    scheduledTaskCount: stats.scheduledTaskCount,
  }));

export const memoriesResponseSchema = z.object({
  entries: z.array(memoryEntrySchema),
});

export const connectionsResponseSchema = z.object({
  connections: z.array(connectionSchema),
});

export const legacyIntegrationsResponseSchema = z.object({
  integrations: z.array(legacyIntegrationSchema),
});

export const scheduledTasksResponseSchema = z.object({
  tasks: z.array(scheduledTaskSchema),
});

export const sessionsResponseSchema = z.object({
  sessions: z.array(sessionSummarySchema),
});

export const importMemoryResponseSchema = z.object({
  imported: z.number(),
  entries: z.array(memoryEntrySchema),
});
