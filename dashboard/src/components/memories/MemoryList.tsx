"use client";

import { useState } from "react";
import type { MemoryEntry, MemoryFreshness } from "@/domain/memory";
import { Badge } from "@/components/ui/Badge";
import { Switch } from "@/components/ui/Switch";

const freshnessDot: Record<MemoryFreshness, "green" | "cyan" | "red"> = {
  updated: "green",
  new: "cyan",
  stale: "red",
};

type MemoryListProps = {
  entries: MemoryEntry[];
};

export function MemoryList({ entries }: MemoryListProps) {
  const [items, setItems] = useState(entries);

  function toggle(id: string, enabled: boolean) {
    setItems((current) =>
      current.map((entry) =>
        entry.id === id ? { ...entry, status: enabled ? "active" : "disabled" } : entry,
      ),
    );
  }

  return (
    <div className="list">
      {items.map((entry) => (
        <div className="record" key={entry.id}>
          <div>
            <h3>{entry.title}</h3>
            <p>{entry.text}</p>
            <div className="row">
              <Badge dot={freshnessDot[entry.freshness]}>{entry.freshness}</Badge>
              <span className="meta">{entry.updatedLabel}</span>
            </div>
          </div>
          <div className="toggle-cell">
            <span className="meta">{entry.status}</span>
            <Switch
              checked={entry.status === "active"}
              label={`enable ${entry.title}`}
              onChange={(checked) => toggle(entry.id, checked)}
            />
          </div>
        </div>
      ))}
    </div>
  );
}