import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type {
  CurrentJobs,
  Decision,
  ImportRow,
  Job,
  LibraryItem,
  Matches,
  Overview,
  Report,
  Scan,
  Settings,
} from "../types";
import { api } from "./api";
import { useOrg } from "./org";

export function useOverview() {
  const { apiPath, org } = useOrg();
  return useQuery({
    queryKey: ["overview", org.org_id],
    queryFn: () => api.get<Overview>(apiPath("/overview")),
  });
}

/** The only thing that polls: every 2 s while something runs, every 20 s otherwise. */
export function useJobs() {
  const { apiPath, org } = useOrg();
  return useQuery({
    queryKey: ["jobs", org.org_id],
    queryFn: () => api.get<CurrentJobs>(apiPath("/jobs/current")),
    refetchInterval: (query) => ((query.state.data?.active.length ?? 0) > 0 ? 2000 : 20_000),
  });
}

export function useLibrary() {
  const { apiPath, org } = useOrg();
  return useQuery({
    queryKey: ["library", org.org_id],
    queryFn: () => api.get<{ items: LibraryItem[]; indexing: boolean }>(apiPath("/library")),
    // Signed image URLs last an hour; refresh well before that.
    staleTime: 60_000,
    refetchInterval: 30 * 60_000,
  });
}

export function useMatches(withinDays: number) {
  const { apiPath, org } = useOrg();
  return useQuery({
    queryKey: ["matches", org.org_id, withinDays],
    queryFn: () => api.get<Matches>(apiPath(`/matches?within_days=${withinDays}`)),
    placeholderData: (previous) => previous,
    staleTime: 60_000,
    refetchInterval: 30 * 60_000,
  });
}

export function useScans() {
  const { apiPath, org } = useOrg();
  return useQuery({ queryKey: ["scans", org.org_id], queryFn: () => api.get<{ scans: Scan[] }>(apiPath("/scans")) });
}

export function useReports() {
  const { apiPath, org } = useOrg();
  return useQuery({
    queryKey: ["reports", org.org_id],
    queryFn: () => api.get<{ reports: Report[] }>(apiPath("/reports")),
    refetchInterval: 30 * 60_000,
  });
}

export function useSettings() {
  const { apiPath, org } = useOrg();
  return useQuery({ queryKey: ["settings", org.org_id], queryFn: () => api.get<Settings>(apiPath("/settings")) });
}

/** What to refresh when a job of each kind finishes. */
export const refreshAfter: Record<Job["kind"], string[]> = {
  crawl: ["overview", "matches", "scans", "library"],
  match: ["overview", "matches", "library"],
  index: ["overview", "matches", "library"],
  report: ["reports"],
};

export function useInvalidate() {
  const client = useQueryClient();
  const { org } = useOrg();
  return (...keys: string[]) => {
    for (const key of keys) void client.invalidateQueries({ queryKey: [key, org.org_id] });
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

export function useCancelJob() {
  const { apiPath } = useOrg();
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (jobId: string) => api.post<{ job: Job }>(apiPath(`/jobs/${jobId}/cancel`)),
    onSuccess: () => invalidate("jobs"),
  });
}

type ReviewInput = { referenceId: string; siteImageIds: string[]; decision: Decision | "" };

/** Decisions apply instantly on screen and roll back if the server refuses. */
export function useReview(withinDays: number) {
  const { apiPath, org } = useOrg();
  const client = useQueryClient();
  const key = ["matches", org.org_id, withinDays];
  return useMutation({
    mutationFn: (input: ReviewInput) =>
      api.post(apiPath("/reviews"), {
        reference_id: input.referenceId,
        site_image_ids: input.siteImageIds,
        decision: input.decision,
      }),
    onMutate: async (input) => {
      await client.cancelQueries({ queryKey: key });
      const previous = client.getQueryData<Matches>(key);
      if (previous) {
        const ids = new Set(input.siteImageIds);
        const patch = (groups: Matches["confirmed"]) =>
          groups.map((group) =>
            group.reference_id !== input.referenceId
              ? group
              : {
                  ...group,
                  hits: group.hits.map((hit) =>
                    hit.site_image_ids.some((id) => ids.has(id)) ? { ...hit, decision: input.decision || null } : hit,
                  ),
                },
          );
        client.setQueryData<Matches>(key, {
          ...previous,
          confirmed: patch(previous.confirmed),
          to_verify: patch(previous.to_verify),
          later: patch(previous.later),
        });
      }
      return { previous };
    },
    onError: (_error, _input, context) => {
      if (context?.previous) client.setQueryData(key, context.previous);
    },
    onSettled: () => void client.invalidateQueries({ queryKey: ["overview", org.org_id] }),
  });
}

export function useLibraryMutations() {
  const { apiPath } = useOrg();
  const invalidate = useInvalidate();
  const done = () => invalidate("library", "overview", "matches", "jobs");
  return {
    upload: useMutation({
      mutationFn: (files: File[]) => {
        const body = new FormData();
        for (const file of files) body.append("files", file);
        return api.post<{ saved: string[]; failed: { filename: string; reason: string }[] }>(apiPath("/library/upload"), body);
      },
      onSuccess: done,
    }),
    updateMeta: useMutation({
      mutationFn: (input: { filename: string; expiry_date: string; credit: string; notes: string }) =>
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
  const { apiPath } = useOrg();
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (overrides: Settings["overrides"]) => api.put(apiPath("/settings"), { overrides }),
    onSuccess: () => invalidate("settings", "overview"),
  });
}
