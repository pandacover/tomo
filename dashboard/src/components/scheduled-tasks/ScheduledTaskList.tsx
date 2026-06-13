"use client";

import { useState } from "react";
import type { ScheduledTask } from "@/domain/scheduled-task";
import { Badge } from "@/components/ui/Badge";
import { Switch } from "@/components/ui/Switch";
import { patchScheduledTaskAction } from "@/lib/api/actions";
import { controlApiConfigured } from "@/lib/api/config";

type ScheduledTaskListProps = {
  tasks: ScheduledTask[];
};

function statusDot(task: ScheduledTask): "green" | "cyan" | "amber" {
  if (!task.enabled) return "cyan";
  if (task.requiresApproval) return "amber";
  return "green";
}

export function ScheduledTaskList({ tasks }: ScheduledTaskListProps) {
  const [items, setItems] = useState(tasks);
  const [pendingIds, setPendingIds] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const apiEnabled = controlApiConfigured();

  async function toggle(id: string, enabled: boolean) {
    setError(null);
    const previous = items;
    setItems((current) =>
      current.map((task) => (task.id === id ? { ...task, enabled } : task)),
    );

    if (!enabled && apiEnabled) {
      setPendingIds((current) => new Set(current).add(id));
      try {
        const updated = await patchScheduledTaskAction(id, { enabled: false });
        setItems((current) =>
          current.map((task) => (task.id === id ? updated : task)),
        );
      } catch (err) {
        setItems(previous);
        setError(err instanceof Error ? err.message : "Failed to update scheduled task.");
      } finally {
        setPendingIds((current) => {
          const next = new Set(current);
          next.delete(id);
          return next;
        });
      }
      return;
    }

    if (enabled && apiEnabled) {
      setItems(previous);
      setError("Re-enabling cancelled tasks is not supported yet.");
    }
  }

  return (
    <>
      {error ? <p className="meta danger" style={{ marginBottom: 12 }}>{error}</p> : null}
      <div className="list">
        {items.map((task) => (
          <div className="record" key={task.id}>
            <div>
              <h3>{task.label}</h3>
              <p>{task.description}</p>
              <div className="row">
                <Badge dot={statusDot(task)}>{task.enabled ? "enabled" : "disabled"}</Badge>
                <span className="meta">{task.scheduleLabel}</span>
              </div>
            </div>
            <Switch
              checked={task.enabled}
              disabled={pendingIds.has(task.id)}
              label={`enable ${task.label}`}
              onChange={(checked) => void toggle(task.id, checked)}
            />
          </div>
        ))}
      </div>
    </>
  );
}