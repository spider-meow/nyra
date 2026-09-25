import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useToast } from "../components/feedback";
import type {
  Brand,
  CurrentJobs,
  Decision,
  Exclusion,
  ImportRow,
  Job,
  LibraryItem,
  Matches,
  Overview,
  Report,
  Scan,
  Settings,
  Site,
  SiteImage,
} from "../types";
import { ApiError, api, errorMessage } from "./api";
import { plural } from "./format";
import { useOrg } from "./org";

export function useOverview() {
  const { apiPath, brand } = useOrg();
  return useQuery({
    queryKey: ["overview", brand.id],
    queryFn: () => api.get<Overview>(apiPath("/overview")),
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

export function useLibrary() {
  const { apiPath, brand } = useOrg();
  return useQuery({
    queryKey: ["library", brand.id],
    queryFn: () => api.get<{ items: LibraryItem[]; indexing: boolean }>(apiPath("/library")),
    // Signed image URLs last an hour; refresh well before that.
    staleTime: 60_000,
    refetchInterval: 30 * 60_000,
  });
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
      onSuccess: () => invalidate("site-images", "library", "matches", "overview", "jobs"),
    }),
  };
}

export function useSites() {
  const { apiPath, brand } = useOrg();
  return useQuery({ queryKey: ["sites", brand.id], queryFn: () => api.get<{ sites: Site[] }>(apiPath("/sites")) });
}

/** What to refresh when a job of each kind finishes. */
export const refreshAfter: Record<Job["kind"], string[]> = {
  crawl: ["overview", "matches", "scans", "library", "site-images", "sites"],
  match: ["overview", "matches", "library", "site-images"],
  index: ["overview", "matches", "library", "site-images"],
  report: ["reports"],
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
  const { apiPath, brand } = useOrg();
  const client = useQueryClient();
  const key = ["matches", brand.id, withinDays];
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
    onSettled: () => void client.invalidateQueries({ queryKey: ["overview", brand.id] }),
  });
}

// A serverless host refuses request bodies above ~4.5 MB: send a few files at a time.
const BATCH_BYTES = 3_500_000;
const BATCH_FILES = 8;

function batches(files: File[]): File[][] {
  const out: File[][] = [];
  let current: File[] = [];
  let size = 0;
  for (const file of files) {
    if (current.length && (size + file.size > BATCH_BYTES || current.length >= BATCH_FILES)) {
      out.push(current);
      current = [];
      size = 0;
    }
    current.push(file);
    size += file.size;
  }
  if (current.length) out.push(current);
  return out;
}

export type UploadResult = {
  saved: string[];
  /** Also in `saved`: a visual of that name was already in the library and has been replaced. */
  replaced: string[];
  failed: { filename: string; reason: string }[];
};

/**
 * Adds references a few at a time, with a progress toast that stays while
 * it runs and a summary when it's done. Rows appear in the library as each
 * batch lands; closing the tab mid-way asks first.
 */
export function useReferenceUpload() {
  const { apiPath, brand } = useOrg();
  const invalidate = useInvalidate();
  const toast = useToast();
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);

  async function run(files: File[]): Promise<UploadResult> {
    const result: UploadResult = { saved: [], replaced: [], failed: [] };
    // Two files with the same name would overwrite each other: send the first, say so for the others.
    const seen = new Set<string>();
    const unique: File[] = [];
    for (const file of files) {
      if (seen.has(file.name)) {
        result.failed.push({ filename: file.name, reason: "Même nom qu'un autre fichier de cet envoi : un seul exemplaire a été gardé." });
      } else {
        seen.add(file.name);
        unique.push(file);
      }
    }
    files = unique;
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
        const body = new FormData();
        for (const file of batch) body.append("files", file);
        try {
          const data = await api.post<UploadResult>(apiPath("/library/upload"), body);
          result.saved.push(...data.saved);
          result.replaced.push(...(data.replaced ?? []));
          result.failed.push(...data.failed);
        } catch (error) {
          const reason =
            error instanceof ApiError && error.status === 413
              ? "Fichier trop lourd pour être envoyé (4 Mo au plus par fichier sur ce serveur)."
              : errorMessage(error);
          result.failed.push(...batch.map((file) => ({ filename: file.name, reason })));
        }
        done += batch.length;
        setProgress({ done, total });
        if (total > 1) toast.update(id, { message: `Envoi des visuels · ${done} sur ${total}`, progress: done / total });
        invalidate("library");
      }
    } finally {
      window.removeEventListener("beforeunload", warn);
      setProgress(null);
      invalidate("library", "overview", "matches", "jobs");
    }

    // Every file sent must come back as saved or refused; anything else is reported, never dropped silently.
    const answered = new Set([...result.saved, ...result.failed.map((item) => item.filename)]);
    for (const file of files) {
      if (!answered.has(file.name)) result.failed.push({ filename: file.name, reason: "Le serveur n'a rien répondu pour ce fichier." });
    }

    const saved = result.saved.length;
    const replaced = result.replaced.length;
    const failed = result.failed.length;
    const added = saved - replaced;
    if (saved && !failed && !replaced) {
      toast.update(id, {
        tone: "success",
        message: saved > 1 ? `${saved} visuels ajoutés` : "Visuel ajouté",
        description: "L'indexation démarre, puis la comparaison avec les sites de la marque se fait toute seule.",
        progress: undefined,
      });
    } else if (saved) {
      toast.update(id, {
        tone: failed ? "info" : "success",
        message: `${saved} sur ${saved + failed} ${saved + failed > 1 ? "fichiers enregistrés" : "fichier enregistré"}`,
        description: [
          added ? plural(added, "nouveau", "nouveaux") : "",
          replaced ? `${replaced} ${replaced > 1 ? "remplacés" : "remplacé"} (même nom déjà présent)` : "",
          failed ? plural(failed, "refusé", "refusés") : "",
        ].filter(Boolean).join(" · ") + ". Le détail est en haut de la bibliothèque.",
        progress: undefined,
      });
    } else {
      toast.update(id, {
        tone: "error",
        message: failed > 1 ? "Aucun visuel n'a pu être ajouté" : "Le visuel n'a pas pu être ajouté",
        description: failed > 1 ? "Le détail est affiché en haut de la bibliothèque." : result.failed[0]?.reason,
        progress: undefined,
      });
    }
    return result;
  }

  return { run, progress, uploading: progress !== null };
}

export function useLibraryMutations() {
  const { apiPath } = useOrg();
  const invalidate = useInvalidate();
  const done = () => invalidate("library", "overview", "matches", "jobs");
  return {
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
  const done = () => invalidate("exclusions", "matches", "overview", "jobs", "site-images");
  return {
    add: useMutation({
      mutationFn: (input: { siteImageId: string; reason: string }) =>
        api.post<{ matches_removed: number }>(apiPath("/exclusions"), { site_image_id: input.siteImageId, reason: input.reason }),
      onSuccess: done,
    }),
    remove: useMutation({
      mutationFn: (id: string) => api.del(apiPath(`/exclusions/${id}`)),
      onSuccess: done,
    }),
  };
}
