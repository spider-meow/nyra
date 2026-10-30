import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router";
import { useToast } from "../components/feedback";
import { Button, EmptyState, PageHeader, Skeleton } from "../components/ui";
import { errorMessage } from "../lib/api";
import { useOrg } from "../lib/org";
import { useAssignments, useLabels } from "../lib/labels";
import { useLibrary, useLibraryThumbs } from "../lib/queries";
import type { LibraryItem } from "../types";
import { readAddress, writeAddress } from "./library/address";
import { EditReference } from "./library/EditReference";
import { ImportCsv } from "./library/ImportCsv";
import { CollectionTree, FolderBar } from "./library/CollectionTree";
import { LibraryGrid, RecentCards } from "./library/Grid";
import { SelectionHeader, useRemoveReferences } from "./library/Selection";
import { LibraryToolbar } from "./library/Toolbar";
import { DropZone, LibraryActions, UploadNotices, useUpload } from "./library/Upload";
import { newest, useLibraryView, type Tab } from "./library/view";

const RECENT_CARDS = 4; // one row at the widest layout

/** What stands in for the library while it loads, fails to load, or is empty. */
function LibraryNotice(props: { loading: boolean; error: unknown; admin: boolean; uploading: boolean; onPick: () => void }) {
  if (props.loading) return <div className="grid gap-2">{Array.from({ length: 6 }, (_, index) => <Skeleton key={index} className="h-14" />)}</div>;
  if (props.error) return <EmptyState title="Impossible de charger la bibliothèque" body={errorMessage(props.error)} />;
  return (
    <EmptyState
      title="Aucun visuel pour l'instant"
      body={props.admin ? "Glissez vos images ici, ou utilisez « Ajouter des visuels ». Vous renseignerez ensuite leur date d'expiration, une par une." : "Un administrateur doit d'abord déposer les visuels à surveiller."}
      action={props.admin ? <Button variant="primary" loading={props.uploading} onClick={props.onPick}>Ajouter des visuels</Button> : undefined}
    />
  );
}

/** The cards ticked. Bulk actions only touch what is on screen: a card hidden by the search, a tag or a status filter is never acted on. */
function usePicked(visible: LibraryItem[]) {
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const selected = useMemo(() => new Set(visible.filter((item) => picked.has(item.filename)).map((item) => item.filename)), [visible, picked]);
  // A card that leaves the screen also leaves the selection, so it does not come back checked once the filters are cleared.
  if (selected.size !== picked.size) setPicked(selected);
  return { setPicked, selected, clear: () => setPicked(new Set()) };
}

/**
 * The visual whose sheet is open, from the address (`?visual=<id>`, AXE-633): opening one adds an entry to the history (so "Retour" closes it),
 * closing replaces it. A link to a visual or a collection that is gone says so once, and the address is corrected to what is shown.
 */
function useOpenVisual(items: LibraryItem[], ready: boolean, view: { missingScope: boolean; setScope: (scope: "all", replace?: boolean) => void }) {
  const [params, setParams] = useSearchParams();
  const toast = useToast();
  const told = useRef(new Set<string>()); // once each: a development double render must not say it twice
  const asked = readAddress(params).visual;
  const editing = asked ? items.find((item) => item.id === asked) ?? null : null;
  const closeVisual = useCallback(() => setParams((current) => writeAddress(current, { visual: null }), { replace: true }), [setParams]);
  const missingVisual = ready && asked !== null && !editing;
  const { missingScope, setScope } = view;
  useEffect(() => {
    if (missingVisual && !told.current.has(`visual:${asked}`)) {
      told.current.add(`visual:${asked}`);
      toast.show({ tone: "error", message: "Ce visuel n'existe plus", description: "Il a été supprimé, ou le lien n'est pas celui de cette marque." });
      closeVisual();
    }
  }, [missingVisual, asked, toast, closeVisual]);
  useEffect(() => {
    if (missingScope && !told.current.has("scope")) {
      told.current.add("scope");
      toast.show({ tone: "error", message: "Cette collection n'existe plus", description: "Voici « Tous les visuels »." });
      setScope("all", true);
    }
    if (!missingScope) told.current.delete("scope");
  }, [missingScope, toast, setScope]);
  return { editing, openVisual: (item: LibraryItem) => setParams((current) => writeAddress(current, { visual: item.id })), closeVisual };
}

