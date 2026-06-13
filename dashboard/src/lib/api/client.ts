import {
  controlApiAuthHeaders,
  controlApiConfigured,
  resolveControlApiUrl,
} from "@/lib/api/config";
import { controlApiRoutes } from "@/lib/api/contract";
import {
  connectionsResponseSchema,
  legacyIntegrationsResponseSchema,
  memoriesResponseSchema,
  overviewStatsSchema,
  sessionsResponseSchema,
  scheduledTasksResponseSchema,
} from "@/lib/api/schemas";
import type { Connection } from "@/domain/connection";
import type { MemoryEntry } from "@/domain/memory";
import type { OverviewStats } from "@/domain/overview";
import type { ScheduledTask } from "@/domain/scheduled-task";
import type { SessionSummary } from "@/domain/session";

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

async function tryControlFetch<T>(path: string): Promise<T | null> {
  try {
    return await controlFetch<T>(path);
  } catch {
    return null;
  }
}

export async function fetchOverviewFromApi(): Promise<OverviewStats> {
  const data = await controlFetch<unknown>(controlApiRoutes.overview);
  return overviewStatsSchema.parse(data);
}

export async function fetchMemoriesFromApi(): Promise<MemoryEntry[]> {
  const data = memoriesResponseSchema.parse(await controlFetch<unknown>(controlApiRoutes.memories));
  return data.entries;
}

export async function fetchConnectionsFromApi(): Promise<Connection[]> {
  const current = await tryControlFetch<unknown>(controlApiRoutes.connections);
  if (current) {
    return connectionsResponseSchema.parse(current).connections;
  }

  const legacy = legacyIntegrationsResponseSchema.parse(await controlFetch<unknown>(controlApiRoutes.integrations));
  return legacy.integrations
    .filter((integration) => integration.kind === "gateway")
    .map((integration) => ({
      id: `chat-${integration.name}`,
      name: integration.name,
      category: "chat" as const,
      description: integration.description,
      status: integration.enabled ? ("connected" as const) : ("needs_setup" as const),
      enabled: integration.enabled,
      reviewRequired: integration.reviewRequired,
    }));
}

export async function fetchScheduledTasksFromApi(): Promise<ScheduledTask[]> {
  const data = scheduledTasksResponseSchema.parse(await controlFetch<unknown>(controlApiRoutes.scheduledTasks));
  return data.tasks;
}

export async function fetchSessionsFromApi(): Promise<SessionSummary[]> {
  const data = sessionsResponseSchema.parse(await controlFetch<unknown>(controlApiRoutes.sessions));
  return data.sessions;
}
