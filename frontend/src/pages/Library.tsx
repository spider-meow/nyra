import { useCallback, useMemo, useState } from "react";
import { Button, EmptyState, PageHeader, Skeleton, cx } from "../components/ui";
import { errorMessage } from "../lib/api";
import { useOrg } from "../lib/org";
import { useAssignments, useLabels } from "../lib/labels";
import { useLibrary, useLibraryThumbs } from "../lib/queries";
import type { LibraryItem } from "../types";
import { EditReference } from "./library/EditReference";
import { ImportCsv } from "./library/ImportCsv";
import { LibraryGrid } from "./library/Grid";
import { SelectionBar, useBulkEdit, useRemoveReferences } from "./library/Selection";
import { LibraryToolbar } from "./library/Toolbar";
import { LibraryActions, UploadNotices, useUpload } from "./library/Upload";
import { useLibraryView, type Tab } from "./library/view";

export function Library() {
  const { admin } = useOrg();
  const library = useLibrary();
  const items = library.data?.items ?? [];
  const assignments = useAssignments("reference").data?.assignments;
  const labelsOf = useCallback((id: string) => assignments?.[id] ?? [], [assignments]);
  const view = useLibraryView(items, labelsOf);
  const labels = useLabels().data?.labels ?? [];
  const up = useUpload();
  const [picked, setPicked] = useState<Set<string>>(new Set());
  // Bulk actions only touch what is on screen: a card hidden by the search, a tag or a status filter is never acted on.
  const selected = useMemo(() => new Set(view.visible.filter((item) => picked.has(item.filename)).map((item) => item.filename)), [view.visible, picked]);
  // A card that leaves the screen also leaves the selection, so it does not come back checked once the filters are cleared.
  if (selected.size !== picked.size) setPicked(selected);
  const thumbs = useLibraryThumbs(view.visible.slice(0, view.shown).map((item) => item.filename));
  const [editing, setEditing] = useState<LibraryItem | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const clear = () => setPicked(new Set());
  const changeTab = (next: Tab) => { view.changeTab(next); clear(); };
  const bulk = useBulkEdit(selected, clear);
  const { removeNames, removing } = useRemoveReferences((names) => {
    setPicked((current) => new Set([...current].filter((name) => !names.includes(name)))); // keep the picks that were not deleted
    setEditing(null);
  });

  return (
    <div
      {...up.dropProps}
      className={cx("relative", admin && selected.size > 0 && "pb-20", up.dragging && "after:pointer-events-none after:absolute after:inset-0 after:rounded-xl after:border-2 after:border-dashed after:border-focus after:bg-focus-soft/40")}
    >
      <PageHeader
        title="Bibliothèque"
        description="Les visuels sous droits, leur date d'expiration et leurs tags. C'est à eux que chaque page lue est comparée, même une fois expirés."
        actions={<LibraryActions up={up} onImport={() => setImportOpen(true)} />}
      />
      <UploadNotices up={up} indexing={Boolean(library.data?.indexing)} />

      {library.isLoading ? (
        <div className="grid gap-2">{Array.from({ length: 6 }, (_, index) => <Skeleton key={index} className="h-14" />)}</div>
      ) : library.error ? (
        <EmptyState title="Impossible de charger la bibliothèque" body={errorMessage(library.error)} />
      ) : !items.length ? (
        <EmptyState
          title="Aucun visuel pour l'instant"
          body={admin ? "Glissez vos images ici, ou utilisez « Ajouter des visuels ». Vous renseignerez ensuite leur date d'expiration, une par une ou par import CSV." : "Un administrateur doit d'abord déposer les visuels à surveiller."}
          action={admin ? <Button variant="primary" loading={up.uploader.uploading} onClick={up.pick}>Ajouter des visuels</Button> : undefined}
        />
      ) : (
        <>
          <LibraryToolbar view={view} onTab={changeTab} />
          {admin && selected.size ? <SelectionBar selected={selected} referenceIds={items.filter((item) => selected.has(item.filename)).map((item) => item.id)} labels={labels} bulk={bulk} removing={removing} onRemove={removeNames} onClear={clear} /> : null}
          {thumbs.failed ? <p role="status" className="mb-3 text-[13px] text-muted">Vignettes indisponibles pour le moment.</p> : null}
          <LibraryGrid view={view} thumbs={thumbs.urls} labelsOf={labelsOf} selected={selected} setSelected={setPicked} onEdit={setEditing} onPick={up.pick} />
        </>
      )}

      <EditReference item={editing} thumbUrl={editing ? thumbs.urls[editing.filename] : undefined} onClose={() => setEditing(null)} onDelete={(item) => void removeNames([item.filename])} />
      <ImportCsv open={importOpen} onClose={() => setImportOpen(false)} />
    </div>
  );
}
