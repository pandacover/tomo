import type { PendingApproval } from "@/domain/approval";

export const mockApprovals: PendingApproval[] = [
  {
    id: "apr-1",
    operation: "browser",
    target: "open shared session",
    reason:
      "Browser tool can open a shared session, but risky actions still need a clear approval rule.",
    channelId: "desktop:local",
  },
  {
    id: "apr-2",
    operation: "write_file",
    target: "MEMORY.md",
    reason:
      "One stale memory entry still mentions profile persistence. Mark it stale before publishing dependent tasks.",
    channelId: "desktop:local",
  },
  {
    id: "apr-3",
    operation: "terminal",
    target: "uv run tomo telegram start",
    reason:
      "Network and browser permissions changed. Confirm the new scope before enabling gateway access.",
    channelId: "telegram:123456789",
  },
];