import type { Collection } from "../types";

/** `ids` with the one at `from` taken out and put back so that it ends at index `to`. Out-of-range places are clamped. */
export function moved(ids: string[], from: number, to: number): string[] {
  if (from < 0 || from >= ids.length) return ids;
  const target = Math.min(Math.max(to, 0), ids.length - 1);
  const rest = ids.filter((_, index) => index !== from);
  return [...rest.slice(0, target), ids[from], ...rest.slice(target)];
}

/** The slot a drag points at: 0 = before the first row, `rows.length` = after the last. A row counts as passed once the pointer is below its middle. */
export function slotAt(rows: { top: number; bottom: number }[], y: number): number {
  return rows.filter((row) => y > (row.top + row.bottom) / 2).length;
}

/** The index the dragged row ends at when it is dropped in `slot` (the slots count the row's own place: dropping right before or after itself changes nothing). */
export const landing = (slot: number, from: number): number => (slot > from ? slot - 1 : slot);

/** The collections with the children of `parentId` in the order of `ids`, the ones `ids` leaves out after them; every other collection keeps its place. Ids that are not children of `parentId` are ignored. */
export function withOrder(collections: Collection[], parentId: string | null, ids: string[]): Collection[] {
  const children = new Map(collections.filter((collection) => collection.parent_id === parentId).map((collection) => [collection.id, collection]));
  const ordered = [...ids.flatMap((id) => children.get(id) ?? []), ...[...children.values()].filter((child) => !ids.includes(child.id))];
  return [...collections.filter((collection) => collection.parent_id !== parentId), ...ordered];
}
