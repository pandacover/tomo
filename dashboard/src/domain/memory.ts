export type MemoryFreshness = "new" | "updated" | "stale";
export type MemoryStatus = "active" | "disabled";

export type MemoryEntry = {
  id: string;
  timestamp: string;
  text: string;
  title: string;
  status: MemoryStatus;
  freshness: MemoryFreshness;
  updatedLabel: string;
};