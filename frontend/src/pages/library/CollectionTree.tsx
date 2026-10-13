import { useState } from "react";
import { useConfirm, useToast } from "../../components/feedback";
import { Icon } from "../../components/icons";
import { cx } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import { useOrg } from "../../lib/org";
import { useLibraryMutations } from "../../lib/queries";
import type { Collection } from "../../types";
import type { LibraryView } from "./view";

const MAX_DEPTH = 3; // same as the API

/** What the name field is doing: adding under a parent (null = top level), or renaming. */
type Editing = { add: string | null } | { rename: string } | null;

/** Folders of the library, brandcenter style: "Tous", "Non classés", then the tree. Admins add, rename and delete. */
export function CollectionTree({ view }: { view: LibraryView }) {
  const { admin } = useOrg();
  const { addCollection, renameCollection, removeCollection } = useLibraryMutations();
  const toast = useToast();
  const confirm = useConfirm();
  const [editing, setEditing] = useState<Editing>(null);
  const [open, setOpen] = useState<Set<string>>(new Set());
  const { folders, folderCounts, scope } = view;
  const fail = (error: unknown) => toast.show({ tone: "error", message: "L'enregistrement n'a pas abouti", description: errorMessage(error) });

  function submit(name: string) {
    const trimmed = name.trim();
    const mode = editing;
    setEditing(null);
    if (!trimmed || !mode) return;
    if ("rename" in mode) renameCollection.mutate({ id: mode.rename, name: trimmed }, { onError: fail });
    else {
      addCollection.mutate({ name: trimmed, parent_id: mode.add }, {
        onSuccess: ({ collection }) => { setOpen((current) => new Set(current).add(collection.parent_id ?? "")); view.setScope(collection.id); },
        onError: fail,
      });
      if (mode.add) setOpen((current) => new Set(current).add(mode.add as string));
    }
  }

  async function remove(collection: Collection) {
    const inside = folders.subtree(collection.id).length > 1;
    const ok = await confirm({
      title: `Supprimer « ${collection.name} » ?`,
      body: <p>{inside ? "Ses sous-collections sont supprimées aussi. " : ""}Les visuels ne sont pas supprimés : ils repassent dans « Non classés ».</p>,
      confirm: "Supprimer la collection",
      danger: true,
    });
    if (!ok) return;
    removeCollection.mutate(collection.id, { onSuccess: () => { if (folders.subtree(collection.id).includes(scope)) view.setScope("all"); }, onError: fail });
  }

  // A function, not a component: the name field must keep its focus while the tree redraws.
  function node(collection: Collection, depth: number) {
    const kids = folders.children.get(collection.id) ?? [];
    const expanded = open.has(collection.id) || (scope !== collection.id && folders.subtree(collection.id).includes(scope));
    return (
      <li key={collection.id}>
        {editing && "rename" in editing && editing.rename === collection.id ? (
          <NameField initial={collection.name} onDone={submit} depth={depth} />
        ) : (
          <Row
            depth={depth}
            active={scope === collection.id}
            label={collection.name}
            count={folderCounts[collection.id] ?? 0}
            onSelect={() => view.setScope(collection.id)}
            chevron={kids.length ? { expanded, toggle: () => setOpen((current) => { const next = new Set(current); if (!next.delete(collection.id)) next.add(collection.id); return next; }) } : undefined}
            actions={admin ? [
              ...(depth < MAX_DEPTH ? [{ label: `Ajouter une sous-collection à ${collection.name}`, icon: "plus" as const, run: () => setEditing({ add: collection.id }) }] : []),
              { label: `Renommer ${collection.name}`, text: "Renommer", run: () => setEditing({ rename: collection.id }) },
              { label: `Supprimer ${collection.name}`, icon: "close" as const, run: () => void remove(collection) },
            ] : []}
          />
        )}
        {expanded || (editing && "add" in editing && editing.add === collection.id) ? (
          <ul>
            {kids.map((child) => node(child, depth + 1))}
            {editing && "add" in editing && editing.add === collection.id ? <li><NameField initial="" onDone={submit} depth={depth + 1} /></li> : null}
          </ul>
        ) : null}
      </li>
    );
  }

  const top = folders.children.get(null) ?? [];
  return (
    <nav aria-label="Collections" className="mb-5 lg:mb-0">
      <ul className="flex flex-col gap-0.5">
        <li><Row depth={1} active={scope === "all"} label="Tous les visuels" count={folderCounts.all} onSelect={() => view.setScope("all")} actions={[]} /></li>
        {folderCounts.none || scope === "none" ? <li><Row depth={1} active={scope === "none"} label="Non classés" count={folderCounts.none} onSelect={() => view.setScope("none")} actions={[]} /></li> : null}
        {top.map((collection) => node(collection, 1))}
        {editing && "add" in editing && editing.add === null ? <li><NameField initial="" onDone={submit} depth={1} /></li> : null}
      </ul>
      {admin ? (
        <button type="button" onClick={() => setEditing({ add: null })} className="mt-2 flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-[13px] text-muted hover:bg-side hover:text-ink">
          <Icon name="plus" size={14} /> Nouvelle collection
        </button>
      ) : null}
      {!top.length && !admin ? <p className="px-2.5 text-[12.5px] text-muted">Pas encore de collection.</p> : null}
    </nav>
  );
}

