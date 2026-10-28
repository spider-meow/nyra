import { useEffect, useRef, type PointerEvent } from "react";
import { Icon } from "../../components/icons";
import { LabelChips } from "../../components/Labels";
import { Button, StatusBadge, cx } from "../../components/ui";
import { formatDate, rightsText } from "../../lib/format";
import { useOrg } from "../../lib/org";
import type { ImageLabel, LibraryItem } from "../../types";
import type { LibraryView } from "./view";

type Props = {
  view: LibraryView;
  thumbs: Record<string, string>;
  labelsOf: (id: string) => ImageLabel[];
  selected: Set<string>;
  setSelected: (selected: Set<string>) => void;
  onEdit: (item: LibraryItem) => void;
};

/** "Select all" box, the visuels (first page of them), "show more" and the empty-result message. */
export function LibraryGrid({ view, thumbs, labelsOf, selected, setSelected, onEdit }: Props) {
  const { admin } = useOrg();
  const { visible, shown, tab, filtering } = view;
  // Which collection a card is in matters when the cards come from several: the whole library, or a search.
  const showWhere = view.scope === "all" || Boolean(view.query.trim());
  const allVisibleSelected = visible.length > 0 && visible.every((item) => selected.has(item.filename));

  function toggle(filename: string, on: boolean) {
    const next = new Set(selected);
    if (on) next.add(filename);
    else next.delete(filename);
    setSelected(next);
  }

  return (
    <>
      {admin && visible.length ? (
        <label className="mb-3 inline-flex items-center gap-2 text-[13px] text-muted">
          <input
            type="checkbox"
            className="h-4 w-4 accent-ink"
            checked={allVisibleSelected}
            onChange={(event) => setSelected(event.target.checked ? new Set(visible.map((item) => item.filename)) : new Set())}
          />
          Tout sélectionner
        </label>
      ) : null}
      <ul className="grid grid-cols-2 gap-4 sm:grid-cols-3 xl:grid-cols-4">
        {visible.slice(0, shown).map((item) => (
          <ReferenceCard key={item.id} item={item} where={showWhere && item.collection_id ? view.folders.path(item.collection_id) : ""} labels={labelsOf(item.id)} thumbUrl={thumbs[item.filename]} checked={selected.has(item.filename)} anySelected={selected.size > 0} onEdit={onEdit} onToggle={toggle} />
        ))}
      </ul>
      {visible.length > shown ? (
        <div className="mt-6 text-center">
          <Button onClick={view.showMore}>Afficher plus · {shown}/{visible.length}</Button>
        </div>
      ) : null}
      {!visible.length ? (
        <p className="py-10 text-center text-sm text-muted">
          {tab === "expired" && !filtering ? "Aucun visuel expiré : tout ce que vous surveillez est encore sous droits." : "Aucun visuel ne correspond à ces filtres."}
        </p>
      ) : null}
    </>
  );
}

/** The landing: the newest visuals on a single row (the cards that do not fit the columns of the screen are hidden), then the way to the whole library. */
export function RecentCards(props: { items: LibraryItem[]; total: number; thumbs: Record<string, string>; labelsOf: (id: string) => ImageLabel[]; onEdit: (item: LibraryItem) => void; onAll: () => void }) {
  return (
    <section aria-label="Derniers ajouts">
      {props.items.length ? <h2 className="mb-3 text-sm font-semibold">Derniers ajouts</h2> : null}
      <ul className="grid grid-cols-2 gap-4 sm:grid-cols-3 xl:grid-cols-4 max-xl:[&>li:nth-child(n+4)]:hidden max-sm:[&>li:nth-child(n+3)]:hidden">
        {props.items.map((item) => (
          <ReferenceCard key={item.id} item={item} where="" labels={props.labelsOf(item.id)} thumbUrl={props.thumbs[item.filename]} onEdit={props.onEdit} />
        ))}
      </ul>
      <div className="mt-6 text-center">
        <Button onClick={props.onAll}>Afficher tous les visuels ({props.total.toLocaleString("fr-FR")})</Button>
      </div>
    </section>
  );
}

const LONG_PRESS_MS = 450;
const MOVE_LIMIT_PX = 10;

/**
 * A finger held on a card: there is no hover on a phone, so this is how a selection starts. A move past a few pixels (a scroll) or
 * a lift cancels it. `wasLongPress` is true once after it fired: the click that follows the lift must not also open the card.
 */
