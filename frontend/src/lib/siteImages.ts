import { useInfiniteQuery, useMutation, useQuery } from "@tanstack/react-query";
import type { SiteImagesPage, SiteImageView } from "../types";
import { api } from "./api";
import { useOrg } from "./org";
import { useInvalidate } from "./queries";

// Only the pages "Images en ligne" and Réglages > Général > Logos use these: kept out of `queries.ts`, which every page loads.

export const SITE_IMAGES_PAGE = 60;

/** One tab of "Images en ligne", narrowed to a site version and a text, a page of entries at a time (`fetchNextPage`). */
export function useSiteImages(options: { view: SiteImageView; site: string; text: string }) {
  const { apiPath, brand } = useOrg();
  const { view, site, text } = options;
  return useInfiniteQuery({
    queryKey: ["site-images", brand.id, view, site, text],
    queryFn: ({ pageParam }) => {
      const params = new URLSearchParams({ view, offset: String(pageParam), limit: String(SITE_IMAGES_PAGE), q: text });
      if (site !== "all") params.set("site", site);
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

/** "C'est / ce n'est pas un packshot", for the Nyra team (AXE-668): kept with the detector's measures, never changes the detection. */
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
