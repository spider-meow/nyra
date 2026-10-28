import { useState } from "react";
import { Modal } from "../../components/feedback";
import { Button, Checkbox, FieldLabel, Input, Segmented, fieldClass } from "../../components/ui";
import { formatDate, plural } from "../../lib/format";
import type { Destination, UploadRights } from "../../lib/upload";
import type { CollectionIndex } from "./collections";

const NAMES_SHOWN = 6;
/** From this many files an import is a batch worth grouping: the dialog proposes a new collection first. */
const MASS_IMPORT = 10;

type Mode = "new" | "existing" | "none";

function importName(): string {
  return `Import du ${formatDate(new Date().toISOString().slice(0, 10))}`;
}

/** Where the files go when the dialog opens: the collection being viewed, else a new one for a mass import, else nowhere. */
function startingPoint(count: number, folders: CollectionIndex, current?: string): { mode: Mode; existing: string; name: string } {
  if (current && folders.byId.has(current)) return { mode: "existing", existing: current, name: importName() };
  return { mode: count >= MASS_IMPORT ? "new" : "none", existing: "", name: importName() };
}

type Props = {
  files: File[];
  folders: CollectionIndex;
  /** The collection open behind the dialog: files dropped there go there unless the person says otherwise. */
  current?: string;
  onCancel: () => void;
  onSend: (files: File[], rights: UploadRights, destination: Destination) => void;
};

/** Before files are sent: which collection they go in, and an optional expiry date (or unlimited rights) common to these files only. */
export function ImportDialog({ files, folders, current, onCancel, onSend }: Props) {
  const [rights, setRights] = useState<UploadRights>({ expiry: "", unlimited: false });
  const [mode, setMode] = useState<Mode>("none");
  const [existing, setExisting] = useState("");
  const [name, setName] = useState("");
  const [openedFor, setOpenedFor] = useState<File[]>([]);
  if (files !== openedFor) {
    setOpenedFor(files);
    if (!openedFor.length) {
      // Opening: each import starts from the context, with no common value. Files dropped onto the open dialog keep the choices made.
      const next = startingPoint(files.length, folders, current);
      setMode(next.mode);
      setExisting(next.existing);
      setName(next.name);
      setRights({ expiry: "", unlimited: false });
    }
  }
  const names = files.map((file) => file.name);
  const hasFolders = folders.byId.size > 0;
  const paths = [...folders.byId.keys()].map((id) => ({ id, path: folders.path(id) })).sort((a, b) => a.path.localeCompare(b.path, "fr", { numeric: true }));

  function submit() {
    const destination: Destination = mode === "new" ? { newName: name } : mode === "existing" ? { collectionId: existing } : {};
    onSend(files, rights, destination);
  }

  return (
    <Modal
      open={files.length > 0}
      onClose={onCancel}
      title={files.length > 1 ? `Ajouter ${files.length} visuels` : "Ajouter un visuel"}
      footer={
        <>
          <Button onClick={onCancel}>Annuler</Button>
          <Button variant="primary" type="submit" form="import-form">{files.length > 1 ? `Envoyer les ${files.length}` : "Envoyer"}</Button>
        </>
      }
    >
      <form id="import-form" className="grid grid-cols-[minmax(0,1fr)] gap-4" onSubmit={(event) => { event.preventDefault(); submit(); }}>
        <p className="break-words text-sm text-ink-soft">
          {names.slice(0, NAMES_SHOWN).join(", ")}
          {names.length > NAMES_SHOWN ? ` et ${plural(names.length - NAMES_SHOWN, "autre")}` : ""}
        </p>

        <div>
          <FieldLabel>Ranger dans</FieldLabel>
          <Segmented
            label="Collection de destination"
            value={mode}
            onChange={setMode}
            options={[
              { value: "new", label: "Nouvelle collection" },
              ...(hasFolders ? [{ value: "existing" as const, label: "Collection existante" }] : []),
              { value: "none", label: "Sans collection" },
            ]}
          />
          {mode === "new" ? (
            <div className="mt-2">
              <Input aria-label="Nom de la nouvelle collection" value={name} maxLength={80} required autoFocus onChange={(event) => setName(event.target.value)} />
              <p className="mt-1.5 text-xs text-muted">
                {files.length >= MASS_IMPORT ? "Regroupez cet envoi pour le retrouver d'un bloc. " : ""}Vous pourrez la renommer ensuite.
              </p>
            </div>
          ) : null}
          {mode === "existing" ? (
            <select aria-label="Collection existante" className={`${fieldClass} mt-2`} value={existing} required onChange={(event) => setExisting(event.target.value)}>
              <option value="" disabled>Choisir une collection…</option>
              {paths.map((folder) => <option key={folder.id} value={folder.id}>{folder.path}</option>)}
            </select>
          ) : null}
        </div>

        <div>
          <FieldLabel htmlFor="import-expiry" hint="facultatif">Date d'expiration commune</FieldLabel>
          <Input id="import-expiry" type="date" disabled={rights.unlimited} value={rights.expiry} onChange={(event) => setRights({ ...rights, expiry: event.target.value })} />
          <div className="mt-2">
            <Checkbox checked={rights.unlimited} onChange={(value) => setRights({ expiry: value ? "" : rights.expiry, unlimited: value })} label="Droits illimités" hint="Jamais expiré : le visuel reste reconnu sur les sites, sans alerte." />
          </div>
        </div>
        <p className="text-xs text-muted">
          {rights.expiry || rights.unlimited
            ? "Appliqué à ces fichiers seulement, aucun autre visuel de la bibliothèque n'est modifié. Si l'un d'eux remplace un visuel du même nom, il prend cette valeur aussi."
            : "Sans valeur commune, les nouveaux visuels n'ont pas d'échéance, et un visuel remplacé garde la sienne. Vous pourrez la saisir ensuite."}
        </p>
      </form>
    </Modal>
  );
}
