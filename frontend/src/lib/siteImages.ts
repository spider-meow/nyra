import { useInfiniteQuery, useMutation, useQuery } from "@tanstack/react-query";
import type { SiteImagesPage, SiteImageType, SiteImageView } from "../types";
import { api } from "./api";
import { useOrg } from "./org";
import { useInvalidate } from "./queries";

// Only the pages "Images en ligne" and Réglages > Général > Logos use these: kept out of `queries.ts`, which every page loads.

export const SITE_IMAGES_PAGE = 60;

/** One tab of "Images en ligne", narrowed to a site version and a text, a page of entries at a time (`fetchNextPage`). */
export function useSiteImages(options: { view: SiteImageView; type: SiteImageType | null; site: string; text: string; min: number }) {
  const { apiPath, brand } = useOrg();
  const { view, type, site, text, min } = options;
  return useInfiniteQuery({
    queryKey: ["site-images", brand.id, view, type, site, text, min],
    queryFn: ({ pageParam }) => {
      const params = new URLSearchParams({ view, offset: String(pageParam), limit: String(SITE_IMAGES_PAGE), q: text });
      if (type) params.set("type", type);
      if (site !== "all") params.set("site", site);
      if (min) params.set("min", String(min));
      return api.get<SiteImagesPage>(`${apiPath("/site-images")}?${params}`);
    },
    initialPageParam: 0,
    getNextPageParam: (last, pages) => {
      const loaded = pages.reduce((sum, page) => sum + page.items.length, 0);
      return loaded < last.total ? loaded : undefined;
    },
    // A search or a site keeps the cards of the same tab on screen while the answer comes; another tab never shows the cards of this one.
    placeholderData: (previous, previousQuery) => (previousQuery?.queryKey[2] === view ? previous : undefined),
    // Signed image URLs last an hour; refresh well before that.
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: false,
    refetchInterval: 30 * 60_000,
  });
}

/** Put images ignored by hand back to "À qualifier" (their exclusions are deleted, a comparison is queued). */
export function useRequalify() {
  const { apiPath } = useOrg();
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (ids: string[]) => api.post<{ exclusions: number }>(apiPath("/site-images/requalify"), { site_image_ids: ids }),
    onSuccess: () => invalidate("site-images", "exclusions", "occurrences", "matches", "overview", "jobs"),
  });
}

/** The sheet of an image (AXE-740): all its pages, fifty at a time, and the visual of the library it matches. Asked once opened, not per card. */
export type SiteImageDetail = { pages: { site: string; page: string }[]; page_total: number; visual: { id: string; filename: string; score: number } | null };

export function useSiteImageDetail(id: string) {
  const { apiPath } = useOrg();
  return useInfiniteQuery({
    queryKey: ["site-images", "detail", id],
    queryFn: ({ pageParam }) => api.get<SiteImageDetail>(`${apiPath(`/site-images/${id}/detail`)}?offset=${pageParam}`),
    initialPageParam: 0,
    getNextPageParam: (last, pages) => {
      const loaded = pages.reduce((sum, page) => sum + page.pages.length, 0);
      return loaded < last.page_total ? loaded : undefined;
    },
    refetchOnWindowFocus: false,
  });
}

/** An image of the brand's sites recognised as a logo; `kept`: a person put it back into the comparison. */
export type RecognisedLogo = { id: string; url: string; thumb_url: string; width: number | null; height: number | null; score: number; pages: number; kept: boolean };

/** Whether the brand sets its logos aside, and the images recognised as logos (administrators). */
export function useLogos() {
  const { admin, apiPath, brand } = useOrg();
  return useQuery({
    queryKey: ["logos", brand.id],
    queryFn: () => api.get<{ enabled: boolean; items: RecognisedLogo[] }>(apiPath("/logos")),
    enabled: admin,
  });
}

/** The Packshots category of "Images en ligne" (AXE-527): whether the brand turned it on, whether the database can, and how many images are detected (administrators). */
export function usePackshots() {
  const { admin, apiPath, brand } = useOrg();
  return useQuery({
    queryKey: ["packshots", brand.id],
    queryFn: () => api.get<{ available: boolean; enabled: boolean; detected: number }>(apiPath("/packshots")),
    enabled: admin,
  });
}

export function usePackshotMutations() {
  const { apiPath } = useOrg();
  const invalidate = useInvalidate();
  return {
    setEnabled: useMutation({
      mutationFn: (enabled: boolean) => api.put<{ enabled: boolean; scored: number }>(apiPath("/packshots/setting"), { enabled }),
      onSuccess: () => invalidate("packshots", "site-images", "overview", "jobs"),
    }),
  };
}

/** Take one version out of its group of crops: it is not the same photo (AXE-521). It gets its own card; nothing is hidden. */
export function useSplitVariant() {
  const { apiPath } = useOrg();
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (siteImageId: string) => api.post<{ addresses: number; group_dissolved: boolean }>(apiPath("/site-images/split"), { site_image_id: siteImageId }),
    onSuccess: () => invalidate("site-images", "matches", "overview", "occurrences"),
  });
}

/** "C'est / ce n'est pas un packshot", for the Nyra team (AXE-668): kept with the detector's measures; the answer wins over the detection for that image (AXE-680). */
export function usePackshotFeedback() {
  const { apiPath } = useOrg();
  const invalidate = useInvalidate();
  const done = () => invalidate("site-images");
  return {
    give: useMutation({
      mutationFn: (input: { id: string; is_packshot: boolean; note: string }) =>
        api.put(apiPath("/packshots/feedback"), { site_image_id: input.id, is_packshot: input.is_packshot, note: input.note.trim() }),
      onSuccess: done,
    }),
    takeBack: useMutation({ mutationFn: (id: string) => api.del(apiPath(`/packshots/feedback/${id}`)), onSuccess: done }),
  };
}

export function useLogoMutations() {
  const { apiPath } = useOrg();
  const invalidate = useInvalidate();
  const done = () => invalidate("logos", "site-images", "overview", "matches", "occurrences", "jobs");
  return {
    setEnabled: useMutation({
      mutationFn: (enabled: boolean) => api.put<{ enabled: boolean; matches_removed: number }>(apiPath("/logos/setting"), { enabled }),
      onSuccess: done,
    }),
    keep: useMutation({
      // The files of one photo (crops, resizes): all of them go back, or none.
      mutationFn: (input: { ids: string[]; kept: boolean }) => api.post(apiPath("/logos/keep"), { site_image_ids: input.ids, kept: input.kept }),
      onSuccess: done,
    }),
  };
}
