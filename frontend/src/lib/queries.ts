import { keepPreviousData, skipToken, useMutation, useQuery, useQueryClient, type QueryClient, type QueryKey } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useToast } from "../components/feedback";
import type {
  Brand,
  CrawlEstimate,
  CurrentJobs,
  Decision,
  Exclusion,
  Hit,
  ImportRow,
  Job,
  LibraryItem,
  Matches,
  Member,
  Occurrences,
  OrgInsights,
  Overview,
  Report,
  Scan,
  Settings,
  Site,
  SiteImage,
  Status,
} from "../types";
import { api, errorMessage } from "./api";
import { useAuth } from "./auth";
import { useOrg } from "./org";
import { isRecord, oneOf, readStored, removeOtherBrands, removeStored, storageKey, writeStored } from "./storage";
import { batches, reportUnanswered, sendBatch, summary, withoutDuplicates, type UploadResult } from "./upload";

// Copies of the Library and Overview answers in localStorage, so those pages open at once.
// They hold no signed URL (thumbnails come from `useLibraryThumbs`). Older than this, a copy is ignored.
const COPY_MAX_AGE_MS = 5 * 60_000;

/**
 * The last server answer for `name`, per user and brand, to hand to `initialData`.
 * `initialDataUpdatedAt: 0` (at the call sites) makes TanStack treat it as stale,
 * so the page shows the copy and refetches immediately.
 * Only the latest brand keeps a copy per user and `name`: a user going through many large brands cannot fill the storage.
 */
function useAnswerCopy<T>(name: string, isAnswer: (value: unknown) => value is T, maxChars: number) {
  const { session } = useAuth();
  const { brand } = useOrg();
  const key = session ? storageKey(session.user.id, brand.id, `copy.${name}`) : "";
  const isCopy = (value: unknown): value is { savedAt: number; answer: T } =>
    isRecord(value) && typeof value.savedAt === "number" && isAnswer(value.answer);
  return {
    read(): T | undefined {
      const copy = key ? readStored("local", key, isCopy) : undefined;
      const age = copy ? Date.now() - copy.savedAt : -1;
      if (copy && age >= 0 && age <= COPY_MAX_AGE_MS) return copy.answer;
      if (key) removeStored("local", key); // too old or unreadable: do not keep signed URLs around
      return undefined;
    },
    write(answer: T): void {
      if (!key || !session) return;
      removeOtherBrands("local", session.user.id, key, `copy.${name}`);
      writeStored("local", key, { savedAt: Date.now(), answer }, maxChars);
    },
  };
}

const isOverview = (value: unknown): value is Overview =>
  isRecord(value) &&
  isRecord(value.brand) && typeof value.brand.id === "string" &&
  isRecord(value.organization) &&
  isRecord(value.stats) &&
  isRecord(value.dashboard) && Array.isArray(value.dashboard.upcoming) &&
  isRecord(value.jobs) && Array.isArray(value.jobs.active) &&
  Array.isArray(value.sites) &&
  isRecord(value.defaults) && typeof value.defaults.within_days === "number";

const isStatus = oneOf<Status>(["expire", "<30j", "<90j", "ok", "inconnue"]);
const isLibraryItem = (value: unknown): value is LibraryItem =>
  isRecord(value) &&
  typeof value.id === "string" && typeof value.filename === "string" && typeof value.expiry_date === "string" &&
  (value.days_left === null || typeof value.days_left === "number") &&
  isStatus(value.status) &&
  typeof value.credit === "string" && typeof value.notes === "string" &&
  Array.isArray(value.tags) && value.tags.every((tag) => typeof tag === "string") &&
  typeof value.indexed === "boolean" && typeof value.compared === "boolean";

type LibraryAnswer = { items: LibraryItem[]; indexing: boolean };
const isLibraryAnswer = (value: unknown): value is LibraryAnswer =>
  isRecord(value) && typeof value.indexing === "boolean" && Array.isArray(value.items) && value.items.every(isLibraryItem);

export function useOverview() {
  const { apiPath, brand } = useOrg();
  const copy = useAnswerCopy("overview", isOverview, 100_000);
  return useQuery({
    queryKey: ["overview", brand.id],
    queryFn: async ({ signal }) => {
      const answer = await api.get<Overview>(apiPath("/overview"));
      if (!signal.aborted) copy.write(answer); // aborted = signed out meanwhile: leave nothing behind
      return answer;
    },
    initialData: copy.read,
    initialDataUpdatedAt: 0,
  });
}

