import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { Modal, useToast } from "../../components/feedback";
import { Icon, type IconName } from "../../components/icons";
import { ContentTagger } from "../../components/Labels";
import { Button, Input, cx } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import { formatDate, plural, splitTags } from "../../lib/format";
import { LABELS_ENABLED } from "../../lib/labels";
import { useLibraryMutations } from "../../lib/queries";
import type { LabelInfo, LibraryItem } from "../../types";
import type { CollectionIndex } from "./collections";
import { dateHint, summarize, whereHint } from "./selectionSummary";

type Dialog = "date" | "unlimited" | "tags" | "folder" | "labels";

/** The edits applied to the selected visuals. Each one says how many visuals changed, then ends the selection. */
function useBulkEdit(names: string[], done: () => void) {
  const { setExpiry, setTags, fileInCollection } = useLibraryMutations();
  const toast = useToast();
  const options = (message: string) => ({
    onSuccess: (result: { updated: number }) => {
      toast.show({ tone: "success", message, description: plural(result.updated, "visuel mis à jour", "visuels mis à jour") });
      done();
    },
    onError: (error: unknown) => toast.show({ tone: "error", message: "L'enregistrement n'a pas abouti", description: errorMessage(error) }),
  });
  return {
    setDate: (date: string) => setExpiry.mutate({ filenames: names, expiry_date: date, unlimited_rights: false }, options(`Échéance fixée au ${formatDate(date)}`)),
    clearDate: () => setExpiry.mutate({ filenames: names, expiry_date: "", unlimited_rights: false }, options("Échéance retirée")),
    unlimited: () => setExpiry.mutate({ filenames: names, expiry_date: "", unlimited_rights: true }, options("Droits illimités")),
    tags: (mode: "add" | "remove", tags: string[]) => setTags.mutate({ filenames: names, [mode]: tags }, options(mode === "add" ? "Tags ajoutés" : "Tags retirés")),
    file: (collectionId: string | null) => fileInCollection.mutate({ filenames: names, collection_id: collectionId }, options(collectionId ? "Visuels classés" : "Visuels sortis de leur collection")),
    busy: setExpiry.isPending || setTags.isPending || fileInCollection.isPending,
  };
}

function Row(props: { icon: IconName; label: string; hint?: string; danger?: boolean; disabled?: boolean; onClick: () => void }) {
  return (
    <li role="none">
      <button
        type="button"
        role="menuitem"
        disabled={props.disabled}
        onClick={props.onClick}
        className={cx("flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left outline-none focus-visible:bg-sunk disabled:opacity-50 sm:py-2", props.danger ? "text-expired hover:bg-expired-soft focus-visible:bg-expired-soft" : "hover:bg-sunk")}
      >
        <span className={props.danger ? "" : "text-ink-soft"}><Icon name={props.icon} size={19} /></span>
        <span className="min-w-0 flex-1 leading-tight">
          <span className="block text-sm font-medium">{props.label}</span>
          {props.hint ? <span className="block text-[12.5px] text-muted">{props.hint}</span> : null}
        </span>
      </button>
    </li>
  );
}

