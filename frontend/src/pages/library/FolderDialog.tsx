import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { Modal } from "../../components/feedback";
import { Icon } from "../../components/icons";
import { Button, Input, cx } from "../../components/ui";
import { num, plural } from "../../lib/format";
import type { CollectionIndex } from "./collections";
import { folderRows, highlight, searchFolders, type FolderRow } from "./folderSearch";

const INDENT_PX = 20;

/** `text` with what the search found in bold on a yellow marker. */
function Marked({ text, query }: { text: string; query: string }) {
  return <>{highlight(text, query).map((piece, at) => (piece.hit ? <mark key={at} className="rounded-sm bg-yellow px-0.5 text-ink">{piece.text}</mark> : piece.text))}</>;
}

function Choice(props: { chosen: boolean; stop: boolean; depth: number; icon: boolean; onPick: () => void; count?: number; children: React.ReactNode }) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={props.chosen}
      tabIndex={props.stop ? 0 : -1}
      onClick={props.onPick}
      style={{ paddingLeft: 12 + props.depth * INDENT_PX }}
      className={cx("flex w-full items-center gap-3 rounded-lg border py-2.5 pr-3 text-left text-sm", props.chosen ? "border-ink bg-yellow/40" : "border-transparent hover:bg-sunk")}
    >
      <span className={cx("grid size-5 shrink-0 place-items-center rounded-full", props.chosen ? "bg-ink text-white" : "text-faint")}>
        {props.chosen ? <Icon name="check" size={12} strokeWidth={3} /> : props.icon ? <Icon name="folders" size={16} /> : null}
      </span>
      <span className="min-w-0 flex-1 leading-snug">{props.children}</span>
      {props.count === undefined ? null : <span className="shrink-0 text-[12.5px] tabular-nums text-muted">{num(props.count)}</span>}
    </button>
  );
}

/** « Classer N visuels » : where to put the selection. The whole tree, indented, or the matches of what is typed with the path above each. */
export function FolderDialog(props: { count: number; folders: CollectionIndex; counts: Record<string, number>; busy: boolean; onFile: (id: string | null) => void; onClose: () => void }) {
  const [choice, setChoice] = useState<string | null | undefined>(undefined); // undefined: nothing chosen yet; null: out of any collection
  const [query, setQuery] = useState("");
  const list = useRef<HTMLUListElement>(null);
  const rows = useMemo(() => folderRows(props.folders), [props.folders]);
  const shown = useMemo(() => searchFolders(rows, query), [rows, query]);
  const searching = query.trim() !== "";
  const where = choice === undefined ? "" : choice === null ? "Aucune collection" : props.folders.path(choice);
  // One tab stop in the list (the chosen one, else the first); the arrows move inside it: 100 collections are not 100 tab stops.
  const visible = [...(searching ? [] : [null]), ...shown.map((item) => item.id)];
  const stop = visible.includes(choice as string | null) ? choice : visible[0];

  useEffect(() => {
    if (!searching) list.current?.querySelector("[aria-checked=true]")?.scrollIntoView({ block: "nearest" }); // back from a search: the choice made is in view
  }, [searching]);

  function move(event: KeyboardEvent<HTMLElement>) {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    const stops = [...event.currentTarget.querySelectorAll<HTMLElement>("input, [role=radio]")];
    const next = stops.indexOf(document.activeElement as HTMLElement) + (event.key === "ArrowDown" ? 1 : -1);
    event.preventDefault();
    stops[Math.min(Math.max(next, 0), stops.length - 1)]?.focus();
  }

  const row = (item: FolderRow) => (
    <li key={item.id}>
      <Choice chosen={choice === item.id} stop={stop === item.id} depth={searching ? 0 : item.depth} icon count={props.counts[item.id] ?? 0} onPick={() => setChoice(item.id)}>
        <span className={cx("block break-words", searching || item.depth > 0 ? "font-medium" : "font-semibold")}><Marked text={item.name} query={query} /></span>
        {searching && item.parent ? <span className="block break-words text-[12.5px] text-muted"><Marked text={item.parent} query={query} /></span> : null}
      </Choice>
    </li>
  );

  return (
    <Modal
      open
      wide
      onClose={props.onClose}
      title={`Classer ${plural(props.count, "visuel", "visuels")}`}
      footer={
        <>
          <p className="line-clamp-2 min-w-0 basis-full break-words text-[13px] text-muted sm:flex-1 sm:basis-0" aria-live="polite" title={where}>{where ? `Destination : ${where}` : "Choisissez une destination."}</p>
          <Button onClick={props.onClose}>Annuler</Button>
          <Button variant="primary" disabled={choice === undefined} loading={props.busy} onClick={() => choice !== undefined && props.onFile(choice)}>Classer ici</Button>
        </>
      }
    >
      <div className="grid grid-cols-[minmax(0,1fr)] gap-3" onKeyDown={move}>
        <div className="sticky top-0 z-10 -mx-1 bg-paper px-1 pb-1 pt-1">
          <Input type="search" data-autofocus aria-label="Rechercher une collection" placeholder="Rechercher une collection…" value={query} onChange={(event) => setQuery(event.target.value)} />
        </div>
        <ul ref={list} className="grid grid-cols-[minmax(0,1fr)] gap-0.5" role="radiogroup" aria-label="Collection">
          {searching ? null : (
            <li className="mb-1 border-b border-line pb-1">
              <Choice chosen={choice === null} stop={stop === null} depth={0} icon={false} onPick={() => setChoice(null)}><span className="font-medium">Aucune (non classé)</span></Choice>
            </li>
          )}
          {shown.map(row)}
        </ul>
        {searching && !shown.length ? <p className="px-1 pb-2 text-sm text-muted">Aucune collection ne correspond à « {query.trim()} ».</p> : null}
        {!rows.length ? <p className="px-1 pb-2 text-sm text-muted">Cette marque n'a pas encore de collection : créez-en une depuis la bibliothèque.</p> : null}
      </div>
    </Modal>
  );
}
