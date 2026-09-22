export class ApiError extends Error {}

type TokenProvider = () => Promise<string | null>;

let orgId: string | null = null;
let tokenProvider: TokenProvider = async () => null;

export function bindApiSession(next: { orgId: string; token: TokenProvider } | null): void {
  orgId = next?.orgId ?? null;
  tokenProvider = next?.token ?? (async () => null);
}

function withOrg(url: string): string {
  if (!orgId || !url.startsWith("/api/") || url.startsWith("/api/auth/") || url.startsWith("/api/signup") || url.startsWith("/api/orgs/") || url.startsWith("/api/healthz")) {
    return url;
  }
  return `/api/orgs/${orgId}${url.slice("/api".length)}`;
}

export async function api<T>(url: string, options?: RequestInit): Promise<T> {
  const headers = new Headers(options?.headers);
  const token = await tokenProvider();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(withOrg(url), { ...options, headers });
  const text = await response.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text) as unknown;
    } catch {
      data = { detail: text };
    }
  }
  if (!response.ok) {
    const detail = data && typeof data === "object" && "detail" in data ? data.detail : null;
    throw new ApiError(typeof detail === "string" ? detail : "La requête a échoué.");
  }
  return data as T;
}

export function thumb(url: string, size = 80): string {
  if (!url || url.startsWith("http://") || url.startsWith("https://")) return url;
  const join = url.includes("?") ? "&" : "?";
  return `${url}${join}w=${size}`;
}

export async function openReport(name: "report.html" | "matches.csv" | "not_found.csv", withinDays: number): Promise<void> {
  const data = await api<{ files: Record<string, string> }>("/api/reports", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ within_days: withinDays }),
  });
  const url = data.files[name];
  if (!url) throw new ApiError("Fichier de rapport absent.");
  window.open(url, "_blank", "noopener");
}
