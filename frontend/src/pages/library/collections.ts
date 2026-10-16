import type { Collection } from "../../types";

export const MAX_DEPTH = 3; // same as the API

/** The library's folders as a lookup: children by parent, and the "A › B › C" path of any collection. */
export function collectionIndex(collections: Collection[]) {
  const byId = new Map(collections.map((collection) => [collection.id, collection]));
  const children = new Map<string | null, Collection[]>();
  for (const collection of collections) children.set(collection.parent_id, [...(children.get(collection.parent_id) ?? []), collection]);
  for (const list of children.values()) list.sort((a, b) => a.name.localeCompare(b.name, "fr", { numeric: true }));

  const path = (id: string): string => {
    const collection = byId.get(id);
    return collection ? (collection.parent_id ? `${path(collection.parent_id)} › ${collection.name}` : collection.name) : "";
  };
  /** The collection and everything below it. */
  const subtree = (id: string): string[] => [id, ...(children.get(id) ?? []).flatMap((child) => subtree(child.id))];
  /** Levels from the top down to this collection (a top-level one is 1), like the API. */
  const depth = (id: string): number => {
    let levels = 0;
    for (let current = byId.get(id); current && levels < MAX_DEPTH; current = current.parent_id ? byId.get(current.parent_id) : undefined) levels += 1;
    return levels;
  };
  return { byId, children, path, subtree, depth };
}
export type CollectionIndex = ReturnType<typeof collectionIndex>;

/** Where a collection can go: the top level, or any collection that is not in its own subtree and leaves the whole thing within three levels. The current place is left out. */
export function moveTargets(folders: CollectionIndex, collection: Collection): { id: string | null; label: string }[] {
  const below = folders.subtree(collection.id);
  const height = Math.max(...below.map((id) => folders.depth(id))) - folders.depth(collection.id) + 1;
  const targets = [...folders.byId.values()]
    .filter((target) => !below.includes(target.id) && folders.depth(target.id) + height <= MAX_DEPTH)
    .map((target): { id: string | null; label: string } => ({ id: target.id, label: folders.path(target.id) }))
    .sort((a, b) => a.label.localeCompare(b.label, "fr", { numeric: true }));
  return [{ id: null, label: "Premier niveau" }, ...targets].filter((target) => target.id !== collection.parent_id);
}

/** "all": every visual; "none": the unfiled ones; otherwise a collection id. */
export type Scope = "all" | "none" | string;
