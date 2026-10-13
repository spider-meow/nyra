import type { Collection } from "../../types";

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
  return { byId, children, path, subtree };
}
export type CollectionIndex = ReturnType<typeof collectionIndex>;

/** "all": every visual; "none": the unfiled ones; otherwise a collection id. */
export type Scope = "all" | "none" | string;
