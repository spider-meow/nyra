import { useMemo } from "react";
import { SettingsBlock } from "../../components/SettingsBlock";
import { Button, cx, EmptyState, Skeleton } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import { useLibrary } from "../../lib/queries";
import type { Collection } from "../../types";
import { collectionIndex, MAX_DEPTH, moveTargets, type CollectionIndex } from "../library/collections";
import { MoveField, NameField, useFolderActions } from "../library/folderEdit";
import { useSortable, type HandleProps } from "./sortable";

/** The four actions of a row, in four fixed slots: an action is always in the same column, whatever the level. */
function RowActions(props: { collection: Collection; depth: number; folders: CollectionIndex; actions: ReturnType<typeof useFolderActions> }) {
  const { collection, depth, folders, actions } = props;
  const { setEditing, remove } = actions;
  return (
    <span className="ml-auto flex flex-wrap items-center justify-end">
      {depth < MAX_DEPTH ? <Button size="sm" variant="ghost" className="w-[7.75rem] px-2" aria-label={`+ Sous-collection de ${collection.name}`} onClick={() => setEditing({ add: collection.id })}>+ Sous-collection</Button> : <span className="w-[7.75rem] px-2" />}
      <Button size="sm" variant="ghost" className="w-[4.75rem] px-2" aria-label={`Renommer ${collection.name}`} onClick={() => setEditing({ rename: collection.id })}>Renommer</Button>
      {moveTargets(folders, collection).length ? <Button size="sm" variant="ghost" className="w-[4.5rem] px-2" aria-label={`Déplacer ${collection.name}`} onClick={() => setEditing({ move: collection.id })}>Déplacer</Button> : <span className="w-[4.5rem]" />}
      <Button size="sm" variant="ghost" className="w-20 px-2 text-expired! hover:text-expired!" aria-label={`Supprimer ${collection.name}`} onClick={() => void remove(collection)}>Supprimer</Button>
    </span>
  );
}

/** The grip that orders a row (a level with a single row has nothing to order: an empty place of the same width keeps the names aligned). */
function Grip(props: { handle: HandleProps | null }) {
  if (!props.handle) return <span className="mr-1 w-6 shrink-0 pointer-coarse:w-11" />;
  return (
    <button type="button" {...props.handle} className="mr-1 grid h-7 w-6 shrink-0 cursor-grab touch-none place-items-center rounded text-faint hover:text-ink focus-visible:text-ink active:cursor-grabbing pointer-coarse:h-11 pointer-coarse:w-11">
      <span aria-hidden>⠿</span>
    </button>
  );
}

/** The folders of the brand's library, on up to three levels: what the library's side menu shows. Created, renamed, moved, ordered (grip or arrow keys) and deleted here. */
export function Collections() {
  const library = useLibrary();
  const folders = useMemo(() => collectionIndex(library.data?.collections ?? []), [library.data]);
  const actions = useFolderActions(folders);
  const { editing, setEditing, submit, move, reorder } = actions;
  const sort = useSortable(reorder);
  const adding = (parent: string | null) => editing !== null && "add" in editing && editing.add === parent;

  // A function, not a component: the name field must keep its focus while the list redraws. `siblings`: the level the row is in, in order.
  function node(collection: Collection, depth: number, index: number, siblings: Collection[]) {
    const kids = folders.children.get(collection.id) ?? [];
    const ids = siblings.map((sibling) => sibling.id);
    return (
      <li key={collection.id} className={cx("relative", sort.rowClass(collection.parent_id, collection.id, index, siblings.length))}>
        <div data-row className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 border-t border-line py-2 pr-4 text-sm first:border-t-0" style={{ paddingLeft: 20 + (depth - 1) * 24 }}>
          {editing && "rename" in editing && editing.rename === collection.id ? <NameField initial={collection.name} label={`Nouveau nom de ${collection.name}`} onDone={submit} />
            : editing && "move" in editing && editing.move === collection.id ? <MoveField folders={folders} collection={collection} onDone={(parentId) => move(collection.id, parentId)} />
            : (
              <>
                <Grip handle={ids.length > 1 ? sort.handle(collection.parent_id, ids, collection.id, collection.name) : null} />
                <span className={cx("min-w-0 flex-1 basis-40 [overflow-wrap:anywhere]", depth === 1 && "font-medium")}>
                  {depth > 1 ? <span aria-hidden className="mr-2 text-faint">↳</span> : null}
                  {collection.name}
                </span>
                <RowActions collection={collection} depth={depth} folders={folders} actions={actions} />
              </>
            )}
        </div>
        {kids.length || adding(collection.id) ? (
          <ul>
            {kids.map((child, at) => node(child, depth + 1, at, kids))}
            {adding(collection.id) ? <li className="border-t border-line py-2" style={{ paddingLeft: 20 + depth * 24 }}><NameField initial="" label={`Nom de la nouvelle sous-collection de ${collection.name}`} onDone={submit} /></li> : null}
          </ul>
        ) : null}
      </li>
    );
  }

  if (library.isLoading) return <Skeleton className="h-40" />;
  if (library.error) return <EmptyState title="Impossible de charger les collections" body={errorMessage(library.error)} />;
  const top = folders.children.get(null) ?? [];
  return (
    <SettingsBlock
      title="Arborescence de la bibliothèque"
      description="Trois niveaux au plus : un type d'asset, une collection, une collection spécifique. Glissez la poignée ⠿ (ou ses flèches haut et bas au clavier) pour choisir l'ordre à l'intérieur d'un niveau. Les visuels ne sont jamais supprimés avec une collection : ils repassent dans « Non classés »."
      aside={<Button size="sm" onClick={() => setEditing({ add: null })}>Nouvelle collection</Button>}
    >
      <p role="status" className="sr-only">{sort.said}</p>
      {top.length || adding(null) ? (
        <ul>
          {top.map((collection, index) => node(collection, 1, index, top))}
          {adding(null) ? <li className="border-t border-line px-5 py-2 first:border-t-0"><NameField initial="" label="Nom de la nouvelle collection" onDone={submit} /></li> : null}
        </ul>
      ) : <p className="px-5 py-4 text-sm text-muted">Aucune collection pour l'instant.</p>}
    </SettingsBlock>
  );
}
