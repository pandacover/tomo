/**
 * Cross-host Control API contract (v1).
 * Future Python service: src/tomo/control_api.py (FastAPI).
 * Dashboard calls CONTROL_API_URL; agent stays on a separate host.
 */

import type { PendingApproval } from "@/domain/approval";
import type { Integration } from "@/domain/integration";
import type { MemoryEntry } from "@/domain/memory";
import type { OverviewStats } from "@/domain/overview";
import type { ScheduledTask } from "@/domain/scheduled-task";

export const CONTROL_API_VERSION = "v1" as const;

export type HealthResponse = {
  ok: boolean;
  model?: string;
  projectRoot?: string;
  authenticated: boolean;
};

export type OverviewResponse = OverviewStats & {
  pendingApprovalCount: number;
};

export type MemoriesResponse = {
  entries: MemoryEntry[];
};

export type AppendMemoryRequest = {
  text: string;
};

export type ImportMemoryResponse = {
  imported: number;
  entries: MemoryEntry[];
};

export type IntegrationsResponse = {
  integrations: Integration[];
};

export type ScheduledTasksResponse = {
  tasks: ScheduledTask[];
};

export type PatchScheduledTaskRequest = {
  enabled?: boolean;
  status?: ScheduledTask["status"];
};

export type ApprovalsResponse = {
  approvals: PendingApproval[];
};

export type ResolveApprovalRequest = {
  approved: boolean;
};

export const controlApiRoutes = {
  health: `/${CONTROL_API_VERSION}/health`,
  overview: `/${CONTROL_API_VERSION}/overview`,
  memories: `/${CONTROL_API_VERSION}/memories`,
  memoriesImport: `/${CONTROL_API_VERSION}/memories/import`,
  integrations: `/${CONTROL_API_VERSION}/integrations`,
  scheduledTasks: `/${CONTROL_API_VERSION}/scheduled-tasks`,
  approval: (id: string) => `/${CONTROL_API_VERSION}/approvals/${id}`,
  approvals: `/${CONTROL_API_VERSION}/approvals`,
} as const;