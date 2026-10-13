import { useMemo, useState } from "react";
import { LIBRARY_PAGE } from "../../lib/queries";
import { isBoolean, isShortText, isStrings, oneOf, usePersistedState } from "../../lib/storage";
import type { Collection, ImageLabel, LibraryItem, Status } from "../../types";
import { collectionIndex, type CollectionIndex, type Scope } from "./collections";

export type Filter = "all" | Status | "unindexed";
type Sort = "expiry" | "name";
const isTab = oneOf<Tab>(["active", "expired"]);
const isSort = oneOf<Sort>(["expiry", "name"]);
const isFilter = oneOf<Filter>(["all", "expire", "<30j", "<90j", "ok", "inconnue", "illimite", "unindexed"]);
/** Expired visuals live in their own tab: still compared to the sites, out of the way of the working library. */
export type Tab = "active" | "expired";
export const TAGS_SHOWN = 12;

/** Tags with the number of visuals carrying each, most used first. */
function tagCounts(items: LibraryItem[]): [string, number][] {
  const counts = new Map<string, number>();
  for (const item of items) for (const tag of item.tags) counts.set(tag, (counts.get(tag) ?? 0) + 1);
  return [...counts].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], "fr"));
}

type Criteria = { tab: Tab; filter: Filter; sort: Sort; query: string; activeTags: string[]; untagged: boolean; content: string; scope: Scope; subtree: Set<string>; folders: CollectionIndex };
type LabelsOf = (id: string) => ImageLabel[];

/** Every word typed must be found somewhere: the name, a tag, the collection path or the credit. "classic decanter" finds a visual tagged "classic" and "decanter" as well as one tagged "classic decanter". */
function searchText(item: LibraryItem, folders: CollectionIndex): string {
  return [item.filename, ...item.tags, item.collection_id ? folders.path(item.collection_id) : "", item.credit].join(" ").toLowerCase();
}

function visibleItems(pool: LibraryItem[], { tab, filter, sort, query, activeTags, untagged, content, scope, subtree, folders }: Criteria, labelsOf: LabelsOf): LibraryItem[] {
  const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const list = pool.filter((item) => {
    // Typing searches the whole library, like a brand center: the folder being looked at does not narrow it.
    if (words.length) {
      const text = `${searchText(item, folders)} ${labelsOf(item.id).map((label) => label.name).join(" ").toLowerCase()}`;
      if (!words.every((word) => text.includes(word))) return false;
    } else if (scope === "none" ? item.collection_id : scope !== "all" && !(item.collection_id && subtree.has(item.collection_id))) return false;
    if (untagged && item.tags.length) return false;
    if (activeTags.some((tag) => !item.tags.includes(tag))) return false;
    if (content !== "all" && !labelsOf(item.id).some((label) => label.label_id === content)) return false;
    if (filter === "unindexed") return !item.indexed;
    return filter === "all" || item.status === filter;
  });
  // Working library: closest deadline first. Expired tab: most recently expired first.
  return [...list].sort((a, b) => {
    if (sort === "name") return a.filename.localeCompare(b.filename, "fr");
    const left = a.days_left ?? Number.MAX_SAFE_INTEGER;
    const right = b.days_left ?? Number.MAX_SAFE_INTEGER;
    return (tab === "expired" ? right - left : left - right) || a.filename.localeCompare(b.filename, "fr");
  });
}

