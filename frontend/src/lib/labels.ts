import { useMutation, useQuery } from "@tanstack/react-query";
import type { ImageLabel, LabelInfo, LabelSampleItem, LabelsAnswer } from "../types";
import { api } from "./api";
import { useOrg } from "./org";
import { useInvalidate } from "./queries";

/** Types and contents are switched off until they are calibrated on real images: build with VITE_LABELS=1 to see them. */
export const LABELS_ENABLED = import.meta.env.VITE_LABELS === "1";

/** The brand's labels (the four types, then the contents) and how well the type examples predict themselves. */
export function useLabels() {
  const { apiPath, brand } = useOrg();
  return useQuery({ queryKey: ["labels", brand.id], queryFn: () => api.get<LabelsAnswer>(apiPath("/labels")), staleTime: 30_000, enabled: LABELS_ENABLED });
}

/** The labels of every image of the sites or of the library: decided by a person, or proposed. */
export function useAssignments(target: "site" | "reference") {
  const { apiPath, brand } = useOrg();
  return useQuery({
    queryKey: ["label-assignments", brand.id, target],
    queryFn: () => api.get<{ assignments: Record<string, ImageLabel[]> }>(apiPath(`/labels/assignments?target=${target}`)),
    staleTime: 60_000,
    enabled: LABELS_ENABLED,
  });
}

/** Images to type by hand, unlike each other; fetched again when `round` changes. */
export function useLabelSample(open: boolean, round: number) {
  const { apiPath, brand } = useOrg();
  return useQuery({
    queryKey: ["label-sample", brand.id, round],
    queryFn: () => api.get<{ items: LabelSampleItem[]; remaining: number }>(apiPath("/labels/sample?count=40")),
    enabled: open && LABELS_ENABLED,
    staleTime: Infinity,
    gcTime: 0,
  });
}

export type Decision = "yes" | "no" | "clear";
type Images = { siteImageIds?: string[]; referenceIds?: string[] };

/** `quiet`: do not refresh the labels after each decision (the hand-labeling mode refreshes once, when it ends a round). */
export function useLabelMutations(quiet = false) {
  const { apiPath } = useOrg();
  const invalidate = useInvalidate();
  const refresh = () => (quiet ? undefined : invalidate("labels", "label-assignments"));
  const decide = (labelId: string, decision: Decision, images: Images) =>
    api.post<{ written: number }>(apiPath(`/labels/${labelId}/images`), {
      decision,
      site_image_ids: images.siteImageIds ?? [],
      reference_ids: images.referenceIds ?? [],
    });
  return {
    decide: useMutation({
      mutationFn: (input: { labelId: string; decision: Decision } & Images) => decide(input.labelId, input.decision, input),
      onSuccess: refresh,
    }),
    /** A content label by its name (created when new), then the decision on the images. */
    tagContent: useMutation({
      mutationFn: async (input: { name: string; decision: Decision } & Images) => {
        const label = await api.post<LabelInfo>(apiPath("/labels"), { name: input.name });
        return decide(label.id, input.decision, input);
      },
      onSuccess: refresh,
    }),
  };
}
