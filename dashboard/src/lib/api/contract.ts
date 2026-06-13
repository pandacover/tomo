/**
 * Cross-host Control API contract (v1).
 * Future Python service: src/tomo/control_api.py (FastAPI).
 * Dashboard calls CONTROL_API_URL; agent stays on a separate host.
 */

import type { Connection } from "@/domain/connection";
import type { MemoryEntry } from "@/domain/memory";
import type { OverviewStats } from "@/domain/overview";
import type { ScheduledTask } from "@/domain/scheduled-task";
import type { SessionSummary } from "@/domain/session";

export const CONTROL_API_VERSION = "v1" as const;

export type HealthResponse = {
  ok: boolean;
  model?: string;
  projectRoot?: string;
  authenticated: boolean;
};

export type OverviewResponse = OverviewStats;

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

export type ConnectionsResponse = {
  connections: Connection[];
};

export type ScheduledTasksResponse = {
  tasks: ScheduledTask[];
};

export type SessionsResponse = {
  sessions: SessionSummary[];
};

export const controlApiRoutes = {
  health: `/${CONTROL_API_VERSION}/health`,
  overview: `/${CONTROL_API_VERSION}/overview`,
  memories: `/${CONTROL_API_VERSION}/memories`,
  memoriesImport: `/${CONTROL_API_VERSION}/memories/import`,
  connections: `/${CONTROL_API_VERSION}/connections`,
  sessions: `/${CONTROL_API_VERSION}/sessions`,
  integrations: `/${CONTROL_API_VERSION}/integrations`,
  scheduledTasks: `/${CONTROL_API_VERSION}/scheduled-tasks`,
} as const;
