import type { OverviewStats } from "@/domain/overview";
import { mockConnections } from "@/lib/mock/connections";
import { mockScheduledTasks } from "@/lib/mock/scheduled-tasks";
import { mockSessions } from "@/lib/mock/sessions";

export const mockOverviewStats: OverviewStats = {
  memoryCount: 128,
  memoriesUpdatedThisWeek: 14,
  sessionCount: mockSessions.length,
  connectionCount: mockConnections.length,
  connectionsNeedingReview: mockConnections.filter((connection) => connection.reviewRequired).length,
  scheduledTaskCount: mockScheduledTasks.length + 28,
};
