import Link from "next/link";
import type { MemoryEntry } from "@/domain/memory";
import { Badge } from "@/components/ui/Badge";

type MemoryPreviewProps = {
  entries: MemoryEntry[];
};

export function MemoryPreview({ entries }: MemoryPreviewProps) {
  const preview = entries.slice(0, 4);

  return (
    <article className="memory-tile">
      <div className="section-head">
        <div>
          <span className="label">memories</span>
          <h2>memories</h2>
        </div>
        <Link className="button primary" href="/memories">
          view all
        </Link>
      </div>
      <div className="list">
        {preview.length ? (
          preview.map((entry) => (
            <div className="record" key={entry.id}>
              <div>
                <h3>{entry.title}</h3>
                <p>{entry.text}</p>
                <div className="row">
                  <Badge dot={entry.freshness === "stale" ? "red" : "green"}>{entry.freshness}</Badge>
                  <span className="meta">{entry.updatedLabel}</span>
                </div>
              </div>
            </div>
          ))
        ) : (
          <p className="meta">No memories yet.</p>
        )}
      </div>
    </article>
  );
}
