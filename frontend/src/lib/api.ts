export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

type TokenProvider = () => Promise<string | null>;

let tokenProvider: TokenProvider = async () => null;
let onUnauthorized: () => void = () => {};

export function bindSession(provider: TokenProvider, unauthorized: () => void): void {
  tokenProvider = provider;
  onUnauthorized = unauthorized;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const token = await tokenProvider();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(`/api${path}`, { ...init, headers });
  const text = await response.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = { detail: text };
    }
  }
  if (!response.ok) {
    // An expired or revoked session: send the user back to the login page.
    if (response.status === 401 && token) onUnauthorized();
    const detail = data && typeof data === "object" && "detail" in data ? (data as { detail: unknown }).detail : null;
    throw new ApiError(typeof detail === "string" ? detail : "La requête a échoué.", response.status);
  }
  return data as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body instanceof FormData ? body : JSON.stringify(body ?? {}) }),
  put: <T>(path: string, body: unknown) => request<T>(path, { method: "PUT", body: JSON.stringify(body) }),
  del: <T>(path: string) => request<T>(path, { method: "DELETE" }),
};

/** Download an authenticated file (the browser can't add the bearer token to a plain link). */
export async function downloadFile(path: string, filename: string): Promise<void> {
  const token = await tokenProvider();
  const response = await fetch(`/api${path}`, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (!response.ok) throw new ApiError("Le téléchargement a échoué.", response.status);
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "La requête a échoué.";
}