type Action = { label: string; run: () => void; icon?: "plus" | "close"; text?: string };

function Row(props: { depth: number; active: boolean; label: string; count: number; onSelect: () => void; chevron?: { expanded: boolean; toggle: () => void }; actions: Action[] }) {
  return (
    <div className={cx("group flex items-center rounded-lg text-[13.5px]", props.active ? "bg-side font-medium text-ink" : "text-ink-soft hover:bg-side/60")} style={{ paddingLeft: (props.depth - 1) * 14 }}>
      {props.chevron ? (
        <button type="button" onClick={props.chevron.toggle} aria-label={props.chevron.expanded ? "Replier" : "Déplier"} aria-expanded={props.chevron.expanded} className="grid h-7 w-6 shrink-0 place-items-center text-muted">
          <Icon name="arrow" size={12} className={props.chevron.expanded ? "rotate-90" : ""} />
        </button>
      ) : <span className="w-6 shrink-0" />}
      <button type="button" onClick={props.onSelect} aria-current={props.active ? "true" : undefined} className="flex min-w-0 flex-1 items-center gap-2 py-1.5 pr-1 text-left">
        <span className="truncate">{props.label}</span>
        <span className="ml-auto text-[11.5px] text-muted tabular">{props.count.toLocaleString("fr-FR")}</span>
      </button>
      <span className="flex pr-1 opacity-0 transition-opacity group-focus-within:opacity-100 group-hover:opacity-100">
        {props.actions.map((action) => (
          <button key={action.label} type="button" onClick={action.run} aria-label={action.label} title={action.label} className="grid h-6 min-w-6 place-items-center rounded px-1 text-[11.5px] text-muted hover:text-ink">
            {action.icon ? <Icon name={action.icon} size={12} /> : action.text}
          </button>
        ))}
      </span>
    </div>
  );
}

function NameField({ initial, onDone, depth }: { initial: string; onDone: (name: string) => void; depth: number }) {
  return (
    <input
      autoFocus
      defaultValue={initial}
      maxLength={80}
      placeholder="Winter 26-27"
      aria-label="Nom de la collection"
      onBlur={(event) => onDone(event.target.value)}
      onKeyDown={(event) => {
        if (event.key === "Enter") event.currentTarget.blur();
        if (event.key === "Escape") { event.currentTarget.value = ""; event.currentTarget.blur(); }
      }}
      className="my-0.5 h-8 w-[calc(100%-1.5rem)] rounded-md border border-line-strong bg-paper px-2.5 text-[13.5px]"
      style={{ marginLeft: 24 + (depth - 1) * 14 }}
    />
  );
}

