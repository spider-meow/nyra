import type { ReactNode } from "react";
import { plural } from "../lib/format";
import { Icon } from "./icons";

/**
 * The selection is a mode (library and "Images en ligne" share it): this bar sticks to the top of the page, says how many cards are
 * ticked, offers "Tout sélectionner" while some are not, and holds the actions of the page as `children`.
 */
export function SelectionFrame(props: { count: number; total: number; onSelectAll: () => void; onClear: () => void; children: ReactNode }) {
  return (
    <div className="sticky top-2 z-30 mb-6 flex min-h-16 flex-wrap items-center gap-2 rounded-2xl border border-ink bg-paper px-2 py-2 shadow-lift sm:px-3" role="region" aria-label="Sélection">
      <button type="button" className="grid h-11 w-11 shrink-0 place-items-center rounded-xl text-ink hover:bg-sunk" aria-label="Quitter la sélection" onClick={props.onClear}>
        <Icon name="close" size={20} />
      </button>
      <div className="min-w-0 flex-1 basis-40 leading-tight">
        <p className="truncate font-display text-xl sm:text-2xl" aria-live="polite">{plural(props.count, "sélectionné")}</p>
        {props.count < props.total ? (
          <button type="button" className="whitespace-nowrap text-[13px] text-ink-soft underline underline-offset-2" onClick={props.onSelectAll}>Tout sélectionner ({props.total.toLocaleString("fr-FR")})</button>
        ) : null}
      </div>
      <div className="flex w-full flex-wrap items-center gap-2 sm:w-auto sm:flex-nowrap">{props.children}</div>
    </div>
  );
}
