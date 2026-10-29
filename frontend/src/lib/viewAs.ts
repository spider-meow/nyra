/**
 * « Voir en tant que » (AXE-634): a platform admin who opens a client's space may see it as an administrator or as a client. The
 * SERVER applies the role (`X-Nyra-View-As`, see `_staff_member` in routes/common.py): a client gets real 403s, and is read-only
 * unless the actions are allowed (`X-Nyra-View-Write`). This file is the interface side, apart from React so that
 * `npm run check:viewas` can run it. It imports nothing: `api.ts` reads it, and `org.tsx` too.
 */

export type ViewAs = { orgId: string; role: "admin" | "client"; write: boolean };

const KEY = "nyra:view-as";

/** The headers of a request to the API (`path` without `/api`): only requests of the organization being viewed carry the role. */
export function viewAsHeaders(view: ViewAs | null, path: string): Record<string, string> {
  if (!view || !path.startsWith(`/orgs/${view.orgId}/`)) return {};
  return view.role === "client" && view.write ? { "X-Nyra-View-As": "client", "X-Nyra-View-Write": "1" } : { "X-Nyra-View-As": view.role };
}

/** What the screens may show. `isStaff`: the Nyra team. With a role chosen, the team's own settings go as well, as at the client's. */
export function rightsFor(view: ViewAs | null, isStaff: boolean, memberRole: string): { admin: boolean; platformAdmin: boolean } {
  if (!view) return { admin: memberRole === "admin" || isStaff, platformAdmin: isStaff };
  return { admin: view.role === "admin", platformAdmin: false };
}

export function parseViewAs(value: unknown): ViewAs | null {
  if (typeof value !== "object" || value === null) return null;
  const { orgId, role, write } = value as Record<string, unknown>;
  return typeof orgId === "string" && (role === "admin" || role === "client") && typeof write === "boolean" ? { orgId, role, write } : null;
}

// Shared state, kept for the tab (sessionStorage), read by every request: the one exception to « pas d'état global », since a request
// must know the role without a component. It holds a role and an organization, never a token.
function stored(): ViewAs | null {
  try {
    return parseViewAs(JSON.parse(sessionStorage.getItem(KEY) ?? "null"));
  } catch {
    return null; // blocked or corrupted storage: no role chosen (best effort, like lib/storage.ts)
  }
}

let current: ViewAs | null = stored();
const listeners = new Set<() => void>();

export const getViewAs = (): ViewAs | null => current;

export function setViewAs(next: ViewAs | null): void {
  current = next;
  try {
    if (next) sessionStorage.setItem(KEY, JSON.stringify(next));
    else sessionStorage.removeItem(KEY);
  } catch {
    // storage blocked: the role lasts until the page is reloaded
  }
  listeners.forEach((listener) => listener());
}

export function subscribeViewAs(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
