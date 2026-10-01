import { useEffect, useState } from "react";
import { useAuth } from "./auth";
import { useOrg } from "./org";

/**
 * Browser storage is a convenience, never a requirement. Every access below is
 * wrapped in try/catch on purpose: private mode, a full quota or disabled
 * storage must not break the app. This is the documented best-effort exception
 * to "never swallow an error". Nothing sensitive goes in: no token, no
 * credential. Every key starts with `nyra:` so sign-out can remove them all.
 */
const PREFIX = "nyra:";
const KINDS = ["local", "session"] as const;
type StorageKind = (typeof KINDS)[number];
const area = (kind: StorageKind) => (kind === "local" ? window.localStorage : window.sessionStorage);

/** Key scoped to the signed-in user and the brand on screen. */
export const storageKey = (userId: string, brandId: string, name: string) => `${PREFIX}${userId}:${brandId}:${name}`;

export function readStored<T>(kind: StorageKind, key: string, validate: (value: unknown) => value is T): T | undefined {
  try {
    const raw = area(kind).getItem(key);
    const value: unknown = raw === null ? undefined : JSON.parse(raw);
    return validate(value) ? value : undefined;
  } catch {
    return undefined; // unreadable, corrupted or blocked: behave as if nothing was stored
  }
}

/** A value whose JSON exceeds `maxChars` is not stored (and the previous copy is dropped). */
export function writeStored(kind: StorageKind, key: string, value: unknown, maxChars = 50_000): void {
  try {
    const raw = JSON.stringify(value);
    if (raw.length > maxChars) area(kind).removeItem(key);
    else area(kind).setItem(key, raw);
  } catch {
    // quota exceeded or storage blocked: the page keeps working without it
  }
}

export function removeStored(kind: StorageKind, key: string): void {
  try {
    area(kind).removeItem(key);
  } catch {
    // blocked storage: nothing to remove
  }
}

/** Sign-out: remove every `nyra:` key (Supabase's own session key is left to Supabase). */
export function clearStored(): void {
  for (const kind of KINDS) {
    try {
      const store = area(kind);
      const ours: string[] = [];
      for (let index = 0; index < store.length; index++) {
        const key = store.key(index);
        if (key?.startsWith(PREFIX)) ours.push(key);
      }
      for (const key of ours) store.removeItem(key);
    } catch {
      // blocked storage: nothing was written
    }
  }
}

export const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value);
export const isStrings = (value: unknown): value is string[] => Array.isArray(value) && value.length <= 100 && value.every((item) => typeof item === "string");
export const isBoolean = (value: unknown): value is boolean => typeof value === "boolean";
export const isShortText = (value: unknown): value is string => typeof value === "string" && value.length <= 200;
export const oneOf = <T extends string>(allowed: readonly T[]) => (value: unknown): value is T => allowed.some((item) => item === value);

/**
 * useState that survives a reload. A stored value that fails `validate` (stale
 * after a code change, edited by hand) is ignored in favour of `initial`.
 * Scoped to the signed-in user and the brand: another account or brand starts clean.
 */
export function usePersistedState<T>(name: string, initial: T, kind: StorageKind, validate: (value: unknown) => value is T): [T, (value: T) => void] {
  const { session } = useAuth();
  const { brand } = useOrg();
  const user = session?.user.id;
  const key = user ? storageKey(user, brand.id, name) : "";
  const load = () => ({ key, value: (key ? readStored(kind, key, validate) : undefined) ?? initial });
  const [state, setState] = useState(load);
  // The brand (or user) changed under a mounted page: read that brand's own value.
  const current = state.key === key ? state : load();
  if (current !== state) setState(current);
  useEffect(() => {
    if (current.key) writeStored(kind, current.key, current.value);
  }, [kind, current.key, current.value]);
  return [current.value, (value) => setState({ key: current.key, value })];
}