/** The only thing that polls: every 2 s while something runs, every 20 s otherwise. */
export function useJobs() {
  const { apiPath, brand } = useOrg();
  return useQuery({
    queryKey: ["jobs", brand.id],
    queryFn: () => api.get<CurrentJobs>(apiPath("/jobs/current")),
    refetchInterval: (query) => ((query.state.data?.active.length ?? 0) > 0 ? 2000 : 20_000),
  });
}

/** How long the next read would take, from the past ones; asks again when the choice changes or a job ends. */
export function useCrawlEstimate(options: { siteIds: string[]; maxPages: number | undefined; fresh: boolean; thenMatch: boolean }) {
  const { admin, apiPath, brand } = useOrg();
  const { siteIds, maxPages, fresh, thenMatch } = options;
  const params = new URLSearchParams({ fresh: String(fresh), then_match: String(thenMatch) });
  siteIds.forEach((id) => params.append("site_ids", id));
  if (maxPages) params.set("max_pages", String(maxPages));
  return useQuery({
    queryKey: ["estimate", brand.id, params.toString()],
    queryFn: () => api.get<CrawlEstimate>(`${apiPath("/jobs/estimate")}?${params}`),
    enabled: admin && siteIds.length > 0 && siteIds.length <= 50,
    placeholderData: keepPreviousData,
    staleTime: 60_000,
  });
}

// ~1,000 references weigh ~0.2 MB (metadata only); above this cap nothing is stored.
const LIBRARY_COPY_MAX_CHARS = 1_500_000;

export function useLibrary() {
  const { apiPath, brand } = useOrg();
  const copy = useAnswerCopy("library", isLibraryAnswer, LIBRARY_COPY_MAX_CHARS);
  return useQuery({
    queryKey: ["library", brand.id],
    queryFn: async ({ signal }) => {
      const answer = await api.get<LibraryAnswer>(apiPath("/library"));
      if (!signal.aborted) copy.write(answer); // aborted = signed out meanwhile: leave nothing behind
      return answer;
    },
    initialData: copy.read,
    initialDataUpdatedAt: 0,
    staleTime: 60_000,
  });
}

/** Cards shown at a time in the library, and the most thumbnail names asked of the server in one request. */
export const LIBRARY_PAGE = 100;

/** Thumbnail URL of a visual (undefined: it has none) and when the server gave it. Signed URLs last an hour, the server may reuse one with 10 minutes left. */
type Thumbs = Record<string, { url: string | undefined; fetchedAt: number }>;
const noThumbs: Thumbs = {};
const THUMB_MAX_AGE_MS = 25 * 60_000;
const CLOCK_TICK_MS = 5 * 60_000;

/**
 * Signed thumbnail URLs (filename -> URL) of the cards on screen. The URLs known for the brand are kept in the query cache;
 * only the displayed names missing from it (or older than 25 minutes) are asked for, `LIBRARY_PAGE` at a time and one request after the other.
 * A list shift or a filter that reveals known cards therefore costs nothing. A card without a URL shows a placeholder.
 */
