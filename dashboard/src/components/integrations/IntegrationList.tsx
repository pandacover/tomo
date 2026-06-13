"use client";

import { useState } from "react";
import type { Integration } from "@/domain/integration";
import { Badge } from "@/components/ui/Badge";
import { Switch } from "@/components/ui/Switch";

type IntegrationListProps = {
  integrations: Integration[];
};

function scopeDot(scopes: string[]): "cyan" | "green" | "amber" {
  if (scopes.includes("approval_channel")) return "amber";
  if (scopes.includes("browser")) return "cyan";
  return "green";
}

export function IntegrationList({ integrations }: IntegrationListProps) {
  const [items, setItems] = useState(integrations);

  function toggle(id: string, enabled: boolean) {
    setItems((current) =>
      current.map((item) => (item.id === id ? { ...item, enabled } : item)),
    );
  }

  return (
    <div className="list">
      {items.map((integration) => (
        <div className="record" key={integration.id}>
          <div>
            <h3>
              {integration.name}
              <span className="meta"> · {integration.kind}</span>
            </h3>
            <p>{integration.description}</p>
            <Badge dot={scopeDot(integration.scopes)}>{integration.scopes.join(" + ")}</Badge>
          </div>
          <Switch
            checked={integration.enabled}
            label={`enable ${integration.name}`}
            onChange={(checked) => toggle(integration.id, checked)}
          />
        </div>
      ))}
    </div>
  );
}