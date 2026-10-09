import { useState, type FormEvent } from "react";
import { Modal, useToast, type ToastInput } from "../../components/feedback";
import { Button, Checkbox, FieldLabel, Input } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import { formatDate, plural } from "../../lib/format";
import { useAssignments } from "../../lib/labels";
import { useSiteImageMutations } from "../../lib/queries";
import type { SiteImage } from "../../types";
import { typeOf } from "./Typing";

type Fields = { expiry: string; unlimited: boolean; credit: string; name: string };

type Adopted = { added: { site_image_id: string; filename: string }[]; failed: { reason: string }[] };

function adoptedToast(result: Adopted, hasRights: boolean): ToastInput {
  if (result.added.length && !result.failed.length) {
    return {
      tone: "success",
      message: result.added.length > 1 ? `${result.added.length} visuels ajoutés à la bibliothèque` : `${result.added[0].filename} ajouté à la bibliothèque`,
      description: hasRights
        ? "Ils sont déjà reliés à leurs pages : retrouvez-les dans « À traiter »."
        : "Ils sont reliés à leurs pages. Renseignez leur échéance dans la bibliothèque pour qu'ils comptent dans les alertes.",
    };
  }
  return {
    tone: result.added.length ? "info" : "error",
    message: `${plural(result.added.length, "ajouté", "ajoutés")}, ${plural(result.failed.length, "refusé", "refusés")}`,
    description: result.failed.map((item) => item.reason).filter((reason, index, all) => all.indexOf(reason) === index).join(" "),
  };
}

export function AddToLibrary(props: { items: SiteImage[] | null; onClose: () => void; onDone: (ids: string[]) => void }) {
  const { adopt } = useSiteImageMutations();
  const toast = useToast();
  const [fields, setFields] = useState<Fields>({ expiry: "", unlimited: false, credit: "", name: "" });
  const [openedFor, setOpenedFor] = useState<string | null>(null);
  const items = props.items;
  const assignments = useAssignments("site").data?.assignments;
  // Packshots nearly always come with unlimited rights, never surely: the box is ticked for the ones a person typed (not for a guess), and stays visible.
  const packshots = Boolean(items?.length) && items?.every((item) => typeOf(assignments?.[item.id] ?? [])?.name === "packshot" && typeOf(assignments?.[item.id] ?? [])?.source === "user") === true;
  const key = items?.map((item) => item.id).join(",") ?? null;
  if (key !== openedFor) {
    setOpenedFor(key);
    setFields({ expiry: "", unlimited: packshots, credit: "", name: items?.length === 1 ? items[0].filename : "" });
  }
  if (!items) return null;
  const single = items.length === 1;

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!items) return;
    adopt.mutate(
      {
        site_image_ids: items.map((item) => item.id),
        expiry_date: fields.expiry,
        unlimited_rights: fields.unlimited,
        credit: fields.credit,
        filenames: single && fields.name.trim() ? { [items[0].id]: fields.name.trim() } : {},
      },
      {
        onSuccess: (result) => {
          props.onDone(result.added.map((item) => item.site_image_id));
          props.onClose();
          toast.show(adoptedToast(result, Boolean(fields.expiry) || fields.unlimited));
        },
        onError: (error) => toast.show({ tone: "error", message: "L'ajout n'a pas abouti", description: errorMessage(error) }),
      },
    );
  }

  return (
    <Modal
      open
      onClose={props.onClose}
      title={single ? "Ajouter à la bibliothèque" : `Ajouter ${items.length} images à la bibliothèque`}
      footer={
        <>
          <Button onClick={props.onClose}>Annuler</Button>
          <Button variant="primary" type="submit" form="adopt-form" loading={adopt.isPending}>
            {single ? "Ajouter" : `Ajouter les ${items.length}`}
          </Button>
        </>
      }
    >
      <AdoptFields items={items} fields={fields} packshots={packshots} onChange={setFields} onSubmit={submit} />
    </Modal>
  );
}

function AdoptFields(props: { items: SiteImage[]; fields: Fields; packshots: boolean; onChange: (fields: Fields) => void; onSubmit: (event: FormEvent) => void }) {
  const { items, fields } = props;
  const { expiry } = fields;
  const single = items.length === 1;
  return (
    <form id="adopt-form" className="grid gap-4" onSubmit={props.onSubmit}>
      <div className="flex gap-2 overflow-x-auto">
        {items.slice(0, 8).map((item) => (
          <img key={item.id} src={item.thumb || undefined} alt="" className="h-16 w-16 shrink-0 rounded-md border border-line bg-canvas object-cover" />
        ))}
        {items.length > 8 ? <span className="self-center text-sm text-muted">+{items.length - 8}</span> : null}
      </div>
      {single ? (
        <div>
          <FieldLabel htmlFor="adopt-name">Nom dans la bibliothèque</FieldLabel>
          <Input id="adopt-name" maxLength={200} value={fields.name} onChange={(event) => props.onChange({ ...fields, name: event.target.value })} />
        </div>
      ) : (
        <p className="text-sm text-muted">Chaque image garde le nom de son fichier sur le site.</p>
      )}
      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <FieldLabel htmlFor="adopt-expiry" hint="facultatif">Date d'expiration des droits</FieldLabel>
          <Input id="adopt-expiry" type="date" disabled={fields.unlimited} value={fields.expiry} onChange={(event) => props.onChange({ ...fields, expiry: event.target.value })} />
          <div className="mt-2">
            <Checkbox checked={fields.unlimited} onChange={(value) => props.onChange({ ...fields, unlimited: value, expiry: value ? "" : fields.expiry })} label="Droits illimités" hint={props.packshots ? "Ce sont des packshots : le plus souvent à droits illimités, mais pas toujours. À vérifier." : "Jamais expiré : le visuel reste reconnu sur les sites, sans alerte."} />
          </div>
        </div>
        <div>
          <FieldLabel htmlFor="adopt-credit" hint="facultatif">Crédit</FieldLabel>
          <Input id="adopt-credit" maxLength={500} placeholder="Photographe, agence" value={fields.credit} onChange={(event) => props.onChange({ ...fields, credit: event.target.value })} />
        </div>
      </div>
      <p className="text-xs text-muted">
        {fields.unlimited
          ? "Droits illimités : le visuel reste reconnu sur les sites, sans jamais expirer."
          : expiry
          ? `Échéance ${new Date(expiry) < new Date() ? "déjà passée : ces visuels apparaîtront comme expirés et en ligne" : `au ${formatDate(expiry)}`}.`
          : "Sans échéance, le visuel est surveillé mais ne déclenche pas d'alerte d'expiration."}
      </p>
    </form>
  );
}
