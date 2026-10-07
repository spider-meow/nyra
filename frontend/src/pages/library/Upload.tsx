import { useRef, useState, type DragEvent } from "react";
import { useToast } from "../../components/feedback";
import { Button, Card } from "../../components/ui";
import { plural } from "../../lib/format";
import { useOrg } from "../../lib/org";
import { useReferenceUpload } from "../../lib/queries";

type Failure = { filename: string; reason: string };

/** Refused files grouped by reason: one explanation, then the names it applies to. */
function byReason(failures: Failure[]): [string, string[]][] {
  const groups = new Map<string, string[]>();
  for (const item of failures) groups.set(item.reason, [...(groups.get(item.reason) ?? []), item.filename]);
  return [...groups];
}

/** Sends image files to the library (admins only, one batch at a time) and keeps what came back: refused files and replaced visuels. */
export function useUpload() {
  const { admin } = useOrg();
  const uploader = useReferenceUpload();
  const toast = useToast();
  const fileInput = useRef<HTMLInputElement>(null);
  const [failures, setFailures] = useState<Failure[]>([]);
  // The refused files themselves, to send them again in one click.
  const [retryable, setRetryable] = useState<File[]>([]);
  const [replaced, setReplaced] = useState<string[]>([]);
  const [dragging, setDragging] = useState(false);

  function upload(files: File[]) {
    if (!admin || !files.length) return;
    if (uploader.uploading) {
      toast.show({ message: "Un envoi est déjà en cours", description: "Ajoutez ces fichiers dès qu'il est terminé." });
      return;
    }
    setFailures([]);
    setRetryable([]);
    setReplaced([]);
    void uploader.run(files).then((result) => {
      setFailures(result.failed);
      setReplaced(result.replaced);
      const refused = new Set(result.failed.map((item) => item.filename));
      setRetryable(files.filter((file) => refused.has(file.name)));
    });
  }

  // Spread on the page: dropping image files anywhere on it uploads them.
  const dropProps = {
    onDragOver: (event: DragEvent) => {
      if (!admin) return;
      event.preventDefault();
      setDragging(true);
    },
    onDragLeave: (event: DragEvent) => {
      if (event.currentTarget === event.target) setDragging(false);
    },
    onDrop: (event: DragEvent) => {
      event.preventDefault();
      setDragging(false);
      upload(Array.from(event.dataTransfer.files));
    },
  };

  return {
    uploader, fileInput, failures, retryable, replaced, dragging, dropProps, upload,
    pick: () => fileInput.current?.click(),
    closeReplaced: () => setReplaced([]),
    closeFailures: () => { setFailures([]); setRetryable([]); },
  };
}

type Upload = ReturnType<typeof useUpload>;

/** Header buttons: CSV import, and the upload button with its hidden file input. */
export function LibraryActions({ up, onImport }: { up: Upload; onImport: () => void }) {
  const { admin } = useOrg();
  const { uploader } = up;

  return (
    <>
      {admin ? <Button onClick={onImport}>Importer (CSV)</Button> : null}
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
              up.upload(Array.from(event.target.files ?? []));
              event.target.value = "";
            }}
          />
        </>
      ) : null}
    </>
  );
}

/** Banners above the library: drop hint, indexing in progress, replaced visuels, refused files. */
export function UploadNotices(props: { up: Upload; indexing: boolean }) {
  const { brand } = useOrg();
  const { up } = props;
  return (
    <>
      {up.dragging ? (
        <p className="pointer-events-none fixed inset-x-0 top-6 z-40 mx-auto w-fit rounded-full border border-line-strong bg-bar px-4 py-2 text-sm text-white shadow-float">
          Déposez pour ajouter à la bibliothèque de {brand.name}
        </p>
      ) : null}

      {props.indexing ? (
        <p className="mb-5 flex items-center gap-2.5 rounded-xl bg-peach-soft px-4 py-3 text-sm text-bark-800">
          <span aria-hidden className="h-3.5 w-3.5 shrink-0 animate-spin rounded-full border-2 border-bark-800/25 border-t-bark-800" />
          Indexation en cours : les nouveaux visuels seront comparés aux sites dès qu'elle sera terminée.
        </p>
      ) : null}

      {up.replaced.length ? <ReplacedNotice names={up.replaced} onClose={up.closeReplaced} /> : null}
      {up.failures.length ? <FailedNotice up={up} /> : null}
    </>
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
            Un visuel du même nom était déjà dans la bibliothèque : l'image a été remplacée, son échéance, son crédit et ses notes sont conservés.
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
