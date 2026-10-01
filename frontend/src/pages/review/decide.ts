import { useEffect, useRef } from "react";
import { useToast } from "../../components/feedback";
import { errorMessage } from "../../lib/api";
import { decisionLabel, plural } from "../../lib/format";
import type { Decision } from "../../types";
import { keep, type Row, type ReviewView } from "./view";

/** Records a decision on one occurrence, or on every occurrence of the visual; "Annuler" puts each one back. */
export function useDecide(view: ReviewView) {
  const toast = useToast();
  const { review, rows, selectedIndex, setSelectedKey, decisionFilter } = view;

  return function decide(row: Row, decision: Decision | "", everywhere = false) {
    const hits = everywhere ? row.group.hits : [row.hit];
    const ids = hits.flatMap((hit) => hit.site_image_ids);
    const nextKey = rows[selectedIndex + 1]?.key ?? rows[selectedIndex - 1]?.key ?? null;
    // What each occurrence was before, so "Annuler" puts every one back.
    const before = new Map<Decision | "", string[]>();
    for (const hit of hits) before.set(hit.decision ?? "", [...(before.get(hit.decision ?? "") ?? []), ...hit.site_image_ids]);
    const undo = () => {
      for (const [previous, siteImageIds] of before) {
        review.mutate(
          { referenceId: row.group.reference_id, siteImageIds, decision: previous },
          { onError: (error) => toast.show({ tone: "error", message: "L'annulation n'a pas abouti", description: errorMessage(error) }) },
        );
      }
    };
    review.mutate(
      { referenceId: row.group.reference_id, siteImageIds: ids, decision },
      {
        onSuccess: () =>
          toast.show({
            tone: "success",
            message: decision
              ? `${decisionLabel[decision]}${hits.length > 1 ? ` · ${plural(hits.length, "occurrence")}` : ""}`
              : "Décision retirée",
            description: row.group.filename,
            action: { label: "Annuler", onClick: undo },
            duration: 5000,
          }),
        onError: (error) =>
          toast.show({ tone: "error", message: "La décision n'a pas été enregistrée", description: `${errorMessage(error)} Elle a été annulée à l'écran.` }),
      },
    );
    // Move on when the decided row will leave the current filter.
    if (decision && !keep({ ...row.hit, decision }, decisionFilter)) setSelectedKey(nextKey);
  };
}

/** J/K to move, R/F/T/U to decide on the selected row. */
export function useShortcuts(view: ReviewView, decide: ReturnType<typeof useDecide>) {
  // The listener is added once; it reads the latest move, selection and decide through this ref.
  const latest = useRef({ view, decide });
  useEffect(() => {
    latest.current = { view, decide };
  });
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const { target } = event;
      if ((target instanceof Element && target.closest("input, select, textarea, [role=dialog]")) || event.metaKey || event.ctrlKey || event.altKey) return;
      const { move, selected } = latest.current.view;
      const decideRow = latest.current.decide;
      const key = event.key.toLowerCase();
      if (key === "j" || key === "arrowdown") move(1);
      else if (key === "k" || key === "arrowup") move(-1);
      else if (selected && key === "r") decideRow(selected, selected.hit.decision === "retenu" ? "" : "retenu");
      else if (selected && key === "f") decideRow(selected, selected.hit.decision === "ecarte" ? "" : "ecarte");
      else if (selected && key === "t") decideRow(selected, selected.hit.decision === "traite" ? "" : "traite");
      else if (selected && key === "u") decideRow(selected, "");
      else return;
      event.preventDefault();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
}
