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
  crawl: "Lecture du site",
  match: "Comparaison",
  index: "Indexation",
  report: "Rapport",
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