/** The menu: a bubble under the header button on a wide screen, a sheet from the bottom on a phone. Arrow keys move, Escape closes. */
function Menu(props: { items: LibraryItem[]; labelsOn: boolean; removing: boolean; onPick: (dialog: Dialog | "remove") => void; onClose: () => void }) {
  const list = useRef<HTMLUListElement>(null);
  const sum = summarize(props.items);
  useEffect(() => {
    list.current?.querySelector<HTMLElement>("[role=menuitem]")?.focus();
    const onKey = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") props.onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
    // Only on open: `onClose` is the page's own.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function onKeyDown(event: KeyboardEvent) {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    const rows = [...(list.current?.querySelectorAll<HTMLElement>("[role=menuitem]:not(:disabled)") ?? [])];
    const at = rows.findIndex((row) => row === document.activeElement);
    event.preventDefault();
    rows[(at + (event.key === "ArrowDown" ? 1 : -1) + rows.length) % rows.length]?.focus();
  }

  return (
    <>
      <div className="fixed inset-0 z-30 bg-black/25 sm:bg-transparent" onMouseDown={props.onClose} />
      <div className="fixed inset-x-0 bottom-0 z-40 rounded-t-2xl border border-line-strong bg-paper pb-6 pt-3 shadow-float sm:absolute sm:inset-x-auto sm:bottom-auto sm:right-0 sm:top-full sm:mt-2 sm:w-80 sm:rounded-xl sm:pb-0 sm:pt-0">
        <div className="mx-auto mb-2 h-1 w-10 rounded-full bg-line-strong sm:hidden" aria-hidden />
        <p className="px-4 pb-1 text-[13px] text-muted sm:hidden">{plural(sum.count, "visuel sélectionné", "visuels sélectionnés")}</p>
        <ul ref={list} className="grid gap-0.5 p-1.5" role="menu" aria-label="Actions sur la sélection" onKeyDown={onKeyDown}>
          <Row icon="calendar" label="Fixer l'échéance…" hint={dateHint(sum.count, sum.withDate)} onClick={() => props.onPick("date")} />
          <Row icon="infinity" label="Droits illimités…" hint="Jamais expirés, sans alerte" onClick={() => props.onPick("unlimited")} />
          <Row icon="tag" label="Ajouter ou retirer des tags…" hint={sum.tags ? plural(sum.tags, "tag déjà utilisé", "tags déjà utilisés") : "Aucun tag pour l'instant"} onClick={() => props.onPick("tags")} />
          <Row icon="folders" label="Classer dans une collection…" hint={whereHint(sum.unclassified, sum.collections)} onClick={() => props.onPick("folder")} />
          {props.labelsOn ? <Row icon="radar" label="Étiquettes de contenu…" onClick={() => props.onPick("labels")} /> : null}
          <li role="separator" className="my-1 border-t border-line" />
          <Row icon="trash" danger disabled={props.removing} label={props.removing ? "Suppression…" : `Supprimer ${plural(sum.count, "visuel", "visuels")}…`} onClick={() => props.onPick("remove")} />
        </ul>
      </div>
    </>
  );
}

function DateDialog(props: { items: LibraryItem[]; bulk: ReturnType<typeof useBulkEdit>; onClose: () => void }) {
  const [date, setDate] = useState("");
  const sum = summarize(props.items);
  return (
    <Modal
      open
      onClose={props.onClose}
      title={`Fixer l'échéance de ${plural(sum.count, "visuel", "visuels")}`}
      footer={
        <>
          <button type="button" disabled={props.bulk.busy} className="mr-auto text-[13px] text-expired underline underline-offset-2 disabled:opacity-50" onClick={props.bulk.clearDate}>Retirer l'échéance</button>
          <Button onClick={props.onClose}>Annuler</Button>
          <Button variant="primary" disabled={!date} loading={props.bulk.busy} onClick={() => props.bulk.setDate(date)}>Fixer l'échéance</Button>
        </>
      }
    >
      <div className="grid gap-3">
        <Input type="date" value={date} onChange={(event) => setDate(event.target.value)} aria-label="Nouvelle échéance" data-autofocus />
        {sum.withDate ? <p className="rounded-lg bg-peach-soft px-3 py-2 text-[13px] text-bark-800">{plural(sum.withDate, "visuel a déjà une échéance", "visuels ont déjà une échéance")} : elle sera remplacée.</p> : null}
        <p className="text-[13px] text-muted">« Retirer l'échéance » enlève la date sans rien fixer à la place.</p>
      </div>
    </Modal>
  );
}

function UnlimitedDialog(props: { items: LibraryItem[]; bulk: ReturnType<typeof useBulkEdit>; onClose: () => void }) {
  const sum = summarize(props.items);
  return (
    <Modal
      open
      onClose={props.onClose}
      title={`Droits illimités pour ${plural(sum.count, "visuel", "visuels")}`}
      footer={
        <>
          <Button onClick={props.onClose}>Annuler</Button>
          <Button variant="primary" loading={props.bulk.busy} onClick={props.bulk.unlimited}>Passer en droits illimités</Button>
        </>
      }
    >
      <div className="grid gap-3 text-sm">
        <p>Ces visuels ne seront jamais expirés : ils restent reconnus sur les sites, sans alerte.</p>
        {sum.withDate ? <p className="rounded-lg bg-peach-soft px-3 py-2 text-[13px] text-bark-800">{plural(sum.withDate, "visuel perd son échéance", "visuels perdent leur échéance")}.</p> : null}
      </div>
    </Modal>
  );
}

function TagsDialog(props: { items: LibraryItem[]; bulk: ReturnType<typeof useBulkEdit>; onClose: () => void }) {
  const [text, setText] = useState("");
  const tags = splitTags(text);
  const sum = summarize(props.items);
  return (
    <Modal
      open
      onClose={props.onClose}
      title={`Tags de ${plural(sum.count, "visuel", "visuels")}`}
      footer={
        <>
          <Button onClick={props.onClose}>Annuler</Button>
          <Button disabled={!tags.length} loading={props.bulk.busy} onClick={() => props.bulk.tags("remove", tags)}>Retirer ces tags</Button>
          <Button variant="primary" disabled={!tags.length} loading={props.bulk.busy} onClick={() => props.bulk.tags("add", tags)}>Ajouter ces tags</Button>
        </>
      }
    >
      <div className="grid gap-3">
        <Input list="library-tags" value={text} maxLength={120} placeholder="magnum, classic" aria-label="Tags" data-autofocus onChange={(event) => setText(event.target.value)} />
        <p className="text-[13px] text-muted">Séparez par des virgules. Un tag déjà présent n'est pas dupliqué ; retirer un tag absent ne change rien.</p>
      </div>
    </Modal>
  );
}

function FolderDialog(props: { items: LibraryItem[]; folders: CollectionIndex; bulk: ReturnType<typeof useBulkEdit>; onClose: () => void }) {
  const [choice, setChoice] = useState<string | null | undefined>(undefined); // undefined: nothing chosen yet; null: out of any collection
  const sum = summarize(props.items);
  const paths = [...props.folders.byId.keys()].map((id) => ({ id, path: props.folders.path(id) })).sort((a, b) => a.path.localeCompare(b.path, "fr", { numeric: true }));
  const options: { id: string | null; label: string }[] = [{ id: null, label: "Aucune (non classé)" }, ...paths.map((folder) => ({ id: folder.id, label: folder.path }))];
  return (
    <Modal
      open
      onClose={props.onClose}
      title={`Classer ${plural(sum.count, "visuel", "visuels")}`}
      footer={
        <>
          <Button onClick={props.onClose}>Annuler</Button>
          <Button variant="primary" disabled={choice === undefined} loading={props.bulk.busy} onClick={() => choice !== undefined && props.bulk.file(choice)}>Classer ici</Button>
        </>
      }
    >
      <ul className="grid gap-1" role="radiogroup" aria-label="Collection">
        {options.map((option) => (
          <li key={option.id ?? "none"}>
            <button
              type="button"
              role="radio"
              aria-checked={choice === option.id}
              onClick={() => setChoice(option.id)}
              className={cx("flex w-full items-center gap-2.5 rounded-lg border px-3 py-2.5 text-left text-sm", choice === option.id ? "border-ink bg-yellow/40 font-medium" : "border-line hover:bg-sunk")}
            >
              <span className={cx("grid h-4 w-4 shrink-0 place-items-center rounded-full border", choice === option.id ? "border-ink bg-ink text-white" : "border-line-strong")}>
                {choice === option.id ? <Icon name="check" size={10} strokeWidth={3} /> : null}
              </span>
              <span className="min-w-0 break-words">{option.label}</span>
            </button>
          </li>
        ))}
      </ul>
    </Modal>
  );
}

type Props = {
  open: boolean;
  /** The menu is closed (a dialog may stay open): the header gives the focus back to its button. */
  onClose: () => void;
  items: LibraryItem[];
  folders: CollectionIndex;
  labels: LabelInfo[];
  /** The selection is over: after an edit succeeded, or a delete. */
  onDone: () => void;
  removing: boolean;
  onRemove: (names: string[]) => Promise<void>;
};

/** The Actions menu of the selection header and the dialogs it opens. Loaded when the menu is first opened. */
export function SelectionActions({ open, onClose, items, folders, labels, onDone, removing, onRemove }: Props) {
  const [dialog, setDialog] = useState<Dialog | null>(null);
  const names = items.map((item) => item.filename);
  const bulk = useBulkEdit(names, () => {
    setDialog(null);
    onDone();
  });
  const close = () => setDialog(null);

  function pick(choice: Dialog | "remove") {
    onClose();
    if (choice === "remove") void onRemove(names);
    else setDialog(choice);
  }

  return (
    <>
      {open ? <Menu items={items} labelsOn={LABELS_ENABLED} removing={removing} onPick={pick} onClose={onClose} /> : null}
      {dialog === "date" ? <DateDialog items={items} bulk={bulk} onClose={close} /> : null}
      {dialog === "unlimited" ? <UnlimitedDialog items={items} bulk={bulk} onClose={close} /> : null}
      {dialog === "tags" ? <TagsDialog items={items} bulk={bulk} onClose={close} /> : null}
      {dialog === "folder" ? <FolderDialog items={items} folders={folders} bulk={bulk} onClose={close} /> : null}
      {dialog === "labels" ? (
        <Modal open onClose={close} title="Étiquettes de contenu" footer={<Button onClick={close}>Fermer</Button>}>
          <ContentTagger labels={labels} images={{ referenceIds: items.map((item) => item.id) }} />
        </Modal>
      ) : null}
    </>
  );
}
