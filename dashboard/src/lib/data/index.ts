import { unstable_noStore as noStore } from "next/cache";
import type { Connection } from "@/domain/connection";
import type { MemoryEntry } from "@/domain/memory";
import type { OverviewStats } from "@/domain/overview";
import type { ScheduledTask } from "@/domain/scheduled-task";
import type { SessionSummary } from "@/domain/session";
import {
  controlApiConfigured,
  fetchConnectionsFromApi,
  fetchMemoriesFromApi,
  fetchOverviewFromApi,
  fetchScheduledTasksFromApi,
  fetchSessionsFromApi,
} from "@/lib/api/client";
import { mockConnections } from "@/lib/mock/connections";
import { mockMemories } from "@/lib/mock/memories";
import { mockOverviewStats } from "@/lib/mock/overview";
import { mockScheduledTasks } from "@/lib/mock/scheduled-tasks";
import { mockSessions } from "@/lib/mock/sessions";

const useMocks = !controlApiConfigured();

function useLiveApi() {
  if (useMocks) return false;
  noStore();
  return true;
}

export async function getOverviewStats(): Promise<OverviewStats> {
  if (!useLiveApi()) return mockOverviewStats;
  return fetchOverviewFromApi();
}

export async function getMemories(): Promise<MemoryEntry[]> {
  if (!useLiveApi()) return mockMemories;
  return fetchMemoriesFromApi();
}

export async function getConnections(): Promise<Connection[]> {
  if (!useLiveApi()) return mockConnections;
  return fetchConnectionsFromApi();
}

export async function getScheduledTasks(): Promise<ScheduledTask[]> {
  if (!useLiveApi()) return mockScheduledTasks;
  return fetchScheduledTasksFromApi();
}

export async function getSessions(): Promise<SessionSummary[]> {
  if (!useLiveApi()) return mockSessions;
  return fetchSessionsFromApi();
}
