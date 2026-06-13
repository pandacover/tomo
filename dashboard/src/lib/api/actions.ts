import { controlApiAuthHeaders, resolveControlApiUrl } from "@/lib/api/config";
import { controlApiRoutes } from "@/lib/api/contract";
import { importMemoryResponseSchema } from "@/lib/api/schemas";
import type { MemoryEntry } from "@/domain/memory";

class ControlApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ControlApiError";
  }
}

async function controlRequest<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const base = resolveControlApiUrl();
  if (!base) {
    throw new ControlApiError("Control API is not configured.", 0);
  }

  const headers = new Headers(init.headers);
  const auth = controlApiAuthHeaders() as Record<string, string>;
  if (auth.Authorization) {
    headers.set("Authorization", auth.Authorization);
  }
  if (init.body && !(init.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(`${base}${path}`, {
    ...init,
    headers,
  });

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const payload = (await response.json()) as { detail?: string };
      if (payload.detail) detail = payload.detail;
    } catch {
      // ignore parse errors
    }
    throw new ControlApiError(detail, response.status);
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return response.json() as Promise<T>;
}

export async function importMemoriesAction(
  files: File[],
): Promise<{ imported: number; entries: MemoryEntry[] }> {
  const form = new FormData();
  for (const file of files) {
    form.append("files", file);
  }
  const payload = await controlRequest<unknown>(controlApiRoutes.memoriesImport, {
    method: "POST",
    body: form,
  });
  return importMemoryResponseSchema.parse(payload);
}
