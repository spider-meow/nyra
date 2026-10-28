import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import { landing, moved, slotAt } from "../../lib/order";

type Drag = { parent: string | null; ids: string[]; id: string; slot: number };

/** What a row's handle (a `button`) needs. */
export type HandleProps = {
  "data-handle": string;
  "aria-label": string;
  onPointerDown: (event: PointerEvent<HTMLElement>) => void;
  onPointerMove: (event: PointerEvent<HTMLElement>) => void;
  onPointerUp: () => void;
  onPointerCancel: () => void;
  onKeyDown: (event: KeyboardEvent) => void;
};

// Whole class names: Tailwind only generates what it finds written out in the source.
const LINE_ABOVE = "before:pointer-events-none before:absolute before:inset-x-0 before:-top-px before:h-0.5 before:bg-ink";
const LINE_BELOW = "after:pointer-events-none after:absolute after:inset-x-0 after:-bottom-px after:h-0.5 after:bg-ink";

/** The boxes of the rows of the level a handle is in. */
const rowsAround = (handle: HTMLElement) =>
  Array.from(handle.closest("ul")?.querySelectorAll(":scope > li > [data-row]") ?? [], (row) => row.getBoundingClientRect());

/** The classes of a row's `li` (it needs `relative`): dimmed while it is the one dragged, and the line where the drag would drop. */
function rowClass(drag: Drag | null, parent: string | null, id: string, index: number, count: number): string {
  if (!drag || drag.parent !== parent) return "";
  const from = drag.ids.indexOf(drag.id);
  const lands = landing(drag.slot, from) !== from;
  return [drag.id === id && "opacity-50", lands && drag.slot === index && LINE_ABOVE, lands && drag.slot === count && index === count - 1 && LINE_BELOW]
    .filter(Boolean).join(" ");
}

/**
 * Ordering the rows of a list by hand, with a handle on each row: dragged (mouse, finger, pen: pointer events, so no long press
 * is needed on a phone) or moved with the arrow keys. One instance serves every level of a tree: a handle knows its level (`parent`,
 * `ids` = the children of that parent in the order shown). Rows are not moved while dragging: a line shows where the row would land,
 * and the order is asked for (`onOrder`) when it is dropped. Limit: the page does not scroll by itself while dragging.
 */
export function useSortable(onOrder: (parent: string | null, ids: string[]) => void) {
  // The drag is also kept in a ref: a pointer move and the release that follows it can arrive before the page was drawn again, and the release must see the move.
  const live = useRef<Drag | null>(null);
  const [drag, show] = useState<Drag | null>(null);
  const setDrag = (next: Drag | null) => {
    live.current = next;
    show(next);
  };
  const [said, setSaid] = useState("");
  const [refocus, setRefocus] = useState<string | null>(null);

  // A row moved in the page loses the focus in some browsers: the handle that was used gets it back.
  useEffect(() => {
    if (!refocus) return;
    document.querySelector<HTMLElement>(`[data-handle="${CSS.escape(refocus)}"]`)?.focus();
    setRefocus(null);
  }, [refocus]);

  function drop(current: Drag) {
    const from = current.ids.indexOf(current.id);
    const to = landing(current.slot, from);
    setDrag(null);
    if (to !== from) onOrder(current.parent, moved(current.ids, from, to));
  }

  function key(event: KeyboardEvent, parent: string | null, ids: string[], id: string, name: string) {
    if (event.key === "Escape") return setDrag(null);
    const step = event.key === "ArrowUp" ? -1 : event.key === "ArrowDown" ? 1 : 0;
    const from = ids.indexOf(id);
    if (!step || from + step < 0 || from + step >= ids.length) return;
    event.preventDefault();
    onOrder(parent, moved(ids, from, from + step));
    setSaid(`${name} : position ${from + step + 1} sur ${ids.length}`);
    setRefocus(id);
  }

  /** The props of a row's handle (a `button`). */
  function handle(parent: string | null, ids: string[], id: string, name: string): HandleProps {
    return {
      "data-handle": id,
      "aria-label": `Changer l'ordre de ${name} : glisser, ou flèches haut et bas`,
      onPointerDown: (event: PointerEvent<HTMLElement>) => {
        if (event.button !== 0) return;
        event.currentTarget.setPointerCapture(event.pointerId);
        setDrag({ parent, ids, id, slot: ids.indexOf(id) });
      },
      onPointerMove: (event: PointerEvent<HTMLElement>) => {
        const current = live.current;
        if (current?.id === id) setDrag({ ...current, slot: slotAt(rowsAround(event.currentTarget), event.clientY) });
      },
      onPointerUp: () => {
        const current = live.current;
        if (current?.id === id) drop(current);
      },
      onPointerCancel: () => setDrag(null),
      onKeyDown: (event: KeyboardEvent) => key(event, parent, ids, id, name),
    };
  }

  return { handle, rowClass: (parent: string | null, id: string, index: number, count: number) => rowClass(drag, parent, id, index, count), said };
}
