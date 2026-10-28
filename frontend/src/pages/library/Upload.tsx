import { useEffect, useRef, useState, type ReactNode } from "react";
import { useToast } from "../../components/feedback";
import { Icon } from "../../components/icons";
import { Button, Card, cx } from "../../components/ui";
import { carriesFiles, dragDepth, mergeFiles } from "../../lib/dropzone";
import { plural } from "../../lib/format";
import { useOrg } from "../../lib/org";
import { errorMessage } from "../../lib/api";
import { useLibraryMutations, useReferenceUpload } from "../../lib/queries";
import { NO_RIGHTS, type Destination, type UploadRights } from "../../lib/upload";
import type { CollectionIndex } from "./collections";
import { ImportDialog } from "./ImportDialog";

type Failure = { filename: string; reason: string };

/** Refused files grouped by reason: one explanation, then the names it applies to. */
function byReason(failures: Failure[]): [string, string[]][] {
  const groups = new Map<string, string[]>();
  for (const item of failures) groups.set(item.reason, [...(groups.get(item.reason) ?? []), item.filename]);
  return [...groups];
}

/**
 * Sends image files to the library (admins only, one batch at a time) and keeps what came back: refused files and replaced visuels.
 * `onFiled` is told which collection the files go to, so the page can show it.
 */
