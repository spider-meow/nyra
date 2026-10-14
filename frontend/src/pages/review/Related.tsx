import { Thumb, cx } from "../../components/ui";
import { daysText, plural } from "../../lib/format";
import type { Pair } from "./view";

/** When one photo matches several references (crops of one visual), the references to compare it with, and what a decision covers. */
export function RelatedReferences(props: { pairs: Pair[]; activeId: string; onPick: (referenceId: string) => void }) {
  const { pairs } = props;
  if (pairs.length < 2) return null;
  return (
    <div className="rounded-xl border border-line-strong p-3">
      <p className="text-[13px] font-semibold">Cette photo correspond à {plural(pairs.length, "référence")}</p>
      <p className="mt-0.5 text-xs text-muted">Votre décision s'applique à toutes. Cliquez sur l'une pour la comparer.</p>
      <div className="mt-2.5 flex flex-wrap gap-1.5">
        {pairs.map((pair) => (
          <button
            key={pair.group.reference_id}
            type="button"
            title={pair.group.filename}
            aria-pressed={pair.group.reference_id === props.activeId}
            onClick={() => props.onPick(pair.group.reference_id)}
            className={cx("flex items-center gap-2 rounded-lg border py-1 pr-2.5 pl-1 text-left text-xs", pair.group.reference_id === props.activeId ? "border-ink bg-paper" : "border-line-strong hover:border-faint")}
          >
            <Thumb src={pair.group.ref_thumb} size={32} />
            <span className="max-w-32 truncate">{pair.group.filename}<span className="block text-muted">{daysText(pair.group.days_left)}</span></span>
          </button>
        ))}
      </div>
    </div>
  );
}
