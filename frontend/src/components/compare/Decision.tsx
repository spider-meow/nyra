import { DecisionBadge, cx } from "../ui";
import type { Decision, Hit } from "../../types";

/** The three decision buttons, and "all occurrences of this visual" when there are others. */
export function DecisionPanel(props: { hit: Hit; others: number; busy?: boolean; onDecide: (decision: Decision | "", everywhere?: boolean) => void }) {
  const { hit, others, busy, onDecide } = props;
  return (
    <div className="rounded-2xl bg-sunk p-3.5">
      <div className="flex items-center justify-between gap-2">
        <p className="text-[13px] font-semibold">Décision</p>
        <DecisionBadge decision={hit.decision} />
      </div>
      <div className="mt-3 grid grid-cols-3 gap-2">
        <DecisionButton label="À retirer" shortcut="R" active={hit.decision === "retenu"} disabled={busy} onClick={() => onDecide(hit.decision === "retenu" ? "" : "retenu")} />
        <DecisionButton label="Faux positif" shortcut="F" active={hit.decision === "ecarte"} disabled={busy} onClick={() => onDecide(hit.decision === "ecarte" ? "" : "ecarte")} />
        <DecisionButton label="Retiré" shortcut="T" active={hit.decision === "traite"} disabled={busy} onClick={() => onDecide(hit.decision === "traite" ? "" : "traite")} />
      </div>
      {others > 0 ? (
        <div className="mt-3 flex flex-wrap items-center gap-2 text-[13px] text-muted">
          <span>{others} autre{others > 1 ? "s" : ""} occurrence{others > 1 ? "s" : ""} de ce visuel :</span>
          <button type="button" className="underline underline-offset-2 hover:text-ink" disabled={busy} onClick={() => onDecide("retenu", true)}>tout à retirer</button>
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
