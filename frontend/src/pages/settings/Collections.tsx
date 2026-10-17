import { useMemo } from "react";
import { SettingsBlock } from "../../components/SettingsBlock";
import { Button, cx, EmptyState, Skeleton } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import { useLibrary } from "../../lib/queries";
import type { Collection } from "../../types";
import { collectionIndex, MAX_DEPTH, moveTargets } from "../library/collections";
import { MoveField, NameField, useFolderActions } from "../library/folderEdit";

/** The folders of the brand's library, on up to three levels: what the library's side menu shows. Created, renamed, moved and deleted here. */
export function Collections() {
  const library = useLibrary();
  const folders = useMemo(() => collectionIndex(library.data?.collections ?? []), [library.data]);
  const { editing, setEditing, submit, move, remove } = useFolderActions(folders);
  const adding = (parent: string | null) => editing !== null && "add" in editing && editing.add === parent;

  // A function, not a component: the name field must keep its focus while the list redraws.
  function node(collection: Collection, depth: number) {
    const kids = folders.children.get(collection.id) ?? [];
    return (
      <li key={collection.id}>
        <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 border-t border-line py-2 pr-4 text-sm first:border-t-0" style={{ paddingLeft: 20 + (depth - 1) * 24 }}>
          {editing && "rename" in editing && editing.rename === collection.id ? <NameField initial={collection.name} label={`Nouveau nom de ${collection.name}`} onDone={submit} />
            : editing && "move" in editing && editing.move === collection.id ? <MoveField folders={folders} collection={collection} onDone={(parentId) => move(collection.id, parentId)} />
            : (
              <>
                <span className={cx("min-w-0 flex-1 basis-40 [overflow-wrap:anywhere]", depth === 1 && "font-medium")}>
                  {depth > 1 ? <span aria-hidden className="mr-2 text-faint">↳</span> : null}
                  {collection.name}
                </span>
                {/* Four fixed slots: an action is always in the same column, whatever the level. */}
                <span className="ml-auto flex items-center">
                  {depth < MAX_DEPTH ? <Button size="sm" variant="ghost" className="w-[7.75rem] px-2" aria-label={`+ Sous-collection de ${collection.name}`} onClick={() => setEditing({ add: collection.id })}>+ Sous-collection</Button> : <span className="w-[7.75rem] px-2" />}
                  <Button size="sm" variant="ghost" className="w-[4.75rem] px-2" aria-label={`Renommer ${collection.name}`} onClick={() => setEditing({ rename: collection.id })}>Renommer</Button>
                  {moveTargets(folders, collection).length ? <Button size="sm" variant="ghost" className="w-[4.5rem] px-2" aria-label={`Déplacer ${collection.name}`} onClick={() => setEditing({ move: collection.id })}>Déplacer</Button> : <span className="w-[4.5rem]" />}
                  <Button size="sm" variant="ghost" className="w-20 px-2 text-expired! hover:text-expired!" aria-label={`Supprimer ${collection.name}`} onClick={() => void remove(collection)}>Supprimer</Button>
                </span>
              </>
            )}
        </div>
        {kids.length || adding(collection.id) ? (
          <ul>
            {kids.map((child) => node(child, depth + 1))}
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
      description="Trois niveaux au plus : un type d'asset, une collection, une collection spécifique. Les visuels ne sont jamais supprimés avec une collection : ils repassent dans « Non classés »."
      aside={<Button size="sm" onClick={() => setEditing({ add: null })}>Nouvelle collection</Button>}
    >
      {top.length || adding(null) ? (
        <ul>
          {top.map((collection) => node(collection, 1))}
          {adding(null) ? <li className="border-t border-line px-5 py-2 first:border-t-0"><NameField initial="" label="Nom de la nouvelle collection" onDone={submit} /></li> : null}
        </ul>
      ) : <p className="px-5 py-4 text-sm text-muted">Aucune collection pour l'instant.</p>}
    </SettingsBlock>
  );
}
