import { Suspense, lazy, useEffect, useRef, useState } from "react";
import { Icon } from "../../components/icons";
import { Spinner, cx } from "../../components/ui";
import { plural } from "../../lib/format";
import { useJobs, useOccurrences } from "../../lib/queries";
import type { Job, LibraryItem } from "../../types";

export const PAGE_SIZE = 50;

// Only loaded when the section is opened: most visits to the library never need it.
const OccurrencesPanel = lazy(() => import("./OccurrencesPanel"));

/** The search running for this picture, if any: `mine` is false when another picture's search holds the queue. */
export function useLocateJob(item: LibraryItem): { running: Job | undefined; mine: boolean } {
  const running = (useJobs().data?.active ?? []).find((job) => job.kind === "locate");
  return { running, mine: running?.params.reference_id === item.id };
}

/** "Où est-elle utilisée ?": a row that unfolds, in place, the pages showing this picture (crops included). */
export function OccurrencesSection({ item }: { item: LibraryItem }) {
  const [open, setOpen] = useState(false);
  const data = useOccurrences(item.filename, 0, PAGE_SIZE).data;
  const { mine } = useLocateJob(item);
  const section = useRef<HTMLDivElement>(null);
  // Opened near the bottom of the dialog, the content would unfold out of sight.
  useEffect(() => {
    if (open) section.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [open]);

  // Open, the figures are in the panel (and follow its filter): the row only says what it holds.
  const hint = open
    ? ""
    : mine
      ? "Recherche en cours…"
      : !data
        ? ""
        : data.images_found
          ? `${plural(data.total_pages, "page")} · ${plural(data.images_found, "image")}`
          : data.located_at
            ? "Aucune occurrence trouvée"
            : "Pas encore recherchée";

  return (
    <div ref={section} className="mt-5 overflow-hidden rounded-xl border border-line bg-sunk">
      <button type="button" aria-expanded={open} aria-controls="occurrences-panel" onClick={() => setOpen(!open)} className="group flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-paper">
        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-[10px] bg-peach-soft text-bark"><Icon name="scan" size={18} strokeWidth={1.8} /></span>
        <span className="min-w-0 flex-1">
          <span className="block text-sm font-medium">Où est-elle utilisée ?</span>
          <span className="block truncate text-[13px] text-muted">{hint || "Les pages des sites qui montrent ce visuel, recadrages compris"}</span>
        </span>
        <Icon name="chevrons" size={16} className={cx("shrink-0 text-faint transition-transform duration-200 group-hover:text-ink", open && "rotate-180")} />
      </button>
      {open ? (
        <div id="occurrences-panel" className="pop-in border-t border-line bg-paper">
          <Suspense fallback={<div className="px-4 py-4"><Spinner /></div>}>
            <OccurrencesPanel item={item} />
          </Suspense>
        </div>
      ) : null}
    </div>
  );
}
