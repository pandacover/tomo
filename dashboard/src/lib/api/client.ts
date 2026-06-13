import {
  controlApiAuthHeaders,
  controlApiConfigured,
  resolveControlApiUrl,
} from "@/lib/api/config";
import { controlApiRoutes } from "@/lib/api/contract";
import {
  approvalsResponseSchema,
  integrationsResponseSchema,
  memoriesResponseSchema,
  overviewStatsSchema,
  scheduledTasksResponseSchema,
} from "@/lib/api/schemas";
import type { PendingApproval } from "@/domain/approval";
import type { Integration } from "@/domain/integration";
import type { MemoryEntry } from "@/domain/memory";
import type { OverviewStats } from "@/domain/overview";
import type { ScheduledTask } from "@/domain/scheduled-task";

export { controlApiConfigured };

function baseUrl(): string {
  const url = resolveControlApiUrl();
  if (!url) {
    throw new Error("NEXT_PUBLIC_CONTROL_API_URL is not configured.");
  }
  return url;
}

async function controlFetch<T>(path: string): Promise<T> {
  const response = await fetch(`${baseUrl()}${path}`, {
    headers: controlApiAuthHeaders(),
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(`Control API ${response.status} for ${path}`);
  }
  return response.json() as Promise<T>;
}

export async function fetchOverviewFromApi(): Promise<OverviewStats> {
  const data = await controlFetch<unknown>(controlApiRoutes.overview);
  return overviewStatsSchema.parse(data);
}

export async function fetchMemoriesFromApi(): Promise<MemoryEntry[]> {
  const data = memoriesResponseSchema.parse(await controlFetch<unknown>(controlApiRoutes.memories));
  return data.entries;
}

export async function fetchIntegrationsFromApi(): Promise<Integration[]> {
  const data = integrationsResponseSchema.parse(await controlFetch<unknown>(controlApiRoutes.integrations));
  return data.integrations;
}

export async function fetchScheduledTasksFromApi(): Promise<ScheduledTask[]> {
  const data = scheduledTasksResponseSchema.parse(await controlFetch<unknown>(controlApiRoutes.scheduledTasks));
  return data.tasks;
}

export async function fetchApprovalsFromApi(): Promise<PendingApproval[]> {
  const data = approvalsResponseSchema.parse(await controlFetch<unknown>(controlApiRoutes.approvals));
  return data.approvals;
}
