const MAX_LENGTH = 63;

/** Same rules as nyra.cloud.db.slugify: lowercase ascii, digits, single hyphens. */
export function slugify(value: string): string {
  const folded = value.trim().toLowerCase().normalize("NFKD").replace(/[\u0300-\u036f]/g, "");
  const ascii = folded.replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
  return ascii.slice(0, MAX_LENGTH).replace(/-+$/g, "");
}

/** Like slugify, but keeps a trailing hyphen so "remy-" can still become "remy-martin". */
export function slugifyDraft(value: string): string {
  const folded = value.toLowerCase().normalize("NFKD").replace(/[\u0300-\u036f]/g, "");
  const ascii = folded.replace(/[^a-z0-9-]+/g, "-").replace(/-+/g, "-").replace(/^-+/, "");
  return ascii.slice(0, MAX_LENGTH);
}
