import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router";
import { useMatches, useOverview, useReview } from "../../lib/queries";
import { isShortText, usePersistedState } from "../../lib/storage";
import type { Decision, Hit, Matches, MatchGroup, Status } from "../../types";

export type Tab = "found" | "verify" | "missing" | "later";
export type DecisionFilter = "open" | "undecided" | Decision | "all";

export const decisionFilters: { value: DecisionFilter; label: string }[] = [
  { value: "open", label: "À traiter" },
  { value: "undecided", label: "Sans décision" },
  { value: "retenu", label: "À retirer" },
  { value: "traite", label: "Retirés" },
  { value: "ecarte", label: "Faux positifs" },
  { value: "all", label: "Toutes" },
];

export const WINDOWS = [30, 90, 180, 365, 3650];
const isWindow = (value: unknown): value is number => typeof value === "number" && WINDOWS.includes(value);

export type Row = { key: string; group: MatchGroup; hit: Hit };

export function keep(hit: Hit, filter: DecisionFilter): boolean {
  if (filter === "all") return true;
  if (filter === "open") return hit.decision === null || hit.decision === "retenu";
  if (filter === "undecided") return hit.decision === null;
  return hit.decision === filter;
}

function passes(item: { filename: string; status: Status }, statusFilter: Status | "all", needle: string): boolean {
  return (statusFilter === "all" || item.status === statusFilter) && (!needle || item.filename.toLowerCase().includes(needle));
}

/** Window, status, decision and tab filters (in the address), search text, and how many rows are shown. */
function useFilters() {
  const overview = useOverview();
  const [params, setParams] = useSearchParams();
  const defaultWindow = overview.data?.defaults.within_days ?? 90;
  // The window is remembered in this browser (0 = never chosen); status, decision and tab live in the address, so links keep working.
  const [savedWindow, setSavedWindow] = usePersistedState("review.window", 0, "local", isWindow);
  const withinDays = Number(params.get("fenetre") ?? (savedWindow || defaultWindow));
  const statusFilter = (params.get("statut") ?? "all") as Status | "all";
  const decisionFilter = (params.get("decision") ?? "open") as DecisionFilter;
  const tab = (params.get("onglet") ?? "found") as Tab;
  const [query, setQuery] = usePersistedState("review.query", "", "session", isShortText);
  const [shown, setShown] = useState(100);

  function setParam(name: string, value: string | null) {
    const next = new URLSearchParams(params);
    if (value === null) next.delete(name);
    else next.set(name, value);
    setParams(next, { replace: true });
    if (name === "fenetre" && value !== null) setSavedWindow(Number(value));
    setShown(100);
  }

  return { withinDays, statusFilter, decisionFilter, tab, query, setQuery, shown, setShown, setParam };
}

/** One row per occurrence of the tab being looked at, and the references not found. */
function useRows(data: Matches | undefined, filters: ReturnType<typeof useFilters>) {
  const { tab, statusFilter, decisionFilter } = filters;
  const needle = filters.query.trim().toLowerCase();

  const rows: Row[] = useMemo(() => {
    const source = tab === "found" ? data?.confirmed : tab === "verify" ? data?.to_verify : tab === "later" ? data?.later : [];
    const out: Row[] = [];
    for (const group of (source ?? []).filter((item) => passes(item, statusFilter, needle))) {
      for (const hit of group.hits) {
        if (keep(hit, decisionFilter)) out.push({ key: `${group.reference_id}:${hit.site_image_id}`, group, hit });
      }
    }
    return out;
  }, [data, tab, statusFilter, decisionFilter, needle]);

  const missing = useMemo(() => (data?.not_found ?? []).filter((item) => passes(item, statusFilter, needle)), [data, statusFilter, needle]);
  return { rows, missing };
}

/** The selected row, the comparison panel of narrow screens, and moving through the list. */
function useSelection(rows: Row[], shown: number, setShown: (value: number) => void) {
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);
  const selectedIndex = rows.findIndex((row) => row.key === selectedKey);
  const selected = selectedIndex >= 0 ? rows[selectedIndex] : null;

  // Keep a selection on wide screens so the comparison panel is never empty.
  useEffect(() => {
    if (!selected && rows.length && window.matchMedia("(min-width: 1024px)").matches) setSelectedKey(rows[0].key);
  }, [rows, selected]);

  function select(key: string) {
    setSelectedKey(key);
    if (!window.matchMedia("(min-width: 1024px)").matches) setPanelOpen(true);
  }

  function move(delta: number) {
    if (!rows.length) return;
    const index = selectedIndex < 0 ? 0 : Math.min(rows.length - 1, Math.max(0, selectedIndex + delta));
    setSelectedKey(rows[index].key);
    if (index >= shown) setShown(index + 20);
    listRef.current?.querySelector(`[data-key="${CSS.escape(rows[index].key)}"]`)?.scrollIntoView({ block: "nearest" });
  }

  return { selectedKey, setSelectedKey, panelOpen, setPanelOpen, listRef, selectedIndex, selected, select, move };
}

/** Everything the page shows: the answer, the filters, the rows, the selection. */
export function useReviewView() {
  const filters = useFilters();
  const matches = useMatches(filters.withinDays);
  const review = useReview(filters.withinDays);
  const { rows, missing } = useRows(matches.data, filters);
  const selection = useSelection(rows, filters.shown, filters.setShown);
  const data = matches.data;
  const counts = {
    found: data?.confirmed.length ?? 0,
    verify: data?.to_verify.length ?? 0,
    missing: data?.not_found.length ?? 0,
    later: data?.later.length ?? 0,
  };
  return { ...filters, ...selection, matches, review, rows, missing, counts, showMore: () => filters.setShown((value) => value + 100) };
}

export type ReviewView = ReturnType<typeof useReviewView>;
