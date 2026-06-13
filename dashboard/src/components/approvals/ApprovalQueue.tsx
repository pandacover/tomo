"use client";

import Link from "next/link";
import { useState } from "react";
import type { PendingApproval } from "@/domain/approval";
import { resolveApprovalAction } from "@/lib/api/actions";
import { controlApiConfigured } from "@/lib/api/config";

type ApprovalQueueProps = {
  initialApprovals: PendingApproval[];
  showViewAll?: boolean;
};

export function ApprovalQueue({ initialApprovals, showViewAll = true }: ApprovalQueueProps) {
  const [approvals, setApprovals] = useState(initialApprovals);
  const [pendingIds, setPendingIds] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const apiEnabled = controlApiConfigured();

  async function resolve(id: string, approved: boolean) {
    setError(null);
    setPendingIds((current) => new Set(current).add(id));

    const previous = approvals;
    setApprovals((current) => current.filter((item) => item.id !== id));

    if (apiEnabled) {
      try {
        await resolveApprovalAction(id, approved);
      } catch (err) {
        setApprovals(previous);
        setError(err instanceof Error ? err.message : "Failed to resolve approval.");
      }
    }

    setPendingIds((current) => {
      const next = new Set(current);
      next.delete(id);
      return next;
    });
  }

  return (
    <article className="panel">
      <div className="section-head">
        <div>
          <h2>pending approvals</h2>
          <p className="meta">
            Runtime tool and social actions that need a human decision before Tomo can proceed.
          </p>
        </div>
        {showViewAll ? (
          <Link className="button" href="/scheduled-tasks">
            view all
          </Link>
        ) : null}
      </div>
      {error ? <p className="meta danger">{error}</p> : null}
      <div className="list">
        {approvals.length === 0 ? (
          <p className="meta">No pending approvals.</p>
        ) : (
          approvals.map((approval) => (
            <div className="record approval-record" key={approval.id}>
              <div>
                <p>{approval.reason}</p>
                <div className="row">
                  <span className="badge">
                    <span className="dot amber" />
                    {approval.operation}
                  </span>
                  <span className="meta">{approval.target}</span>
                </div>
              </div>
              <div className="actions">
                <Link className="button" href="/memories">
                  inspect
                </Link>
                <button
                  className="button"
                  disabled={pendingIds.has(approval.id)}
                  type="button"
                  onClick={() => void resolve(approval.id, false)}
                >
                  deny
                </button>
                <button
                  className="button primary"
                  disabled={pendingIds.has(approval.id)}
                  type="button"
                  onClick={() => void resolve(approval.id, true)}
                >
                  approve
                </button>
              </div>
            </div>
          ))
        )}
      </div>
    </article>
  );
}