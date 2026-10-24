export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError"; // the SDK reads the error's name: scrubbing recognizes an ApiError wherever it is wrapped
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
  let response: Response;
  for (let retry = 0; ; retry++) {
    response = await fetch(`/api${path}`, { ...init, headers });
    // The API's rate limit answers 429 before the route runs (cloud/ratelimit.py, an app-wide dependency): nothing was
    // done, so replaying is safe for a POST too. Waiting spreads a burst (many images excluded at once) over time.
    if (response.status !== 429 || retry === 3) break;
    const seconds = Number(response.headers.get("Retry-After")); // at most 30 s; 2 s when absent or not a positive number
    await new Promise((resume) => setTimeout(resume, Math.min(seconds > 0 ? seconds : 2, 30) * 1000));
    init.signal?.throwIfAborted(); // cancelled during the wait: stop before any new call
  }
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
    const detail = data && typeof data === "object" && "detail" in data ? data.detail : null;
    throw new ApiError(typeof detail === "string" ? detail : "La requête a échoué.", response.status);
  }
  return data as T; // trust boundary: the shape of the answer is the API contract, only the caller knows T
}

export const api = {
  get: <T>(path: string, init?: RequestInit) => request<T>(path, init),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body instanceof FormData ? body : JSON.stringify(body ?? {}) }),
  put: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "PUT", body: body instanceof FormData ? body : JSON.stringify(body) }),
  del: <T>(path: string) => request<T>(path, { method: "DELETE" }),
};

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "La requête a échoué.";
}
