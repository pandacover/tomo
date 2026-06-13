import type { OverviewStats } from "@/domain/overview";
import { mockApprovals } from "@/lib/mock/approvals";
import { mockIntegrations } from "@/lib/mock/integrations";
import { mockScheduledTasks } from "@/lib/mock/scheduled-tasks";

export const mockOverviewStats: OverviewStats = {
  memoryCount: 128,
  memoriesUpdatedThisWeek: 14,
  integrationCount: mockIntegrations.length + 14,
  integrationsNeedingReview: mockIntegrations.filter((i) => i.reviewRequired).length,
  scheduledTaskCount: mockScheduledTasks.length + 28,
  scheduledTasksGated: mockScheduledTasks.filter((t) => t.requiresApproval).length + 5,
};

export function getMockApprovalCount() {
  return mockApprovals.length;
}