import { keepPreviousData, useMutation, useQuery } from "@tanstack/react-query";
import type { Job, Status } from "../types";
import { api } from "./api";
import { useOrg } from "./org";
import { useInvalidate, useJobs } from "./queries";

/** Where a page stands for a visual (see `backend/nyra/cloud/presence.py`). */
export type PageState = "still" | "gone" | "unknown";
export type Counts = Record<PageState, number> & { marked: number };
export type Section = Counts & { section: string };
export type Version = Counts & { site_id: string; label: string; site_url: string; sections: Section[]; /** The last read of this site looks incomplete: nothing is "gone". */ suspect: boolean };
export type Narrowing = { site?: string; section?: string; state?: PageState; marked?: boolean };

export type Presence = {
  totals: Counts & { pages: number };
  versions: Version[];
  /** The pages for the narrowing, `total` of them, from `offset`. */
  pages: { url: string; site_id: string; section: string; state: PageState; marked: boolean; checked: boolean; /** A consent notice still hid the page: not read, never "gone". */ walled: boolean;
    /** Where a page that answered with another page ended (null: it did not), and how many HTTP redirects led there. Absent from an older server. */ redirect_url?: string | null; redirect_hops?: number | null }[];
  total: number;
  offset: number;
};

export const PAGE_SIZE = 50;

/** The pages of a visual still online, gone or unknown, with the counts per site version and section. The key starts
 * with "occurrences": what a finished read refreshes. */
export function usePresence(referenceId: string, narrowing: Narrowing, offset: number) {
  const { apiPath, brand } = useOrg();
  const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
  for (const [name, value] of Object.entries(narrowing)) if (value !== undefined) params.set(name, String(value));
  return useQuery({
    queryKey: ["occurrences", brand.id, "presence", referenceId, narrowing, offset],
    queryFn: () => api.get<Presence>(apiPath(`/visuals/${referenceId}/presence?${params}`)),
    placeholderData: keepPreviousData,
  });
}

/** A visual to replace: its pages per state; `replaced` once every page was read again and none shows it. */
export type VisualItem = Counts & {
  reference_id: string;
  filename: string;
  expiry_date: string | null;
  days_left: number | null;
  status: Status;
  thumb: string;
  pages: number;
  replaced: boolean;
};

/** The visuals a person confirmed, with how many pages still show each one. The key starts with "occurrences": what a
 * finished read or a decision refreshes. */
export function useVisuals() {
  const { apiPath, brand } = useOrg();
  return useQuery({
    queryKey: ["occurrences", brand.id, "visuals"],
    queryFn: () => api.get<{ visuals: VisualItem[]; suspect_sites: { site_id: string; label: string; site_url: string; reason: string }[] }>(apiPath("/visuals")),
  });
}

/** The most pages one check reads (the server refuses more). */
export const MAX_CHECKED_PAGES = 50;

/** Reads again only these pages (a person changed a visual on them): a job, whose end refreshes the figures. */
export function useCheckPages() {
  const { apiPath } = useOrg();
  const invalidate = useInvalidate();
  const checking = (useJobs().data?.active ?? []).some((job) => job.kind === "check");
  const check = useMutation({
    mutationFn: (urls: string[]) => api.post<{ job: Job }>(apiPath("/jobs/check"), { urls }),
    onSettled: () => invalidate("jobs"),
  });
  return { check, busy: check.isPending || checking };
}

/** Marks pages as replaced for a visual (where the person changed it by hand), or takes the mark off. */
export function useMarkReplaced(referenceId: string) {
  const { apiPath } = useOrg();
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (input: { urls: string[]; replaced: boolean }) => api.post(apiPath(`/visuals/${referenceId}/replaced`), input),
    onSettled: () => invalidate("occurrences"),
  });
}
