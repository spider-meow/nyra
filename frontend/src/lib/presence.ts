import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { api } from "./api";
import { useOrg } from "./org";

/** Where a page stands for a visual (see `backend/nyra/cloud/presence.py`). */
export type PageState = "still" | "gone" | "unknown";
export type Counts = Record<PageState, number>;
export type Section = Counts & { section: string };
export type Version = Counts & { site_id: string; label: string; site_url: string; sections: Section[] };
export type Narrowing = { site?: string; section?: string; state?: PageState };

export type Presence = {
  totals: Counts & { pages: number };
  versions: Version[];
  /** The pages for the narrowing, `total` of them, from `offset`. */
  pages: { url: string; site_id: string; section: string; state: PageState }[];
  total: number;
  offset: number;
};

export const PAGE_SIZE = 50;

/** The pages of a visual still online, gone or unknown, with the counts per site version and section. The key starts
 * with "occurrences": what a finished read refreshes. */
export function usePresence(referenceId: string, narrowing: Narrowing, offset: number) {
  const { apiPath, brand } = useOrg();
  const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
  for (const [name, value] of Object.entries(narrowing)) if (value) params.set(name, value);
  return useQuery({
    queryKey: ["occurrences", brand.id, "presence", referenceId, narrowing, offset],
    queryFn: () => api.get<Presence>(apiPath(`/visuals/${referenceId}/presence?${params}`)),
    placeholderData: keepPreviousData,
  });
}
