/**
 * The interface's addresses used to be French (October 2026: `/o/<org>/m/<brand>/bibliotheque`). They are English
 * now (`/o/<org>/b/<brand>/library`), and every old address keeps working: bookmarks, notes, and above all the
 * invitation and password e-mails already sent, whose sign-in key sits in the fragment (`#access_token=…`).
 * `legacyTarget` turns an old address into the new one, keeping the fragment and renaming the query parameters.
 * Pure functions, no React: `npm run check:paths` runs them on every old address.
 */

/** Pages of a brand: old segment -> new one. */
export const LEGACY_PAGES: Record<string, string> = {
  "tableau-de-bord": "dashboard",
  "a-traiter": "review",
  bibliotheque: "library",
  "images-du-site": "online-images",
  lectures: "scans",
  rapports: "reports",
  statistiques: "statistics",
  reglages: "settings",
};

/** Pages of a brand renamed since the addresses are English: old segment -> new one (`/o/<org>/b/<brand>/site-images`). */
export const RENAMED_PAGES: Record<string, string> = { "site-images": "online-images" };

/** Pages of the back office (`/interne/…`, now `/back-office/…`). */
export const LEGACY_BACK_OFFICE: Record<string, string> = { organisations: "organizations", facturation: "billing", comptes: "accounts" };

/** Names of the query parameters. Their values are data (`expire`, `retenu`…) and do not change. */
export const LEGACY_PARAMS: Record<string, string> = { fenetre: "window", statut: "status", onglet: "tab", pays: "country" };

/** Sections of the settings (`?section=`), including the names they had before (`marques`, then `profil`). */
export const LEGACY_SECTIONS: Record<string, string> = { marques: "general", profil: "general", equipe: "team", lecture: "reading", compte: "account" };

const renamed = (table: Record<string, string>, value: string): string => (Object.hasOwn(table, value) ? table[value] : value);

/** A query string with the old parameter names (and old settings sections) renamed; the rest kept as it was. */
export function legacySearch(search: string): string {
  const next = new URLSearchParams();
  for (const [key, value] of new URLSearchParams(search)) {
    const name = renamed(LEGACY_PARAMS, key);
    next.append(name, name === "section" ? renamed(LEGACY_SECTIONS, value) : value);
  }
  const text = next.toString();
  return text ? `?${text}` : "";
}

/** A page segment of a brand, old or new, as it is now (`bibliotheque` -> `library`, `library` -> `library`). */
export const currentPage = (segment: string): string => renamed(LEGACY_PAGES, segment);

/** The new segments of an old path, or null when the path is not an old one. */
function legacySegments(segments: string[]): string[] | null {
  const [head, ...rest] = segments;
  if (head === "connexion" && !rest.length) return ["login"];
  if (head === "mot-de-passe" && !rest.length) return ["set-password"];
  if (head === "interne") return ["back-office", ...rest.map((segment, index) => (index === 0 ? renamed(LEGACY_BACK_OFFICE, segment) : segment))];
  if (head !== "o" || !rest.length) return null;
  const [org, kind, brand, page, ...tail] = rest;
  if (kind === "b" && brand && page !== undefined && Object.hasOwn(RENAMED_PAGES, page)) return ["o", org, "b", brand, RENAMED_PAGES[page], ...tail];
  if (kind === "m") return brand ? ["o", org, "b", brand, ...(page ? [currentPage(page), ...tail] : [])] : ["o", org];
  // From before brands: /o/<org>/bibliotheque (the organization's first brand takes it from there).
  if (kind !== undefined && Object.hasOwn(LEGACY_PAGES, kind)) return ["o", org, LEGACY_PAGES[kind], ...rest.slice(2)];
  return null;
}

/** The new address of an old one (`pathname`, `search` and `hash` as `location` gives them), or null if it is not old. */
export function legacyTarget(pathname: string, search = "", hash = ""): string | null {
  const segments = legacySegments(pathname.split("/").filter(Boolean));
  return segments ? `/${segments.join("/")}${legacySearch(search)}${hash}` : null;
}
