import { useState } from "react";
import { Modal } from "../../components/feedback";
import { Button, Checkbox, FieldLabel, Input } from "../../components/ui";
import { plural } from "../../lib/format";
import type { UploadRights } from "../../lib/upload";

const NAMES_SHOWN = 6;

/** Before files are sent: an optional expiry date (or unlimited rights) common to these files, and to no other visuel. */
export function ImportDialog(props: { files: File[]; onCancel: () => void; onSend: (files: File[], rights: UploadRights) => void }) {
  const { files } = props;
  const [rights, setRights] = useState<UploadRights>({ expiry: "", unlimited: false });
  const [openedFor, setOpenedFor] = useState<File[]>([]);
  if (files !== openedFor) {
    setOpenedFor(files);
    setRights({ expiry: "", unlimited: false }); // each import starts without a common value
  }
  const names = files.map((file) => file.name);

  return (
    <Modal
      open={files.length > 0}
      onClose={props.onCancel}
      title={files.length > 1 ? `Ajouter ${files.length} visuels` : "Ajouter un visuel"}
      footer={
        <>
          <Button onClick={props.onCancel}>Annuler</Button>
          <Button variant="primary" type="submit" form="import-form">{files.length > 1 ? `Envoyer les ${files.length}` : "Envoyer"}</Button>
        </>
      }
    >
      <form id="import-form" className="grid gap-4" onSubmit={(event) => { event.preventDefault(); props.onSend(files, rights); }}>
        <p className="break-words text-sm text-ink-soft">
          {names.slice(0, NAMES_SHOWN).join(", ")}
          {names.length > NAMES_SHOWN ? ` et ${plural(names.length - NAMES_SHOWN, "autre")}` : ""}
        </p>
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
