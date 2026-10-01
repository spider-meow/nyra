import { useState } from "react";
import { Modal, useToast } from "../../components/feedback";
import { Button, FieldLabel, Input } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import { MAX_TAGS, splitTags } from "../../lib/format";
import { useOrg } from "../../lib/org";
import { useLibraryMutations } from "../../lib/queries";
import type { LibraryItem } from "../../types";

type Form = { expiry_date: string; credit: string; notes: string; tags: string[] };

export function EditReference(props: { item: LibraryItem | null; onClose: () => void; onDelete: (item: LibraryItem) => void }) {
  const { admin } = useOrg();
  const { updateMeta } = useLibraryMutations();
  const toast = useToast();
  const item = props.item;
  const [form, setForm] = useState<Form>({ expiry_date: "", credit: "", notes: "", tags: [] });
  const [loadedFor, setLoadedFor] = useState<string | null>(null);
  if (item && loadedFor !== item.id) {
    setLoadedFor(item.id);
    setForm({ expiry_date: item.expiry_date, credit: item.credit, notes: item.notes, tags: item.tags });
  }
  if (!item) return null;

  function save() {
    if (!item) return;
    updateMeta.mutate(
      { filename: item.filename, ...form },
      {
        onSuccess: () => {
          toast.show({ tone: "success", message: "Modifications enregistrées", description: item.filename, duration: 3000 });
          props.onClose();
        },
        onError: (error) => toast.show({ tone: "error", message: "L'enregistrement n'a pas abouti", description: errorMessage(error) }),
      },
    );
  }

  return (
    <Modal
      open
      onClose={props.onClose}
      title={item.filename}
      wide
      footer={
        admin ? (
          <>
            <Button variant="danger" className="mr-auto" onClick={() => props.onDelete(item)}>Supprimer</Button>
            <Button onClick={props.onClose}>Annuler</Button>
            <Button variant="primary" onClick={save} loading={updateMeta.isPending}>Enregistrer</Button>
          </>
        ) : (
          <Button onClick={props.onClose}>Fermer</Button>
        )
      }
    >
      <ReferenceFields item={item} form={form} setForm={setForm} />
    </Modal>
  );
}

function ReferenceFields({ item, form, setForm }: { item: LibraryItem; form: Form; setForm: (form: Form) => void }) {
  const { admin } = useOrg();
  return (
    <div className="grid gap-5 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <a href={item.url} target="_blank" rel="noreferrer noopener" className="block overflow-hidden rounded-2xl bg-side">
        <img src={item.url || item.thumb_url} alt="" className="aspect-square w-full object-contain" />
      </a>
      <div className="grid content-start gap-4">
        <div>
          <FieldLabel htmlFor="edit-expiry">Date d'expiration des droits</FieldLabel>
          <Input id="edit-expiry" type="date" disabled={!admin} value={form.expiry_date} onChange={(event) => setForm({ ...form, expiry_date: event.target.value })} />
        </div>
        <div>
          <FieldLabel htmlFor="edit-credit">Crédit</FieldLabel>
          <Input id="edit-credit" disabled={!admin} placeholder="Photographe, agence" value={form.credit} onChange={(event) => setForm({ ...form, credit: event.target.value })} />
        </div>
        <div>
          <FieldLabel htmlFor="edit-tags" hint="produit, campagne, shooting…">Tags</FieldLabel>
          <TagEditor id="edit-tags" tags={form.tags} disabled={!admin} onChange={(tags) => setForm({ ...form, tags })} />
        </div>
        <div>
          <FieldLabel htmlFor="edit-notes">Notes</FieldLabel>
          <textarea
            id="edit-notes"
            disabled={!admin}
            rows={4}
            placeholder="Usages autorisés, territoires, numéro de contrat…"
            value={form.notes}
            onChange={(event) => setForm({ ...form, notes: event.target.value })}
            className="w-full rounded-[10px] border border-line-strong bg-paper px-3.5 py-2.5 text-sm outline-none focus:border-focus focus:ring-4 focus:ring-focus-soft disabled:bg-canvas"
          />
        </div>
        <p className="text-xs text-muted">
          {item.width && item.height ? `${item.width} × ${item.height} px · ` : ""}
          {item.indexed ? (item.compared ? "Indexé et comparé au site" : "Indexé, pas encore comparé") : "Indexation en attente"}
        </p>
      </div>
    </div>
  );
}

/** Chips plus a text field: Enter or a comma adds a tag, Backspace on an empty field removes the last one. Suggestions come from the library's `#library-tags` list. */
function TagEditor(props: { id: string; tags: string[]; onChange: (tags: string[]) => void; disabled?: boolean }) {
  const [text, setText] = useState("");

  function commit(raw: string) {
    setText("");
    const added = splitTags(raw).filter((tag) => !props.tags.includes(tag));
    if (added.length) props.onChange([...props.tags, ...added].slice(0, MAX_TAGS));
  }

  return (
    <div className="flex flex-wrap gap-1.5 rounded-[10px] border border-line-strong bg-paper p-2 focus-within:border-focus focus-within:ring-4 focus-within:ring-focus-soft has-[input:disabled]:bg-canvas">
      {props.tags.map((tag) => (
        <span key={tag} className="inline-flex items-center gap-1 rounded-full bg-side py-1 pr-1.5 pl-2.5 text-[12.5px]">
          {tag}
          {!props.disabled ? (
            <button type="button" aria-label={`Retirer le tag ${tag}`} className="grid h-4 w-4 place-items-center rounded-full text-muted hover:bg-line hover:text-ink" onClick={() => props.onChange(props.tags.filter((item) => item !== tag))}>
              ×
            </button>
          ) : null}
        </span>
      ))}
      {!props.disabled && props.tags.length < MAX_TAGS ? (
        <input
          id={props.id}
          list="library-tags"
          value={text}
          maxLength={120}
          placeholder={props.tags.length ? "" : "miniature, classic…"}
          onChange={(event) => (/[,;|]/.test(event.target.value) ? commit(event.target.value) : setText(event.target.value))}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              commit(text);
            } else if (event.key === "Backspace" && !text && props.tags.length) {
              props.onChange(props.tags.slice(0, -1));
            }
          }}
          onBlur={() => commit(text)}
          // The container already shows the focus ring; the global :focus-visible outline would double it.
          style={{ outline: "none" }}
          className="min-w-[8rem] flex-1 bg-transparent px-1 py-1 text-sm placeholder:text-faint"
        />
      ) : props.disabled && !props.tags.length ? (
        <span className="px-1 py-1 text-sm text-muted">Aucun tag</span>
      ) : null}
    </div>
  );
}