export function Library() {
  const { admin } = useOrg();
  const library = useLibrary();
  const items = library.data?.items ?? [];
  const assignments = useAssignments("reference").data?.assignments;
  const labelsOf = useCallback((id: string) => assignments?.[id] ?? [], [assignments]);
  const collections = library.data?.collections ?? [];
  const ready = library.isSuccess;
  const view = useLibraryView(items, collections, labelsOf, ready);
  const { editing, openVisual, closeVisual } = useOpenVisual(items, ready, view);
  const labels = useLabels().data?.labels ?? [];
  const up = useUpload(view.setScope); // after an import, the library shows the collection it went to
  const openFolder = view.scope === "all" || view.scope === "none" ? undefined : view.scope; // dropping in a collection proposes it
  const { setPicked, selected, clear } = usePicked(view.visible);
  // A brand with collections lands on its newest few visuals: only their thumbnails are signed until a folder is opened or "Afficher tous les visuels" pressed.
  const [showAll, setShowAll] = useState(false);
  const browsing = view.scope === "all" && !view.filtering && !showAll && collections.length > 0;
  const recent = useMemo(() => (browsing ? newest(view.visible, RECENT_CARDS) : []), [browsing, view.visible]);
  const thumbs = useLibraryThumbs((browsing ? recent : view.visible.slice(0, view.shown)).map((item) => item.filename));
  const [importOpen, setImportOpen] = useState(false);
  const changeTab = (next: Tab) => { view.changeTab(next); clear(); };
  const pickedItems = useMemo(() => items.filter((item) => selected.has(item.filename)), [items, selected]);
  const { removeNames, removing } = useRemoveReferences((names) => {
    setPicked((current) => new Set([...current].filter((name) => !names.includes(name)))); // keep the picks that were not deleted
    closeVisual();
  });

  return (
    <div className="relative">
      {admin && selected.size > 0 ? (
        <SelectionHeader items={pickedItems} visibleCount={view.visible.length} folders={view.folders} labels={labels} onSelectAll={() => setPicked(new Set(view.visible.map((item) => item.filename)))} onClear={clear} removing={removing} onRemove={removeNames} />
      ) : (
        <PageHeader title="Bibliothèque" actions={<LibraryActions up={up} onImport={() => setImportOpen(true)} />} />
      )}
      <UploadNotices up={up} indexing={Boolean(library.data?.indexing)} folders={view.folders} current={openFolder} />

      {library.isLoading || library.error || !items.length ? (
        <DropZone up={up} folders={view.folders} current={openFolder}>
          <LibraryNotice loading={library.isLoading} error={library.error} admin={admin} uploading={up.uploader.uploading} onPick={up.pick} />
        </DropZone>
      ) : (
        <div className="lg:grid lg:grid-cols-[17rem_minmax(0,1fr)] lg:items-start lg:gap-8">
          <CollectionTree view={view} />
          <div className="min-w-0">
          <FolderBar view={view} />
          <LibraryToolbar view={view} onTab={changeTab} landing={browsing} />
          {thumbs.failed ? <p role="status" className="mb-3 text-[13px] text-muted">Vignettes indisponibles pour le moment.</p> : null}
          <DropZone up={up} folders={view.folders} current={openFolder}>
            {browsing ? (
              <RecentCards items={recent} total={view.visible.length} thumbs={thumbs.urls} labelsOf={labelsOf} onEdit={openVisual} onAll={() => setShowAll(true)} />
            ) : (
              <LibraryGrid view={view} thumbs={thumbs.urls} labelsOf={labelsOf} selected={selected} setSelected={setPicked} onEdit={openVisual} />
            )}
          </DropZone>
          </div>
        </div>
      )}

      <EditReference item={editing} thumbUrl={editing ? thumbs.urls[editing.filename] : undefined} onClose={closeVisual} onDelete={(item) => void removeNames([item.filename])} />
      <ImportCsv open={importOpen} onClose={() => setImportOpen(false)} />
    </div>
  );
}