export function useUpload(onFiled: (collectionId: string) => void) {
  const { admin } = useOrg();
  const uploader = useReferenceUpload();
  const { addCollection } = useLibraryMutations();
  const toast = useToast();
  const fileInput = useRef<HTMLInputElement>(null);
  // The area of the visuals: the only place where a drop is taken (see the effect below).
  const zone = useRef<HTMLDivElement>(null);
  const [failures, setFailures] = useState<Failure[]>([]);
  // The refused files themselves, to send them again in one click.
  const [retryable, setRetryable] = useState<File[]>([]);
  const [replaced, setReplaced] = useState<string[]>([]);
  const [renamed, setRenamed] = useState<{ from: string; to: string }[]>([]);
  const [dragging, setDragging] = useState(false);
  // The files waiting for the common rights to be chosen (the dialog), and the rights of the last send, for a retry.
  const [staged, setStaged] = useState<File[]>([]);
  const [lastRights, setLastRights] = useState<UploadRights>(NO_RIGHTS);
  const [lastCollection, setLastCollection] = useState<string | undefined>();

  // A drop while the dialog is open adds to what it holds: the choices already made stay.
  function stage(files: File[]) {
    if (admin && files.length) setStaged((waiting) => mergeFiles(waiting, files));
  }

  async function upload(files: File[], rights: UploadRights = lastRights, destination: Destination = { collectionId: lastCollection }) {
    if (!admin || !files.length || addCollection.isPending) return; // a double click must not create the collection twice
    if (uploader.uploading) {
      toast.show({ message: "Un envoi est déjà en cours", description: "Ajoutez ces fichiers dès qu'il est terminé." });
      return;
    }
    let collectionId = destination.collectionId;
    if (destination.newName !== undefined) {
      try {
        // Created before sending, and kept for a retry: a second try must not make a second collection.
        collectionId = (await addCollection.mutateAsync({ name: destination.newName, parent_id: null })).collection.id;
      } catch (error) {
        toast.show({ tone: "error", message: "La collection n'a pas pu être créée", description: errorMessage(error) });
        return; // the dialog stays open: the name can be changed
      }
    }
    if (collectionId) onFiled(collectionId);
    setFailures([]);
    setRetryable([]);
    setReplaced([]);
    setRenamed([]);
    setStaged([]);
    setLastRights(rights);
    setLastCollection(collectionId);
    void uploader.run(files, rights, collectionId).then((result) => {
      setFailures(result.failed);
      setReplaced(result.replaced);
      setRenamed(result.renamed);
      const refused = new Set(result.failed.map((item) => item.filename));
      setRetryable(files.filter((file) => refused.has(file.name)));
    });
  }

  // A drop is taken in the visuals' area (`zone`, tall enough to be a target even when a collection is empty), or anywhere while the
  // import dialog is open. A file dropped anywhere else is still caught: otherwise the browser opens the image and the app is left.
  // Only file drags count.
  const dialogOpen = staged.length > 0;
  useEffect(() => {
    let depth = 0;
    const onDrag = (kind: "enter" | "leave" | "drop") => (event: DragEvent) => {
      if (!carriesFiles(event.dataTransfer?.types)) return;
      event.preventDefault(); // without it the browser navigates to the dropped file
      depth = dragDepth(depth, kind);
      setDragging(admin && depth > 0);
      if (kind !== "drop") return;
      if (dialogOpen || (event.target instanceof Node && zone.current?.contains(event.target))) stage(Array.from(event.dataTransfer?.files ?? []));
      else if (admin) toast.show({ message: "Déposez les fichiers dans la zone des visuels", description: "Elle s'allume dès que vous glissez des fichiers au-dessus de la page." });
    };
    // `dragover` fires all the time while dragging: it only has to keep the drop allowed.
    const allowDrop = (event: DragEvent) => {
      if (carriesFiles(event.dataTransfer?.types)) event.preventDefault();
    };
    const [enter, leave, drop] = [onDrag("enter"), onDrag("leave"), onDrag("drop")];
    window.addEventListener("dragenter", enter);
    window.addEventListener("dragover", allowDrop);
    window.addEventListener("dragleave", leave);
    window.addEventListener("drop", drop);
    return () => {
      window.removeEventListener("dragenter", enter);
      window.removeEventListener("dragover", allowDrop);
      window.removeEventListener("dragleave", leave);
      window.removeEventListener("drop", drop);
      setDragging(false);
    };
    // The listeners are rebuilt when what they read changes (`stage` only reads `admin`).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [admin, dialogOpen]);

  return {
    uploader, fileInput, zone, failures, retryable, replaced, renamed, dragging, upload, stage, staged,
    cancelStaged: () => setStaged([]),
    pick: () => fileInput.current?.click(),
    closeReplaced: () => setReplaced([]),
    closeRenamed: () => setRenamed([]),
    closeFailures: () => { setFailures([]); setRetryable([]); },
  };
}

type Upload = ReturnType<typeof useUpload>;

/** The CSV import button is hidden for now: build with VITE_CSV_IMPORT=1 to show it. */
const CSV_IMPORT_ENABLED = import.meta.env.VITE_CSV_IMPORT === "1";

/** Header buttons: CSV import, and the upload button with its hidden file input. */
export function LibraryActions({ up, onImport }: { up: Upload; onImport: () => void }) {
  const { admin } = useOrg();
  const { uploader } = up;

  return (
    <>
      {admin && CSV_IMPORT_ENABLED ? <Button onClick={onImport}>Importer (CSV)</Button> : null}
      {admin ? (
        <>
          <Button variant="primary" loading={uploader.uploading} onClick={up.pick}>
            {uploader.progress
              ? uploader.progress.total > 1
                ? `Envoi · ${uploader.progress.done}/${uploader.progress.total}`
                : "Envoi en cours…"
              : "Ajouter des visuels"}
          </Button>
          <input
            ref={up.fileInput}
            type="file"
            accept="image/jpeg,image/png,image/webp,image/gif,image/avif,image/tiff,image/bmp"
            multiple
            hidden
            onChange={(event) => {
              up.stage(Array.from(event.target.files ?? []));
              event.target.value = "";
            }}
          />
        </>
      ) : null}
    </>
  );
}

/** Banners above the library: drop hint, indexing in progress, replaced visuels, refused files. */
export function UploadNotices(props: { up: Upload; indexing: boolean; folders: CollectionIndex; current?: string }) {
  const { up } = props;
  return (
    <>
      {up.dragging && up.staged.length > 0 ? <DropBox fixed target="" adding /> : null}

      {props.indexing ? (
        <p className="mb-5 flex items-center gap-2.5 rounded-xl bg-peach-soft px-4 py-3 text-sm text-bark-800">
          <span aria-hidden className="h-3.5 w-3.5 shrink-0 animate-spin rounded-full border-2 border-bark-800/25 border-t-bark-800" />
          Indexation en cours : les nouveaux visuels seront comparés aux sites dès qu'elle sera terminée.
        </p>
      ) : null}

      <ImportDialog files={up.staged} folders={props.folders} current={props.current} onCancel={up.cancelStaged} onSend={up.upload} />
      {up.renamed.length ? <RenamedNotice names={up.renamed} onClose={up.closeRenamed} /> : null}
      {up.replaced.length ? <ReplacedNotice names={up.replaced} onClose={up.closeReplaced} /> : null}
      {up.failures.length ? <FailedNotice up={up} /> : null}
    </>
  );
}

/** Where dropped files will go: the open collection with its path, else the brand's library. */
function dropTarget(brandName: string, folders: CollectionIndex, current?: string): string {
  return current && folders.byId.has(current) ? folders.path(current) : `la bibliothèque de ${brandName}`;
}

/** The box that appears over the visuals' area while files are dragged over the page; over the whole window when the import dialog is open. */
function DropBox(props: { target: string; adding: boolean; fixed?: boolean }) {
  return (
    <div aria-hidden className={cx("pointer-events-none z-50 grid place-items-center bg-focus-soft/60 p-3 backdrop-blur-[1px]", props.fixed ? "fixed inset-0" : "absolute inset-0 z-10 rounded-xl")}>
      <div className="absolute inset-2 rounded-xl border-2 border-dashed border-focus" />
      <div className="relative flex max-w-md flex-col items-center gap-2 rounded-xl bg-paper px-8 py-6 text-center shadow-float">
        <span className="grid h-12 w-12 place-items-center rounded-full bg-yellow text-ink">
          <Icon name="upload" size={22} strokeWidth={2} />
        </span>
        <p className="font-display text-xl">{props.adding ? "Déposez pour ajouter à cet envoi" : "Déposez vos visuels"}</p>
        {props.adding ? null : <p className="break-words text-sm text-ink-soft">Ils seront ajoutés à {props.target}.</p>}
      </div>
    </div>
  );
}

/** The visuals' area, the drop target: tall enough to be hit when it is empty, with the drop box over it while files are dragged. */
export function DropZone(props: { up: Upload; folders: CollectionIndex; current?: string; children: ReactNode }) {
  const { brand } = useOrg();
  return (
    <div ref={props.up.zone} className="relative min-h-[55vh]">
      {props.children}
      {props.up.dragging && !props.up.staged.length ? <DropBox target={dropTarget(brand.name, props.folders, props.current)} adding={false} /> : null}
    </div>
  );
}

function RenamedNotice(props: { names: { from: string; to: string }[]; onClose: () => void }) {
  return (
    <Card className="mb-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium">
            {props.names.length > 1 ? `${props.names.length} visuels enregistrés sous un autre nom` : "1 visuel enregistré sous un autre nom"}
          </p>
          <p className="mt-0.5 text-[13px] text-muted">
            Un visuel de ce nom était déjà dans la bibliothèque : il n'a pas été touché, le nouveau a reçu un numéro. Cochez « Remplacer les visuels du même nom » à l'envoi pour mettre à jour l'image d'un visuel.
          </p>
          <ul className="mt-1 grid gap-0.5 break-words text-[13px] text-ink-soft">
            {props.names.map((item) => <li key={item.to}>{item.from} → <span className="font-medium text-ink">{item.to}</span></li>)}
          </ul>
        </div>
        <Button size="sm" variant="ghost" onClick={props.onClose}>Fermer</Button>
      </div>
    </Card>
  );
}

function ReplacedNotice(props: { names: string[]; onClose: () => void }) {
  return (
    <Card className="mb-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium">
            {props.names.length > 1 ? `${props.names.length} visuels remplacés` : "1 visuel remplacé"}
          </p>
          <p className="mt-0.5 text-[13px] text-muted">
            Vous avez demandé de remplacer les visuels du même nom : l'image a été remplacée, son échéance, son crédit et ses notes sont conservés (sauf si une échéance commune a été choisie à l'envoi).
          </p>
          <p className="mt-1 break-words text-[13px] text-ink-soft">{props.names.join(", ")}</p>
        </div>
        <Button size="sm" variant="ghost" onClick={props.onClose}>Fermer</Button>
      </div>
    </Card>
  );
}

function FailedNotice({ up }: { up: Upload }) {
  return (
    <Card className="mb-4 border-expired/30">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-expired">{plural(up.failures.length, "fichier refusé", "fichiers refusés")}</p>
          <div className="mt-2 grid gap-3">
            {byReason(up.failures).map(([reason, items]) => (
              <div key={reason}>
                <p className="text-[13px] font-medium text-ink">{reason}</p>
                <p className="mt-0.5 break-words text-[13px] text-ink-soft">{items.join(", ")}</p>
              </div>
            ))}
          </div>
        </div>
        <div className="flex gap-1">
          {up.retryable.length ? (
            <Button size="sm" loading={up.uploader.uploading} onClick={() => up.upload(up.retryable)}>
              Réessayer {up.retryable.length > 1 ? `les ${up.retryable.length}` : ""}
            </Button>
          ) : null}
          <Button size="sm" variant="ghost" onClick={up.closeFailures}>Fermer</Button>
        </div>
      </div>
    </Card>
  );
}
