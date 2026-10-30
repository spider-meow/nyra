import type { Scope } from "./collections";

/**
 * What the library's address says (AXE-633): `?collection=<id>` (`none` for the unfiled visuals, nothing for the whole library) and `?visual=<id>`, the
 * visual whose sheet is open. Identifiers, never names: renaming or moving a collection breaks no link. Pure functions, no React:
 * `npm run check:address` runs them. A value that points at nothing is the caller's to report ("Cette collection n'existe plus").
 */
const MAX_LENGTH = 64; // identifiers are 36 characters; anything longer is not one

const asked = (value: string | null): string | null => (value && value.length <= MAX_LENGTH ? value : null);

export type LibraryAddress = { scope: Scope; visual: string | null };

export function readAddress(params: URLSearchParams): LibraryAddress {
  return { scope: asked(params.get("collection")) ?? "all", visual: asked(params.get("visual")) };
}

/** The same address with the scope and/or the visual changed ("all" and null take the parameter out); every other parameter is kept. */
export function writeAddress(params: URLSearchParams, change: { scope?: Scope; visual?: string | null }): URLSearchParams {
  const next = new URLSearchParams(params);
  if (change.scope === "all") next.delete("collection");
  else if (change.scope !== undefined) next.set("collection", change.scope);
  if (change.visual === null) next.delete("visual");
  else if (change.visual !== undefined) next.set("visual", change.visual);
  return next;
}

/** The link to a visual's sheet: the library's own path (`/o/<org>/b/<brand>/library`) with its identifier, and nothing else (no name, no signed address). */
export function visualLink(origin: string, libraryPath: string, id: string): string {
  return `${origin}${libraryPath}?${writeAddress(new URLSearchParams(), { visual: id })}`;
}
