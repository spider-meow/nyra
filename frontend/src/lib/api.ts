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
let extraHeaders: (path: string) => Record<string, string> = () => ({});

/** `headers`: what else a request to `path` carries (the role a platform admin chose to see a space with, lib/viewAs.ts). */
export function bindSession(provider: TokenProvider, unauthorized: () => void, headers: (path: string) => Record<string, string> = () => ({})): void {
  tokenProvider = provider;
  onUnauthorized = unauthorized;
  extraHeaders = headers;
}

export const UNREACHABLE = "Impossible de joindre le serveur. Vérifiez votre connexion, puis réessayez.";
export const UNAVAILABLE = "Le serveur ne répond pas pour le moment : il redémarre ou il est surchargé. Patientez une minute, puis réessayez. Si cela dure, prévenez l'équipe Nyra.";
const SERVER_ERROR = "Une erreur est survenue côté serveur. Réessayez ; si elle revient, prévenez l'équipe Nyra.";

/** Whether an error says the server did not answer at all (a gateway error from the proxy, or no connection): what an overloaded or restarting server looks like. */
export function isUnanswered(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 0 || error.status === 502 || error.status === 503 || error.status === 504);
}

/** What to tell the person when the API did not give a reason itself (the proxy's gateway errors have no body worth showing). */
function fallbackMessage(status: number): string {
  if (status === 502 || status === 503 || status === 504) return UNAVAILABLE;
  return status >= 500 ? SERVER_ERROR : "La requête a échoué.";
}

/** `replay` false: a 429 is final, whatever its Retry-After (an action that says to come back in minutes, not in seconds). */
async function request<T>(path: string, init: RequestInit = {}, replay = true): Promise<T> {
  let token: string | null;
  let response: Response;
  for (let left = 3; ; left--) {
    const headers = new Headers(init.headers);
    token = await tokenProvider(); // read again for each try: it may expire during the waits
    if (token) headers.set("Authorization", `Bearer ${token}`);
    for (const [name, value] of Object.entries(extraHeaders(path))) headers.set(name, value);
    if (typeof init.body === "string") headers.set("Content-Type", "application/json"); // `api` sends JSON text or FormData only
    try {
      response = await fetch(`/api${path}`, { ...init, headers });
    } catch {
      throw new ApiError(UNREACHABLE, 0); // no connection, or the connection was cut: the browser's own message ("Failed to fetch") says nothing
    }
    // Only the rate limiter (cloud/ratelimit.py) is replayed: it refuses with 429 and always sets Retry-After, before the
    // route runs, so nothing was done and replaying is safe. Another 429 (the daily invitation limit) has no Retry-After
    // and is final. A file upload (FormData) is not replayed: the server has read the whole body when it refuses, so each try would resend the files.
    const seconds = Number(response.headers.get("Retry-After"));
    if (response.status !== 429 || !(seconds > 0) || init.body instanceof FormData || !replay || !left) break;
    await new Promise((resume) => setTimeout(resume, Math.min(seconds * 1000, 3e4)));
  }
  const text = await response.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = response.status >= 500 ? null : { detail: text }; // a page of the proxy or of the framework is not for the person
    }
  }
  if (!response.ok) {
    // An expired or revoked session: send the user back to the login page.
    if (response.status === 401 && token) onUnauthorized();
    const detail = data && typeof data === "object" && "detail" in data ? data.detail : null;
    throw new ApiError(typeof detail === "string" && detail ? detail : fallbackMessage(response.status), response.status);
  }
  return data as T; // trust boundary: the shape of the answer is the API contract, only the caller knows T
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown, replay = true) =>
    request<T>(path, { method: "POST", body: body instanceof FormData ? body : JSON.stringify(body ?? {}) }, replay),
  put: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "PUT", body: body instanceof FormData ? body : JSON.stringify(body) }),
  del: <T>(path: string) => request<T>(path, { method: "DELETE" }),
};

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "La requête a échoué.";
}
