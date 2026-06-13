"use client";

import { useMemo, useState } from "react";
import type { Connection, ConnectionCategory, ConnectionStatus } from "@/domain/connection";
import { Badge } from "@/components/ui/Badge";

type ConnectionListProps = {
  connections: Connection[];
};

const CATEGORIES: { id: ConnectionCategory; label: string }[] = [
  { id: "chat", label: "chats" },
  { id: "app", label: "apps" },
  { id: "social", label: "socials" },
  { id: "custom", label: "custom" },
];

function statusDot(status: ConnectionStatus): "cyan" | "green" | "amber" {
  if (status === "connected") return "green";
  if (status === "needs_setup") return "amber";
  return "cyan";
}

function statusLabel(status: ConnectionStatus): string {
  return status.replace("_", " ");
}

export function ConnectionList({ connections }: ConnectionListProps) {
  const [activeCategory, setActiveCategory] = useState<ConnectionCategory>("chat");
  const activeConnections = useMemo(
    () => connections.filter((connection) => connection.category === activeCategory),
    [activeCategory, connections],
  );

  return (
    <>
      <div className="tabs" role="tablist" aria-label="connection categories">
        {CATEGORIES.map((category) => {
          const count = connections.filter((connection) => connection.category === category.id).length;
          const active = activeCategory === category.id;
          return (
            <button
              aria-selected={active}
              className={`tab ${active ? "active" : ""}`}
              key={category.id}
              onClick={() => setActiveCategory(category.id)}
              role="tab"
              type="button"
            >
              {category.label}
              <span className="tab-count">{count}</span>
            </button>
          );
        })}
      </div>
      <div className="list" role="tabpanel">
        {activeConnections.length ? (
          activeConnections.map((connection) => (
            <div className="record" key={connection.id}>
              <div>
                <h3>{connection.name}</h3>
                <p>{connection.description}</p>
                <div className="row">
                  <Badge dot={statusDot(connection.status)}>{statusLabel(connection.status)}</Badge>
                  {connection.reviewRequired ? <span className="meta">needs setup</span> : null}
                  {connection.metadata?.toolCount ? (
                    <span className="meta">{connection.metadata.toolCount} tools</span>
                  ) : null}
                </div>
              </div>
            </div>
          ))
        ) : (
          <p className="meta">No {CATEGORIES.find((category) => category.id === activeCategory)?.label} connections.</p>
        )}
      </div>
    </>
  );
}