/** Tab, status filter, sort, search and tag filters of the library, and the visuals they leave. Every filter change goes back to the first page. */
export function useLibraryView(items: LibraryItem[], collections: Collection[], labelsOf: LabelsOf) {
  // Tab and sort are kept in this browser; search and filters only for the browser tab (reload, come back later).
  const [tab, setTab] = usePersistedState<Tab>("library.tab", "active", "local", isTab);
  const [sort, setSort] = usePersistedState<Sort>("library.sort", "expiry", "local", isSort);
  const [filterSaved, setFilter] = usePersistedState<Filter>("library.filter", "all", "session", isFilter);
  const [query, setQuery] = usePersistedState("library.query", "", "session", isShortText);
  const [tagsSaved, setActiveTags] = usePersistedState<string[]>("library.tags", [], "session", isStrings);
  const [untaggedSaved, setUntagged] = usePersistedState("library.untagged", false, "session", isBoolean);
  const [content, setContent] = usePersistedState("library.content", "all", "session", isShortText);
  const [scopeSaved, setScopeSaved] = usePersistedState<Scope>("library.scope", "all", "session", isShortText);
  const [allTags, setAllTags] = useState(false);
  const [shown, setShown] = useState(LIBRARY_PAGE);

  const expiredItems = useMemo(() => items.filter((item) => item.status === "expire"), [items]);
  const activeItems = useMemo(() => items.filter((item) => item.status !== "expire"), [items]);
  const pool = tab === "expired" ? expiredItems : activeItems;
  // Tags of the whole library (suggestions) and of the tab being looked at (filters), most used first.
  const libraryTags = useMemo(() => tagCounts(items), [items]);
  const poolTags = useMemo(() => tagCounts(pool), [pool]);
  const untaggedCount = useMemo(() => pool.filter((item) => !item.tags.length).length, [pool]);
  // A remembered filter can outlive what it points at (tag removed, other tab): only apply what the screen can show and undo.
  const activeTags = useMemo(() => tagsSaved.filter((tag) => poolTags.some(([name]) => name === tag)), [tagsSaved, poolTags]);
  const untagged = untaggedSaved && untaggedCount > 0;
  const filter = tab === "active" ? filterSaved : "all";
  const folders = useMemo(() => collectionIndex(collections), [collections]);
  // A remembered folder can have been deleted since: fall back to the whole library.
  const scope: Scope = scopeSaved === "all" || scopeSaved === "none" || folders.byId.has(scopeSaved) ? scopeSaved : "all";
  const subtree = useMemo(() => new Set(scope === "all" || scope === "none" ? [] : folders.subtree(scope)), [scope, folders]);
  // Visuals of the tab per folder (a visual counts for its folder and every folder above it), for the tree.
  const folderCounts = useMemo(() => {
    const counts: Record<string, number> = { all: pool.length, none: 0 };
    for (const item of pool) {
      if (!item.collection_id) counts.none += 1;
      for (let id: string | null = item.collection_id; id; id = folders.byId.get(id)?.parent_id ?? null) counts[id] = (counts[id] ?? 0) + 1;
    }
    return counts;
  }, [pool, folders]);
  const visible = useMemo(() => visibleItems(pool, { tab, filter, sort, query, activeTags, untagged, content, scope, subtree, folders }, labelsOf), [pool, tab, filter, sort, query, activeTags, untagged, content, scope, subtree, folders, labelsOf]);
  // How many visuals of the tab carry each content label (a label that no visual carries has no chip to filter on).
  const contentCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const item of pool) for (const label of labelsOf(item.id)) counts[label.label_id] = (counts[label.label_id] ?? 0) + 1;
    return counts;
  }, [pool, labelsOf]);

  function changeTab(next: Tab) {
    setTab(next);
    setFilter("all");
    setActiveTags([]);
    setUntagged(false);
    setAllTags(false);
    setShown(LIBRARY_PAGE);
  }

  function toggleTag(tag: string) {
    setActiveTags(activeTags.includes(tag) ? activeTags.filter((item) => item !== tag) : [...activeTags, tag]);
    setShown(LIBRARY_PAGE);
  }

  return {
    tab, filter, sort, query, activeTags, untagged, content, contentCounts, allTags, shown, scope, folders, folderCounts,
    activeItems, expiredItems, libraryTags, poolTags, untaggedCount, visible,
    filtering: filter !== "all" || scope !== "all" || Boolean(query) || activeTags.length > 0 || untagged || content !== "all",
    changeTab, toggleTag, setSort,
    setScope: (value: Scope) => { setScopeSaved(value); setShown(LIBRARY_PAGE); },
    toggleAllTags: () => setAllTags((value) => !value),
    setContent: (value: string) => { setContent(value); setShown(LIBRARY_PAGE); },
    toggleUntagged: () => { setUntagged(!untagged); setShown(LIBRARY_PAGE); },
    setFilter: (value: Filter) => { setFilter(value); setShown(LIBRARY_PAGE); },
    setQuery: (value: string) => { setQuery(value); setShown(LIBRARY_PAGE); },
    showMore: () => setShown((value) => value + LIBRARY_PAGE),
  };
}

export type LibraryView = ReturnType<typeof useLibraryView>;
