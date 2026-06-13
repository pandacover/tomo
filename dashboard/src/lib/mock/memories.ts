import type { MemoryEntry } from "@/domain/memory";

export const mockMemories: MemoryEntry[] = [
  {
    id: "mem-1",
    timestamp: "2026-06-13T10:08:00Z",
    text: "Use the react host boundary before changing desktop shell behavior.",
    title: "react desktop host boundary",
    status: "active",
    freshness: "updated",
    updatedLabel: "refreshed 16:08",
  },
  {
    id: "mem-2",
    timestamp: "2026-06-13T11:44:00Z",
    text: "Default browser collaboration work to a tomo-controlled chromium session.",
    title: "dedicated tomo browser first",
    status: "active",
    freshness: "new",
    updatedLabel: "added 17:44",
  },
  {
    id: "mem-3",
    timestamp: "2026-06-12T09:51:00Z",
    text: "Keep auth flows memory-only unless persistence is explicitly requested.",
    title: "ephemeral auth session",
    status: "disabled",
    freshness: "stale",
    updatedLabel: "flagged 15:51",
  },
];