export function useLibraryThumbs(shown: string[]): { urls: Record<string, string>; failed: boolean } {
  const { apiPath, brand } = useOrg();
  const client = useQueryClient();
  const mapKey = ["library-thumbs", brand.id];
  const known = useQuery({ queryKey: mapKey, queryFn: skipToken, initialData: noThumbs }).data ?? noThumbs;
  // Re-evaluate the ages from time to time: a tab left open for hours must renew its URLs.
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), CLOCK_TICK_MS);
    return () => clearInterval(timer);
  }, []);
  const batch = shown.filter((name) => !known[name] || now - known[name].fetchedAt > THUMB_MAX_AGE_MS).slice(0, LIBRARY_PAGE);
  const request = useQuery({
    queryKey: [...mapKey, batch],
    queryFn: async () => {
      const { urls } = await api.post<{ urls: Record<string, string> }>(apiPath("/library/thumbs"), { filenames: batch });
      const fetchedAt = Date.now();
      // Only names still in the library are kept, so the map stays as small as the library.
      const present = new Set(client.getQueryData<{ items: LibraryItem[] }>(["library", brand.id])?.items.map((item) => item.filename));
      client.setQueryData<Thumbs>(mapKey, (old) => {
        const next = { ...old, ...Object.fromEntries(batch.map((name) => [name, { url: urls[name], fetchedAt }])) };
        return present.size ? Object.fromEntries(Object.entries(next).filter(([name]) => present.has(name))) : next;
      });
      return urls;
    },
    enabled: batch.length > 0,
    gcTime: 0, // a batch is only worth asking again once its names are missing again (replaced file): never serve it from the cache
    refetchInterval: 30 * 60_000, // retries a failed batch; a succeeded one is replaced by the next batch at once
  });
  const urls: Record<string, string> = {};
  for (const name of shown) {
    const url = known[name]?.url;
    if (url) urls[name] = url;
  }
  return { urls, failed: request.isError };
}

/** Visuals just saved over an existing or deleted name: forget their thumbnail and original URL so the next display asks the server again. */
export function useForgetImages() {
  const client = useQueryClient();
  const { brand } = useOrg();
  return (filenames: string[]) => {
    const gone = new Set(filenames);
    client.setQueryData<Thumbs>(["library-thumbs", brand.id], (old) => old && Object.fromEntries(Object.entries(old).filter(([name]) => !gone.has(name))));
    for (const name of gone) void client.invalidateQueries({ queryKey: ["library-url", brand.id, name] });
  };
}

export function useMatches(withinDays: number) {
  const { apiPath, brand } = useOrg();
  return useQuery({
    queryKey: ["matches", brand.id, withinDays],
    queryFn: () => api.get<Matches>(apiPath(`/matches?within_days=${withinDays}`)),
    placeholderData: (previous) => previous,
    staleTime: 60_000,
    refetchInterval: 30 * 60_000,
  });
}

export function useScans() {
  const { apiPath, brand } = useOrg();
  return useQuery({ queryKey: ["scans", brand.id], queryFn: () => api.get<{ scans: Scan[] }>(apiPath("/scans")) });
}

export function useReports() {
  const { apiPath, brand } = useOrg();
  return useQuery({
    queryKey: ["reports", brand.id],
    queryFn: () => api.get<{ reports: Report[] }>(apiPath("/reports")),
    refetchInterval: 30 * 60_000,
  });
}

/** Settings belong to the organization and apply to all its brands. */
export function useInsights() {
  const { apiPath, brand } = useOrg();
  return useQuery({
    queryKey: ["insights", brand.id],
    queryFn: () => api.get<OrgInsights>(apiPath("/insights")),
    staleTime: 60_000,
  });
}

export function useSettings() {
  const { orgApiPath, brand } = useOrg();
  return useQuery({ queryKey: ["settings", brand.id], queryFn: () => api.get<Settings>(orgApiPath("/settings")) });
}

export function useSiteImages() {
  const { apiPath, brand } = useOrg();
  return useQuery({
    queryKey: ["site-images", brand.id],
    queryFn: () => api.get<{ items: SiteImage[] }>(apiPath("/site-images")),
    // Signed image URLs last an hour; refresh well before that.
    staleTime: 60_000,
    refetchInterval: 30 * 60_000,
  });
}

export function useSiteImageMutations() {
  const { apiPath } = useOrg();
  const invalidate = useInvalidate();
  return {
    adopt: useMutation({
      mutationFn: (input: { site_image_ids: string[]; expiry_date: string; credit: string; filenames: Record<string, string> }) =>
        api.post<{
          added: { site_image_id: string; filename: string }[];
          failed: { site_image_id: string; url: string; reason: string }[];
        }>(apiPath("/site-images/adopt"), input),
      onSuccess: () => invalidate("occurrences", "site-images", "library", "matches", "overview", "jobs"),
    }),
  };
}

export function useSites() {
  const { apiPath, brand } = useOrg();
  return useQuery({ queryKey: ["sites", brand.id], queryFn: () => api.get<{ sites: Site[] }>(apiPath("/sites")) });
}

