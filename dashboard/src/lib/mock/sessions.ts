import type { SessionSummary } from "@/domain/session";

export const mockSessions: SessionSummary[] = [
  {
    id: "session-1",
    name: "desktop",
    createdDate: "2026-06-13T09:00:00Z",
    updatedDate: "2026-06-13T12:20:00Z",
    messageCount: 18,
  },
  {
    id: "session-2",
    name: "telegram",
    createdDate: "2026-06-12T10:14:00Z",
    updatedDate: "2026-06-13T08:42:00Z",
    messageCount: 9,
  },
];
