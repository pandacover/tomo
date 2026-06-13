export function resolveControlApiUrl(): string | null {
  const raw = process.env.NEXT_PUBLIC_CONTROL_API_URL?.trim();
  if (!raw) return null;
  const url = raw.split(/\s+/)[0].replace(/\/$/, "");
  try {
    new URL(url);
    return url;
  } catch {
    return null;
  }
}

export function controlApiConfigured(): boolean {
  return resolveControlApiUrl() !== null;
}

export function controlApiAuthHeaders(): HeadersInit {
  const key = process.env.NEXT_PUBLIC_CONTROL_API_KEY;
  if (!key) return {};
  return { Authorization: `Bearer ${key}` };
}