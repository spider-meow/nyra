import type { LibraryItem } from "../../types";

/** What the selected visuals hold, for the hints of the Actions menu and the notes of its dialogs. */
export function summarize(items: LibraryItem[]) {
  return {
    count: items.length,
    withDate: items.filter((item) => item.expiry_date).length,
    withCredit: items.filter((item) => item.credit).length,
    unlimited: items.filter((item) => item.unlimited_rights).length,
    tags: new Set(items.flatMap((item) => item.tags)).size,
    unclassified: items.filter((item) => !item.collection_id).length,
    collections: new Set(items.flatMap((item) => (item.collection_id ? [item.collection_id] : []))).size,
  };
}

/** "12 ont déjà une date", "aucun n'a de date", "tous ont déjà une date": one sentence for the date row. */
export function dateHint(count: number, withDate: number): string {
  if (withDate === 0) return count > 1 ? "Aucun n'a de date" : "Il n'a pas de date";
  if (withDate === count) return count > 1 ? "Tous ont déjà une date" : "Il a déjà une date";
  return `${withDate} sur ${count} ont déjà une date`;
}

/** Where the selection is today, for the collection row. */
export function whereHint(unclassified: number, collections: number): string {
  if (!collections) return "Aucun n'est classé";
  const where = collections > 1 ? `${collections} collections` : "1 collection";
  return unclassified ? `${where}, ${unclassified} non classé${unclassified > 1 ? "s" : ""}` : `Aujourd'hui dans ${where}`;
}

/** What the selection already has as a photo credit, for the credit row. */
export function creditHint(count: number, withCredit: number): string {
  if (withCredit === 0) return "Aucun n'a de crédit";
  if (withCredit === count) return count > 1 ? "Tous ont déjà un crédit" : "Il a déjà un crédit";
  return `${withCredit} sur ${count} ont déjà un crédit`;
}
