export class ApiError extends Error {}

export async function api<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, options);
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
  if (!url) return "";
  const join = url.includes("?") ? "&" : "?";
  return `${url}${join}w=${size}`;
}
