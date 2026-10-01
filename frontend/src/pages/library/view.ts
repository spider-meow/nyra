import { useMemo, useState } from "react";
import { LIBRARY_PAGE } from "../../lib/queries";
import { isBoolean, isShortText, isStrings, oneOf, usePersistedState } from "../../lib/storage";
import type { LibraryItem, Status } from "../../types";

export type Filter = "all" | Status | "unindexed";
type Sort = "expiry" | "name";
const isTab = oneOf<Tab>(["active", "expired"]);
const isSort = oneOf<Sort>(["expiry", "name"]);
const isFilter = oneOf<Filter>(["all", "expire", "<30j", "<90j", "ok", "inconnue", "unindexed"]);
/** Expired visuals live in their own tab: still compared to the sites, out of the way of the working library. */
export type Tab = "active" | "expired";
export const TAGS_SHOWN = 12;

/** Tags with the number of visuals carrying each, most used first. */
function tagCounts(items: LibraryItem[]): [string, number][] {
  const counts = new Map<string, number>();
  for (const item of items) for (const tag of item.tags) counts.set(tag, (counts.get(tag) ?? 0) + 1);
  return [...counts].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], "fr"));
}

type Criteria = { tab: Tab; filter: Filter; sort: Sort; query: string; activeTags: string[]; untagged: boolean };

function visibleItems(pool: LibraryItem[], { tab, filter, sort, query, activeTags, untagged }: Criteria): LibraryItem[] {
  const needle = query.trim().toLowerCase();
  const list = pool.filter((item) => {
    if (needle && !item.filename.toLowerCase().includes(needle) && !item.tags.some((tag) => tag.includes(needle))) return false;
    if (untagged && item.tags.length) return false;
    if (activeTags.some((tag) => !item.tags.includes(tag))) return false;
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
export function useLibraryView(items: LibraryItem[]) {
  // Tab and sort are kept in this browser; search and filters only for the browser tab (reload, come back later).
  const [tab, setTab] = usePersistedState<Tab>("library.tab", "active", "local", isTab);
  const [sort, setSort] = usePersistedState<Sort>("library.sort", "expiry", "local", isSort);
  const [filterSaved, setFilter] = usePersistedState<Filter>("library.filter", "all", "session", isFilter);
  const [query, setQuery] = usePersistedState("library.query", "", "session", isShortText);
  const [tagsSaved, setActiveTags] = usePersistedState<string[]>("library.tags", [], "session", isStrings);
  const [untaggedSaved, setUntagged] = usePersistedState("library.untagged", false, "session", isBoolean);
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
  const visible = useMemo(() => visibleItems(pool, { tab, filter, sort, query, activeTags, untagged }), [pool, tab, filter, sort, query, activeTags, untagged]);

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
    tab, filter, sort, query, activeTags, untagged, allTags, shown,
    activeItems, expiredItems, libraryTags, poolTags, untaggedCount, visible,
    filtering: filter !== "all" || Boolean(query) || activeTags.length > 0 || untagged,
    changeTab, toggleTag, setSort,
    toggleAllTags: () => setAllTags((value) => !value),
    toggleUntagged: () => { setUntagged(!untagged); setShown(LIBRARY_PAGE); },
    setFilter: (value: Filter) => { setFilter(value); setShown(LIBRARY_PAGE); },
    setQuery: (value: string) => { setQuery(value); setShown(LIBRARY_PAGE); },
    showMore: () => setShown((value) => value + LIBRARY_PAGE),
  };
}

export type LibraryView = ReturnType<typeof useLibraryView>;
