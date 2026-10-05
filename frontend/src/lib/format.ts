import type { Confidence, Decision, JobKind, Status } from "../types";

export const statusLabel: Record<Status, string> = {
  expire: "Expiré",
  "<30j": "Moins de 30 jours",
  "<90j": "Moins de 90 jours",
  ok: "Dans les délais",
  inconnue: "Sans échéance",
};

export const confidenceLabel: Record<Confidence, string> = {
  haut: "Confirmé",
  moyen: "Probable",
  a_verifier: "À vérifier",
};

export const confidenceHelp: Record<Confidence, string> = {
  haut: "Même image, éventuellement redimensionnée, recompressée ou retournée.",
  moyen: "Très proche visuellement : un coup d'œil suffit à confirmer.",
  a_verifier: "Ressemblance plus faible : à contrôler avant toute action.",
};

export const decisionLabel: Record<Decision, string> = {
  retenu: "À retirer",
  ecarte: "Faux positif",
  traite: "Retiré",
};

export const jobLabel: Record<JobKind, string> = {
  crawl: "Lecture des sites",
  match: "Comparaison",
  index: "Indexation des nouveaux visuels",
  report: "Rapport",
  locate: "Recherche d'un visuel",
};

const dateFormat = new Intl.DateTimeFormat("fr-FR", { day: "numeric", month: "short", year: "numeric" });
const dateTimeFormat = new Intl.DateTimeFormat("fr-FR", {
  day: "numeric",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
});

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "·";
  const date = new Date(iso.length === 10 ? `${iso}T00:00:00` : iso);
  return Number.isNaN(date.getTime()) ? iso : dateFormat.format(date);
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "·";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : dateTimeFormat.format(date);
}

export function daysText(days: number | null): string {
  if (days === null) return "Sans échéance";
  if (days < 0) return `Expiré depuis ${Math.abs(days)} j`;
  if (days === 0) return "Expire aujourd'hui";
  return `Expire dans ${days} j`;
}

export function plural(count: number, one: string, many?: string): string {
  return `${count.toLocaleString("fr-FR")} ${count > 1 ? (many ?? `${one}s`) : one}`;
}

export function hostOf(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

export function pathOf(url: string): string {
  try {
    const parsed = new URL(url);
    return `${parsed.pathname}${parsed.search}` || "/";
  } catch {
    return url;
  }
}

// --- numbers for the statistics pages ---

const numberFormats = new Map<number, Intl.NumberFormat>();

/** A number in French notation, with at most `digits` decimals; "—" when unknown. */
export function num(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  let format = numberFormats.get(digits);
  if (!format) {
    format = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: digits });
    numberFormats.set(digits, format);
  }
  return format.format(value);
}

export function bytes(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const units = ["o", "Ko", "Mo", "Go", "To"];
  let size = value;
  let unit = 0;
  while (size >= 1000 && unit < units.length - 1) {
    size /= 1000;
    unit += 1;
  }
  return `${num(size, size < 10 && unit > 0 ? 1 : 0)} ${units[unit]}`;
}

export function duration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  if (seconds < 1) return `${num(seconds * 1000)} ms`;
  if (seconds < 60) return `${num(seconds, seconds < 10 ? 1 : 0)} s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min ${String(Math.round(seconds % 60)).padStart(2, "0")}`;
  return `${Math.floor(minutes / 60)} h ${String(minutes % 60).padStart(2, "0")}`;
}

/** An estimate, to the minute: "4 min", "1 h 05". */
export function approxDuration(seconds: number): string {
  const minutes = Math.max(1, Math.round(seconds / 60));
  return minutes < 60 ? `${minutes} min` : `${Math.floor(minutes / 60)} h ${String(minutes % 60).padStart(2, "0")}`;
}

export function percent(ratio: number | null | undefined, digits = 0): string {
  if (ratio === null || ratio === undefined) return "—";
  return `${num(ratio * 100, digits)} %`;
}

export const MAX_TAGS = 20;
export const MAX_TAG_LENGTH = 40;

/** The tags typed in a text field: split on `,` `;` `|`, trimmed, lowercase, without duplicates (as the API stores them). */
export function splitTags(text: string): string[] {
  const tags: string[] = [];
  for (const part of text.split(/[,;|]/)) {
    const tag = part.trim().replace(/\s+/g, " ").toLowerCase().slice(0, MAX_TAG_LENGTH);
    if (tag && !tags.includes(tag)) tags.push(tag);
  }
  return tags;
}