/** What to refresh when a job of each kind finishes. */
export const refreshAfter: Record<Job["kind"], string[]> = {
  crawl: ["occurrences", "overview", "matches", "scans", "library", "site-images", "sites", "insights", "estimate"],
  match: ["occurrences", "overview", "matches", "library", "site-images", "insights", "estimate"],
  index: ["occurrences", "overview", "matches", "library", "site-images", "insights"],
  report: ["reports", "insights"],
  locate: ["occurrences"],
};

export function useInvalidate() {
  const client = useQueryClient();
  const { brand } = useOrg();
  return (...keys: string[]) => {
    for (const key of keys) void client.invalidateQueries({ queryKey: [key, brand.id] });
  };
}

export function useStartJob() {
  const { apiPath } = useOrg();
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (input: { kind: "crawl" | "match" | "report"; body?: unknown }) => {
      const path = input.kind === "report" ? "/reports" : `/jobs/${input.kind}`;
      return api.post<{ job: Job }>(apiPath(path), input.body);
    },
    onSuccess: () => invalidate("jobs"),
  });
}

/** The pages showing one reference (its crops included), `limit` of them from `offset`. */
export function useOccurrences(filename: string, offset: number, limit: number, tier?: "review") {
  const { apiPath, brand } = useOrg();
  return useQuery({
    queryKey: ["occurrences", brand.id, filename, offset, tier ?? "all"],
    queryFn: () => api.get<Occurrences>(`${apiPath(`/library/${encodeURIComponent(filename)}/occurrences`)}?limit=${limit}&offset=${offset}${tier ? `&tier=${tier}` : ""}`),
    placeholderData: keepPreviousData,
  });
}

/** Searches every image already read for one reference; the answer comes back as a job (see `useJobs`). */
export function useLocate() {
  const { apiPath } = useOrg();
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (filename: string) => api.post<{ job: Job }>(apiPath(`/library/${encodeURIComponent(filename)}/locate`)),
    onSuccess: () => invalidate("jobs"),
  });
}

export function useCancelJob() {
  const { apiPath } = useOrg();
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (jobId: string) => api.post<{ job: Job }>(apiPath(`/jobs/${jobId}/cancel`)),
    onSuccess: () => invalidate("jobs"),
  });
}

type ReviewInput = { referenceId: string; siteImageIds: string[]; decision: Decision | "" };

/** Sets the decision of the occurrences `next` answers for (undefined: leave as is), in every time window of the brand. */
function patchHits(client: QueryClient, key: QueryKey, referenceId: string, next: (hit: Hit) => Decision | null | undefined) {
  const patch = (groups: Matches["confirmed"]) =>
    groups.map((group) =>
      group.reference_id !== referenceId
        ? group
        : { ...group, hits: group.hits.map((hit) => { const decision = next(hit); return decision === undefined ? hit : { ...hit, decision }; }) },
    );
  client.setQueriesData<Matches>({ queryKey: key }, (data) =>
    data && { ...data, confirmed: patch(data.confirmed), to_verify: patch(data.to_verify), later: patch(data.later) },
  );
}

/** Decisions apply instantly on screen and roll back, occurrence by occurrence, if the server refuses. */
export function useReview() {
  const { apiPath, brand } = useOrg();
  const client = useQueryClient();
  // Every time window of the brand: a decision must show in all of them, not only the one on screen.
  const key = ["matches", brand.id];
  return useMutation({
    mutationFn: (input: ReviewInput) =>
      api.post(apiPath("/reviews"), {
        reference_id: input.referenceId,
        site_image_ids: input.siteImageIds,
        decision: input.decision,
      }),
    onMutate: async (input) => {
      await client.cancelQueries({ queryKey: key });
      const ids = new Set(input.siteImageIds);
      const touched = (hit: Hit) => hit.site_image_ids.some((id) => ids.has(id));
      // Only what this decision changes is put back on failure: decisions made meanwhile on other rows stay.
      const before = new Map<string, Decision | null>();
      for (const [, data] of client.getQueriesData<Matches>({ queryKey: key })) {
        for (const group of [...(data?.confirmed ?? []), ...(data?.to_verify ?? []), ...(data?.later ?? [])]) {
          if (group.reference_id === input.referenceId) for (const hit of group.hits) if (touched(hit)) before.set(hit.site_image_id, hit.decision);
        }
      }
      patchHits(client, key, input.referenceId, (hit) => (touched(hit) ? input.decision || null : undefined));
      return { before };
    },
    onError: (_error, input, context) => {
      patchHits(client, key, input.referenceId, (hit) => context?.before.get(hit.site_image_id));
    },
    onSettled: () => {
      void client.invalidateQueries({ queryKey: ["overview", brand.id] });
      void client.invalidateQueries({ queryKey: ["occurrences", brand.id] }); // a match set aside leaves the list of where a picture is used
    },
  });
}


