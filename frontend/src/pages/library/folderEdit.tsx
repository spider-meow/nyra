import { useState } from "react";
import { useConfirm, useToast } from "../../components/feedback";
import { errorMessage } from "../../lib/api";
import { useLibraryMutations } from "../../lib/queries";
import type { Collection } from "../../types";
import { moveTargets, type CollectionIndex } from "./collections";

/** What the field in a list of collections is doing: adding under a parent (null = top level), renaming, or choosing where to move one. */
export type Editing = { add: string | null } | { rename: string } | { move: string } | null;

/**
 * Add, rename, move and delete collections, with the error toast and the confirmation. Owns what is being edited.
 * Nothing navigates afterwards: a person who clicked elsewhere while the answer was on its way is not sent back, and a deleted folder
 * needs no call, the library falls back by itself.
 */
export function useFolderActions(folders: CollectionIndex) {
  const { addCollection, renameCollection, moveCollection, reorderCollections, removeCollection } = useLibraryMutations();
  const toast = useToast();
  const confirm = useConfirm();
  const [editing, setEditing] = useState<Editing>(null);
  const fail = (error: unknown) => toast.show({ tone: "error", message: "L'enregistrement n'a pas abouti", description: errorMessage(error) });

  function submit(name: string) {
    const trimmed = name.trim();
    const mode = editing;
    setEditing(null);
    if (!trimmed || !mode) return;
    if ("rename" in mode) renameCollection.mutate({ id: mode.rename, name: trimmed }, { onError: fail });
    else if ("add" in mode) addCollection.mutate({ name: trimmed, parent_id: mode.add }, { onError: fail });
  }

  /** `parentId` undefined = the choice was abandoned; null = the top level. */
  function move(id: string, parentId: string | null | undefined) {
    setEditing(null);
    if (parentId !== undefined) moveCollection.mutate({ id, parent_id: parentId }, { onError: fail });
  }

  /** `ids`: every child of `parentId` (null = the top level) in the order wanted. */
  function reorder(parentId: string | null, ids: string[]) {
    reorderCollections.mutate({ parent_id: parentId, ids }, { onError: fail });
  }

  async function remove(collection: Collection) {
    const inside = folders.subtree(collection.id).length > 1;
    const ok = await confirm({
      title: `Supprimer « ${collection.name} » ?`,
      body: <p>{inside ? "Ses sous-collections sont supprimées aussi. " : ""}Les visuels ne sont pas supprimés : ils repassent dans « Non classés ».</p>,
      confirm: "Supprimer la collection",
      danger: true,
    });
    if (ok) removeCollection.mutate(collection.id, { onError: fail });
  }
  return { editing, setEditing, submit, move, reorder, remove };
}

const TOP = "top"; // the value of the "Premier niveau" option: a select value cannot be null

/** Where to move a collection: only the places that keep the tree within three levels. Choosing moves it, leaving the list cancels. */
export function MoveField({ folders, collection, onDone }: { folders: CollectionIndex; collection: Collection; onDone: (parentId: string | null | undefined) => void }) {
  return (
    <select
      autoFocus
      defaultValue=""
      aria-label={`Déplacer ${collection.name}`}
      onChange={(event) => onDone(event.target.value === TOP ? null : event.target.value)}
      onBlur={() => onDone(undefined)}
      onKeyDown={(event) => { if (event.key === "Escape") onDone(undefined); }}
      className="h-8 w-full max-w-sm rounded-md border border-line-strong bg-paper px-2 text-[13.5px]"
    >
      <option value="" disabled>Déplacer vers…</option>
      {moveTargets(folders, collection).map((target) => <option key={target.id ?? TOP} value={target.id ?? TOP}>{target.label}</option>)}
    </select>
  );
}

/** The name of a collection being added or renamed: Enter or leaving the field saves, Escape cancels. */
export function NameField({ initial, label, onDone }: { initial: string; label: string; onDone: (name: string) => void }) {
  return (
    <input
      autoFocus
      defaultValue={initial}
      maxLength={80}
      placeholder="Winter 26-27"
      aria-label={label}
      onBlur={(event) => onDone(event.target.value)}
      onKeyDown={(event) => {
        if (event.key === "Enter") event.currentTarget.blur();
        if (event.key === "Escape") { event.currentTarget.value = ""; event.currentTarget.blur(); }
      }}
      className="h-8 w-full max-w-sm rounded-md border border-line-strong bg-paper px-2.5 text-[13.5px]"
    />
  );
}
