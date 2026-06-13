import {
  controlApiAuthHeaders,
  controlApiConfigured,
  resolveControlApiUrl,
} from "@/lib/api/config";
import { controlApiRoutes } from "@/lib/api/contract";
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
  const data = await controlFetch<OverviewStats>(controlApiRoutes.overview);
  return data;
}

export async function fetchMemoriesFromApi(): Promise<MemoryEntry[]> {
  const data = await controlFetch<{ entries: MemoryEntry[] }>(controlApiRoutes.memories);
  return data.entries;
}

export async function fetchIntegrationsFromApi(): Promise<Integration[]> {
  const data = await controlFetch<{ integrations: Integration[] }>(
    controlApiRoutes.integrations,
  );
  return data.integrations;
}

export async function fetchScheduledTasksFromApi(): Promise<ScheduledTask[]> {
  const data = await controlFetch<{ tasks: ScheduledTask[] }>(
    controlApiRoutes.scheduledTasks,
  );
  return data.tasks;
}

export async function fetchApprovalsFromApi(): Promise<PendingApproval[]> {
  const data = await controlFetch<{ approvals: PendingApproval[] }>(
    controlApiRoutes.approvals,
  );
  return data.approvals;
}