/**
 * Adds references a few at a time, with a progress toast that stays while
 * it runs and a summary when it's done. Rows appear in the library as each
 * batch lands; closing the tab mid-way asks first.
 */
export function useReferenceUpload() {
  const { apiPath, brand } = useOrg();
  const invalidate = useInvalidate();
  const forgetImages = useForgetImages();
  const toast = useToast();
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);

  async function run(sent: File[]): Promise<UploadResult> {
    const result: UploadResult = { saved: [], replaced: [], failed: [] };
    const files = withoutDuplicates(sent, result);
    const total = files.length;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    setProgress({ done: 0, total });
    const id = toast.show({
      tone: "loading",
      message: total > 1 ? `Envoi de ${total} visuels vers ${brand.name}…` : `Envoi du visuel vers ${brand.name}…`,
      description: "Vous pouvez continuer à naviguer. Gardez simplement cet onglet ouvert.",
      progress: total > 1 ? 0 : undefined,
    });
    let done = 0;
    try {
      for (const batch of batches(files)) {
        await sendBatch(apiPath("/library/upload"), batch, result);
        done += batch.length;
        setProgress({ done, total });
        if (total > 1) toast.update(id, { message: `Envoi des visuels · ${done} sur ${total}`, progress: done / total });
        invalidate("library");
      }
    } finally {
      window.removeEventListener("beforeunload", warn);
      setProgress(null);
      invalidate("occurrences", "library", "overview", "matches", "jobs");
      forgetImages(result.saved); // same name, new picture (replaced, or deleted before): the old thumbnail and original must not stay
    }
    reportUnanswered(files, result);
    toast.update(id, summary(result));
    return result;
  }

  return { run, progress, uploading: progress !== null };
}

export function useLibraryMutations() {
  const { apiPath } = useOrg();
  const invalidate = useInvalidate();
  const done = () => invalidate("occurrences", "library", "overview", "matches", "jobs");
  return {
    updateMeta: useMutation({
      mutationFn: (input: { filename: string; expiry_date: string; credit: string; notes: string; tags: string[] }) =>
        api.put<{ expiry_date: string }>(apiPath(`/library/${encodeURIComponent(input.filename)}`), input),
      onSuccess: () => invalidate("library", "overview", "matches"),
    }),
    remove: useMutation({
      mutationFn: (filenames: string[]) => api.post<{ deleted: number }>(apiPath("/library/delete"), { filenames }),
      onSuccess: done,
    }),
    setExpiry: useMutation({
      mutationFn: (input: { filenames: string[]; expiry_date: string }) =>
        api.post<{ updated: number }>(apiPath("/library/expiry"), input),
      onSuccess: () => invalidate("library", "overview", "matches"),
    }),
    setTags: useMutation({
      mutationFn: (input: { filenames: string[]; add?: string[]; remove?: string[] }) =>
        api.post<{ updated: number }>(apiPath("/library/tags"), input),
      onSuccess: () => invalidate("library"),
    }),
    importCsv: useMutation({
      mutationFn: (input: { file: File; apply: boolean }) => {
        const body = new FormData();
        body.append("file", input.file);
        return api.post<{ rows: ImportRow[]; applied: number }>(apiPath(`/library/import-csv?apply=${input.apply}`), body);
      },
      onSuccess: (data) => {
        if (data.applied) invalidate("library", "overview", "matches");
      },
    }),
  };
}

export function useSaveSettings() {
  const { orgApiPath } = useOrg();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (overrides: Settings["overrides"]) => api.put(orgApiPath("/settings"), { overrides }),
    // Shared by every brand of the organization.
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["settings"] });
      void client.invalidateQueries({ queryKey: ["overview"] });
    },
  });
}

