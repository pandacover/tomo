import { unstable_noStore as noStore } from "next/cache";
import type { PendingApproval } from "@/domain/approval";
import type { Integration } from "@/domain/integration";
import type { MemoryEntry } from "@/domain/memory";
import type { OverviewStats } from "@/domain/overview";
import type { ScheduledTask } from "@/domain/scheduled-task";
import {
  controlApiConfigured,
  fetchApprovalsFromApi,
  fetchIntegrationsFromApi,
  fetchMemoriesFromApi,
  fetchOverviewFromApi,
  fetchScheduledTasksFromApi,
} from "@/lib/api/client";
import { mockApprovals } from "@/lib/mock/approvals";
import { mockIntegrations } from "@/lib/mock/integrations";
import { mockMemories } from "@/lib/mock/memories";
import { mockOverviewStats } from "@/lib/mock/overview";
import { mockScheduledTasks } from "@/lib/mock/scheduled-tasks";

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

export async function getIntegrations(): Promise<Integration[]> {
  if (!useLiveApi()) return mockIntegrations;
  return fetchIntegrationsFromApi();
}

export async function getScheduledTasks(): Promise<ScheduledTask[]> {
  if (!useLiveApi()) return mockScheduledTasks;
  return fetchScheduledTasksFromApi();
}

export async function getPendingApprovals(): Promise<PendingApproval[]> {
  if (!useLiveApi()) return mockApprovals;
  return fetchApprovalsFromApi();
}