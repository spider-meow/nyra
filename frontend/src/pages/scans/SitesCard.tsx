import { useState, type FormEvent } from "react";
import { useConfirm, useToast } from "../../components/feedback";
import { Button, Card, FieldLabel, Input, Skeleton } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import { formatDateTime, hostOf, plural } from "../../lib/format";
import { useOrg } from "../../lib/org";
import { useSiteMutations, type useSites } from "../../lib/queries";
import type { Site } from "../../types";

/** The brand's addresses, each one tickable for the next read, and the form that adds one. */
export function SitesCard(props: { sites: ReturnType<typeof useSites>; skipped: Set<string>; onToggle: (id: string, on: boolean) => void }) {
  const { admin } = useOrg();
  const { sites } = props;
  const list = sites.data?.sites ?? [];
  return (
    <Card className="mb-6" padded={false}>
      <div className="px-5 pt-5">
        <h2 className="font-semibold">Adresses</h2>
        {admin && list.length > 1 ? <p className="mt-1 text-sm text-muted">Cochez celles à lire.</p> : null}
      </div>
      {sites.isLoading ? (
        <div className="p-5"><Skeleton className="h-16" /></div>
      ) : sites.error ? (
        <p className="px-5 pt-2 pb-5 text-sm text-expired">{errorMessage(sites.error)}</p>
      ) : list.length ? (
        <ul className="mt-3 divide-y divide-line border-t border-line">
          {list.map((site) => (
            <SiteRow key={site.id} site={site} selectable={admin && list.length > 1} selected={!props.skipped.has(site.id)} onSelect={(on) => props.onToggle(site.id, on)} />
          ))}
        </ul>
      ) : (
        <p className="px-5 pt-2 pb-5 text-sm text-muted">
          {admin ? "Aucune adresse pour l'instant. Ajoutez la première ci-dessous." : "Aucune adresse pour l'instant. Un administrateur doit en ajouter une."}
        </p>
      )}
      {admin ? <AddSite /> : null}
    </Card>
  );
}

function useSiteRemoval(site: Site, remove: ReturnType<typeof useSiteMutations>["remove"]) {
  const confirm = useConfirm();
  const toast = useToast();

  async function del() {
    const ok = await confirm({
      title: "Retirer cette adresse ?",
      body: (
        <>
          <p>Les pages lues sur {site.url}, les images trouvées et leurs correspondances seront effacées. La bibliothèque n'est pas touchée.</p>
          <p className="mt-2 font-medium">Cette action est définitive.</p>
        </>
      ),
      confirm: "Retirer",
      danger: true,
    });
    if (!ok) return;
    const pending = toast.loading("Retrait de l'adresse…", site.url);
    remove.mutate(site.id, {
      onSuccess: () => toast.update(pending, { tone: "success", message: "Adresse retirée", description: site.url }),
      onError: (error) => toast.update(pending, { tone: "error", message: "L'adresse n'a pas été retirée", description: errorMessage(error) }),
    });
  }

  return del;
}

function SiteRow(props: { site: Site; selectable: boolean; selected: boolean; onSelect: (on: boolean) => void }) {
  const { site } = props;
  const { admin } = useOrg();
  const { rename, remove } = useSiteMutations();
  const del = useSiteRemoval(site, remove);
  const toast = useToast();
  const [editing, setEditing] = useState(false);
  const [label, setLabel] = useState(site.label);

  function save(event: FormEvent) {
    event.preventDefault();
    rename.mutate(
      { id: site.id, label },
      {
        onSuccess: () => {
          setEditing(false);
          toast.show({ tone: "success", message: label.trim() ? `Libellé enregistré : ${label.trim()}` : "Libellé retiré", duration: 2500 });
        },
        onError: (error) => toast.show({ tone: "error", message: "Le libellé n'a pas été enregistré", description: errorMessage(error) }),
      },
    );
  }

  return (
    <li className="flex flex-wrap items-center gap-x-4 gap-y-2 px-5 py-3 text-sm">
      {props.selectable ? (
        <input type="checkbox" aria-label={`Lire ${site.url}`} className="h-4 w-4 accent-ink" checked={props.selected} onChange={(event) => props.onSelect(event.target.checked)} />
      ) : null}
      <div className="min-w-0 flex-1">
        {editing ? (
          <form className="flex items-center gap-2" onSubmit={save}>
            <Input aria-label="Libellé" autoFocus maxLength={60} placeholder="FR, US, INT…" value={label} onChange={(event) => setLabel(event.target.value)} className="max-w-40" />
            <Button size="sm" type="submit" variant="primary" loading={rename.isPending}>OK</Button>
            <Button size="sm" variant="ghost" onClick={() => { setLabel(site.label); setEditing(false); }}>Annuler</Button>
          </form>
        ) : (
          <p className="truncate font-medium">{site.label || hostOf(site.url)}</p>
        )}
        <p className="truncate text-xs text-muted">
          <a href={site.url} target="_blank" rel="noreferrer noopener" className="hover:underline">{site.url}</a>
          {" · "}
          {site.last_crawled_at ? `lue le ${formatDateTime(site.last_crawled_at)} · ${plural(site.images, "image")}` : "jamais lue"}
        </p>
      </div>
      {admin && !editing ? (
        <div className="flex gap-1">
          <Button size="sm" variant="ghost" onClick={() => setEditing(true)}>Libellé</Button>
          <Button size="sm" variant="ghost" loading={remove.isPending} onClick={() => void del()}>Retirer</Button>
        </div>
      ) : null}
    </li>
  );
}

function AddSite() {
  const { add } = useSiteMutations();
  const toast = useToast();
  const [url, setUrl] = useState("");
  const [label, setLabel] = useState("");

  function submit(event: FormEvent) {
    event.preventDefault();
    add.mutate(
      { url, label },
      {
        onSuccess: ({ site }) => {
          setUrl("");
          setLabel("");
          toast.show({ tone: "success", message: "Adresse ajoutée", description: `${site.url} peut être lue dès maintenant.` });
        },
        onError: (error) => toast.show({ tone: "error", message: "L'adresse n'a pas été ajoutée", description: errorMessage(error) }),
      },
    );
  }

  return (
    <form className="grid gap-3 border-t border-line px-5 py-4 sm:grid-cols-[minmax(0,1fr)_140px_auto] sm:items-end" onSubmit={submit}>
      <div>
        <FieldLabel htmlFor="site-url">Nouvelle adresse</FieldLabel>
        <Input id="site-url" type="url" required placeholder="https://www.exemple.com/fr" value={url} onChange={(event) => setUrl(event.target.value)} />
      </div>
      <div>
        <FieldLabel htmlFor="site-label" hint="facultatif">Libellé</FieldLabel>
        <Input id="site-label" maxLength={60} placeholder="FR, US, INT…" value={label} onChange={(event) => setLabel(event.target.value)} />
      </div>
      <Button type="submit" loading={add.isPending} disabled={!url.trim()}>Ajouter</Button>
    </form>
  );
}