export function useSiteMutations() {
  const { apiPath } = useOrg();
  const invalidate = useInvalidate();
  const done = () => invalidate("sites", "overview", "scans", "matches");
  return {
    add: useMutation({
      mutationFn: (input: { url: string; label: string }) => api.post<{ site: Site }>(apiPath("/sites"), input),
      onSuccess: done,
    }),
    rename: useMutation({
      mutationFn: (input: { id: string; label: string }) => api.put(apiPath(`/sites/${input.id}`), { label: input.label }),
      onSuccess: done,
    }),
    remove: useMutation({
      mutationFn: (id: string) => api.del(apiPath(`/sites/${id}`)),
      onSuccess: done,
    }),
  };
}

/** Brands are listed with the organizations (one request resolves both slugs of an address). */
export function useBrandMutations() {
  const { orgApiPath } = useOrg();
  const client = useQueryClient();
  const done = () => void client.invalidateQueries({ queryKey: ["orgs"] });
  return {
    create: useMutation({
      mutationFn: (input: { name: string }) => api.post<{ brand: Brand }>(orgApiPath("/brands"), input),
      onSuccess: done,
    }),
    rename: useMutation({
      mutationFn: (input: { id: string; name: string }) =>
        api.put<{ brand: Brand }>(orgApiPath(`/brands/${input.id}`), { name: input.name }),
      onSuccess: done,
    }),
    remove: useMutation({
      mutationFn: (id: string) => api.del(orgApiPath(`/brands/${id}`)),
      onSuccess: done,
    }),
  };
}

export function useMembers() {
  const { orgApiPath, org } = useOrg();
  return useQuery({ queryKey: ["members", org.org_id], queryFn: () => api.get<{ members: Member[] }>(orgApiPath("/members")) });
}

export function useMemberMutations() {
  const { orgApiPath } = useOrg();
  const client = useQueryClient();
  const done = () => void client.invalidateQueries({ queryKey: ["members"] });
  return {
    invite: useMutation({
      mutationFn: (input: { email: string; role: Member["role"] }) => api.post<{ invited: boolean }>(orgApiPath("/members"), input),
      onSuccess: done,
    }),
    setRole: useMutation({
      mutationFn: (input: { userId: string; role: Member["role"] }) => api.put(orgApiPath(`/members/${input.userId}`), { role: input.role }),
      onSuccess: done,
    }),
    remove: useMutation({ mutationFn: (userId: string) => api.del(orgApiPath(`/members/${userId}`)), onSuccess: done }),
  };
}

export function useExclusions() {
  const { apiPath, brand } = useOrg();
  return useQuery({
    queryKey: ["exclusions", brand.id],
    queryFn: () => api.get<{ exclusions: Exclusion[] }>(apiPath("/exclusions")),
  });
}

export function useExclusionMutations() {
  const { apiPath } = useOrg();
  const invalidate = useInvalidate();
  const done = () => invalidate("occurrences", "exclusions", "matches", "overview", "jobs", "site-images");
  return {
    add: useMutation({
      mutationFn: (input: { siteImageId: string; reason: string }) =>
        api.post<{ matches_removed: number }>(apiPath("/exclusions"), { site_image_id: input.siteImageId, reason: input.reason }),
      onSuccess: done,
    }),
    // Several images at once, ten requests at a time; the lists are refreshed once at the end, not once per image.
    addMany: useMutation({
      mutationFn: async (input: { siteImageIds: string[]; reason: string }) => {
        const failedIds: string[] = [];
        let firstError = "";
        for (let start = 0; start < input.siteImageIds.length; start += 10) {
          const ids = input.siteImageIds.slice(start, start + 10);
          const results = await Promise.allSettled(ids.map((id) => api.post(apiPath("/exclusions"), { site_image_id: id, reason: input.reason })));
          results.forEach((result, index) => {
            if (result.status === "fulfilled") return;
            failedIds.push(ids[index]);
            firstError ||= errorMessage(result.reason);
          });
        }
        return { failedIds, firstError };
      },
      onSettled: done,
    }),
    remove: useMutation({
      mutationFn: (id: string) => api.del(apiPath(`/exclusions/${id}`)),
      onSuccess: done,
    }),
  };
}
