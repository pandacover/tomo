"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { importMemoriesAction } from "@/lib/api/actions";
import { controlApiConfigured } from "@/lib/api/config";

const ACCEPTED = [".pdf", ".txt", ".md", ".markdown", ".json"];

type ImportRow = {
  name: string;
  status: "queued" | "importing" | "imported" | "error";
  detail?: string;
};

export function MemoryImportDropzone() {
  const router = useRouter();
  const [rows, setRows] = useState<ImportRow[]>([]);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const apiEnabled = controlApiConfigured();

  async function importFiles(fileList: FileList | null) {
    if (!fileList || fileList.length === 0) return;
    setError(null);

    const files = Array.from(fileList);
    setRows((current) => [
      ...current,
      ...files.map((file) => ({ name: file.name, status: "importing" as const })),
    ]);

    if (!apiEnabled) {
      setRows((current) =>
        current.map((row) =>
          files.some((file) => file.name === row.name)
            ? { ...row, status: "imported", detail: "queued locally (mock)" }
            : row,
        ),
      );
      return;
    }

    const names = new Set(files.map((file) => file.name));
    try {
      const result = await importMemoriesAction(files);
      setRows((current) =>
        current.map((row) =>
          names.has(row.name)
            ? {
                ...row,
                status: "imported",
                detail: `${result.imported} ${result.imported === 1 ? "entry" : "entries"} appended to MEMORY.md`,
              }
            : row,
        ),
      );
      router.refresh();
    } catch (err) {
      const message = err instanceof Error ? err.message : "Import failed.";
      setError(message);
      setRows((current) =>
        current.map((row) =>
          files.some((file) => file.name === row.name)
            ? { ...row, status: "error", detail: message }
            : row,
        ),
      );
    }
  }

  return (
    <section className="panel">
      <div
        className={`dropzone${dragging ? " dropzone-active" : ""}`}
        onDragEnter={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDragOver={(event) => event.preventDefault()}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          void importFiles(event.dataTransfer.files);
        }}
      >
        <div>
          <h3>drop files here</h3>
          <p className="meta">{ACCEPTED.join(", ")}</p>
          <label className="button primary" style={{ marginTop: 14, cursor: "pointer" }}>
            choose files
            <input
              type="file"
              accept={ACCEPTED.join(",")}
              multiple
              hidden
              onChange={(event) => void importFiles(event.target.files)}
            />
          </label>
        </div>
      </div>
      {error ? <p className="meta danger" style={{ marginTop: 12 }}>{error}</p> : null}
      {rows.length > 0 ? (
        <div className="list" style={{ marginTop: 16 }}>
          {rows.map((row, index) => (
            <div className="record" key={`${row.name}-${index}`}>
              <div>
                <h3>{row.name}</h3>
                <p className="meta">{row.detail ?? row.status}</p>
              </div>
            </div>
          ))}
        </div>
      ) : null}
      {rows.some((row) => row.status === "imported") ? (
        <div className="actions" style={{ marginTop: 16 }}>
          <Link className="button primary" href="/memories">
            view memories
          </Link>
        </div>
      ) : null}
    </section>
  );
}