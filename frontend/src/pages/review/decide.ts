import { useEffect, useRef } from "react";
import { useToast } from "../../components/feedback";
import { errorMessage } from "../../lib/api";
import { decisionLabel, plural } from "../../lib/format";
import type { Decision } from "../../types";
import { keep, type Row, type ReviewView } from "./view";

/**
 * Records a decision on a photo: for each reference that matches it, or, with `everywhere`, on every occurrence of
 * the shown reference. "Annuler" puts each one back.
 */
export function useDecide(view: ReviewView) {
  const toast = useToast();
  const { review, rows, selectedIndex, setSelectedKey, decisionFilter } = view;

  return function decide(row: Row, decision: Decision | "", everywhere = false) {
    const targets = everywhere
      ? [{ group: row.group, hits: row.group.hits }]
      : [{ group: row.group, hits: [row.hit] }, ...row.others.map((pair) => ({ group: pair.group, hits: [pair.hit] }))];
    const nextKey = rows[selectedIndex + 1]?.key ?? rows[selectedIndex - 1]?.key ?? null;
    const send = (target: (typeof targets)[number], value: Decision | "", ids: string[]) =>
      review.mutateAsync({ referenceId: target.group.reference_id, siteImageIds: ids, decision: value });
    const undo = () => {
      for (const target of targets) {
        // What each occurrence was before, so every one goes back to its own decision.
        const before = new Map<Decision | "", string[]>();
        for (const hit of target.hits) before.set(hit.decision ?? "", [...(before.get(hit.decision ?? "") ?? []), ...hit.site_image_ids]);
        for (const [previous, ids] of before) {
          void send(target, previous, ids).catch((error) => toast.show({ tone: "error", message: "L'annulation n'a pas abouti", description: errorMessage(error) }));
        }
      }
    };
    // `mutateAsync`, not `mutate(…, { onSuccess })`: per-call callbacks only run for the latest call, and decisions come in fast with the keyboard.
    void Promise.all(targets.map((target) => send(target, decision, target.hits.flatMap((hit) => hit.site_image_ids)))).then(
      () =>
        toast.show({
          tone: "success",
          message: decision ? `${decisionLabel[decision]}${suffix(everywhere, targets.length, row.group.hits.length)}` : "Décision retirée",
          description: row.group.filename,
          action: { label: "Annuler", onClick: undo },
          duration: 5000,
        }),
      (error) =>
        toast.show({ tone: "error", message: "La décision n'a pas été enregistrée", description: `${errorMessage(error)} Elle a été annulée à l'écran.` }),
    );
    // Move on when the decided row will leave the current filter.
    if (decision && !keep({ ...row.hit, decision }, decisionFilter)) setSelectedKey(nextKey);
  };
}

/** " · 3 références" for a photo several references match, " · 5 occurrences" for "all occurrences", else nothing. */
function suffix(everywhere: boolean, references: number, occurrences: number): string {
  if (everywhere) return occurrences > 1 ? ` · ${plural(occurrences, "occurrence")}` : "";
  return references > 1 ? ` · ${plural(references, "référence")}` : "";
}

/** J/K to move, C/F/U to decide on the selected row (confirm, false positive, undo). */
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
      else if (selected && key === "c") decideRow(selected, selected.hit.decision === "retenu" ? "" : "retenu");
      else if (selected && key === "f") decideRow(selected, selected.hit.decision === "ecarte" ? "" : "ecarte");
      else if (selected && key === "u") decideRow(selected, "");
      else return;
      event.preventDefault();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
}