function useLongPress(onLongPress: () => void) {
  const timer = useRef<number | undefined>(undefined);
  const origin = useRef<{ x: number; y: number } | null>(null);
  const fired = useRef(false);
  const cancel = () => {
    window.clearTimeout(timer.current);
    origin.current = null;
  };
  useEffect(() => cancel, []);
  return {
    handlers: {
      onPointerDown: (event: PointerEvent) => {
        if (event.pointerType !== "touch") return;
        fired.current = false;
        origin.current = { x: event.clientX, y: event.clientY };
        timer.current = window.setTimeout(() => {
          fired.current = true;
          onLongPress();
        }, LONG_PRESS_MS);
      },
      onPointerMove: (event: PointerEvent) => {
        if (origin.current && Math.hypot(event.clientX - origin.current.x, event.clientY - origin.current.y) > MOVE_LIMIT_PX) cancel();
      },
      onPointerUp: cancel,
      onPointerCancel: cancel,
      onContextMenu: (event: { preventDefault: () => void }) => {
        if (fired.current || origin.current) event.preventDefault(); // the browser's own menu on a long press
      },
    },
    wasLongPress: () => {
      const was = fired.current;
      fired.current = false;
      return was;
    },
  };
}

type CardProps = {
  item: LibraryItem;
  where: string;
  labels: ImageLabel[];
  /** Undefined until the signed URL arrives, and for good when the reference has no image to show. */
  thumbUrl: string | undefined;
  checked?: boolean;
  anySelected?: boolean;
  onEdit: (item: LibraryItem) => void;
  /** Absent: the card cannot be ticked. */
  onToggle?: (filename: string, checked: boolean) => void;
};

function ReferenceCard({ item, where, labels, thumbUrl, checked = false, anySelected = false, onEdit, onToggle }: CardProps) {
  const { admin } = useOrg();
  const press = useLongPress(() => onToggle?.(item.filename, !checked));
  // In selection mode a card is ticked or unticked by a touch or a click; outside it, it opens. A long press starts the selection.
  const tick = admin && onToggle !== undefined;
  const open = () => {
    if (tick && press.wasLongPress()) return;
    if (tick && anySelected) onToggle(item.filename, !checked);
    else onEdit(item);
  };
  return (
    <li
      className={cx(
        "group relative flex flex-col overflow-hidden rounded-xl border bg-paper transition-shadow hover:shadow-float",
        checked ? "border-focus shadow-[0_0_0_3px_var(--color-peach-soft)]" : "border-ink",
      )}
    >
      <button
        type="button"
        onClick={open}
        className="block text-left pointer-coarse:select-none pointer-coarse:[-webkit-touch-callout:none]"
        aria-label={`${admin ? (anySelected ? "Cocher" : "Modifier") : "Voir"} ${item.filename}`}
        {...(tick ? press.handlers : {})}
      >
        <span className="block aspect-[4/3] overflow-hidden bg-side">
          {thumbUrl ? (
            <img src={thumbUrl} alt="" loading="lazy" className="h-full w-full object-cover" />
          ) : (
            <span className="flex h-full w-full items-center justify-center text-faint"><Icon name="library" size={32} strokeWidth={1.4} /></span>
          )}
        </span>
        <span className="flex flex-col gap-2 px-3.5 pt-3 pb-3.5">
          <span className="truncate text-[13.5px] font-medium" title={item.filename}>{item.filename}</span>
          {where ? <span className="truncate text-[11.5px] text-muted" title={where}>{where}</span> : null}
          <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <StatusBadge status={item.status} label={item.status === "inconnue" ? undefined : rightsText(item.status, item.days_left)} />
            {item.expiry_date ? <span className="text-[11.5px] text-muted tabular">{formatDate(item.expiry_date)}</span> : null}
          </span>
          {item.tags.length ? (
            <span className="flex flex-wrap gap-1">
              {item.tags.slice(0, 3).map((tag) => (
                <span key={tag} className="rounded-full bg-side px-2 py-0.5 text-[11px] text-ink-soft">{tag}</span>
              ))}
              {item.tags.length > 3 ? <span className="px-1 py-0.5 text-[11px] text-muted">+{item.tags.length - 3}</span> : null}
            </span>
          ) : null}
          <LabelChips labels={labels} />
          {!item.indexed ? <span className="text-[11.5px] text-urgent">Indexation en attente</span> : null}
        </span>
      </button>
      {admin && onToggle ? (
        <input
          type="checkbox"
          className={cx("absolute top-2.5 left-2.5 h-5 w-5 accent-ink transition-opacity", checked || anySelected ? "opacity-100" : "opacity-0 group-hover:opacity-100 focus-visible:opacity-100")}
          aria-label={`Sélectionner ${item.filename}`}
          checked={checked}
          onChange={(event) => onToggle(item.filename, event.target.checked)}
        />
      ) : null}
    </li>
  );
}
