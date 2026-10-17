import { DecisionBadge, cx } from "../ui";
import type { Decision, Hit } from "../../types";

/** The two decisions: it is the visual (confirmed, and then it is to be replaced) or it is not (false positive). For the photo's other references too, when it has some; and "all occurrences of this visual" when there are others. */
export function DecisionPanel(props: { hit: Hit; others: number; related?: number; busy?: boolean; onDecide: (decision: Decision | "", everywhere?: boolean) => void }) {
  const { hit, others, related = 0, busy, onDecide } = props;
  // A photo already marked replaced stays confirmed: that step is taken on the page of the visual.
  const confirmed = hit.decision === "retenu" || hit.decision === "traite";
  return (
    <div className="rounded-xl bg-sunk p-3.5">
      <div className="flex items-center justify-between gap-2">
        <p className="text-[13px] font-semibold">Est-ce bien ce visuel ?</p>
        <DecisionBadge decision={hit.decision} />
      </div>
      <div className="mt-3 grid grid-cols-2 gap-2">
        <DecisionButton label="C'est bien lui" shortcut="C" active={confirmed} disabled={busy} onClick={() => onDecide(confirmed ? "" : "retenu")} />
        <DecisionButton label="Faux positif" shortcut="F" active={hit.decision === "ecarte"} disabled={busy} onClick={() => onDecide(hit.decision === "ecarte" ? "" : "ecarte")} />
      </div>
      {related > 0 ? <p className="mt-3 text-[13px] text-muted">S'applique aussi aux {related} autre{related > 1 ? "s" : ""} référence{related > 1 ? "s" : ""} de cette photo.</p> : null}
      {others > 0 ? (
        <div className="mt-3 flex flex-wrap items-center gap-2 text-[13px] text-muted">
          <span>{others} autre{others > 1 ? "s" : ""} occurrence{others > 1 ? "s" : ""} de ce visuel :</span>
          <button type="button" className="underline underline-offset-2 hover:text-ink" disabled={busy} onClick={() => onDecide("retenu", true)}>tout confirmer</button>
          <button type="button" className="underline underline-offset-2 hover:text-ink" disabled={busy} onClick={() => onDecide("ecarte", true)}>tout en faux positif</button>
        </div>
      ) : null}
    </div>
  );
}

function DecisionButton(props: { label: string; shortcut: string; active: boolean; onClick: () => void; disabled?: boolean }) {
  return (
    <button
      type="button"
      aria-pressed={props.active}
      disabled={props.disabled}
      onClick={props.onClick}
      className={cx(
        "flex h-14 flex-col items-center justify-center gap-0.5 rounded-[14px] border text-[13.5px] font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-40",
        props.active ? "border-ink bg-ink text-paper" : "border-line-strong bg-paper text-ink hover:border-faint",
      )}
    >
      {props.label}
      <span className={cx("text-[11px] font-normal", props.active ? "text-paper/60" : "text-muted")} aria-hidden>
        {props.shortcut}
      </span>
    </button>
  );
}
