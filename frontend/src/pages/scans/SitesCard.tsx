import { useEffect, useRef, useState, type FormEvent } from "react";
import { useConfirm, useToast } from "../../components/feedback";
import { Icon } from "../../components/icons";
import { Button, Card, cx, FieldLabel, Input, Skeleton } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import { formatDateTime, hostOf, plural } from "../../lib/format";
import { useOrg } from "../../lib/org";
import { useJobs, useScans, useSiteMutations, type useSites } from "../../lib/queries";
import type { Site } from "../../types";

/** The brand's addresses, each one tickable for the next read, and the form that adds one. */
export function SitesCard(props: {
  sites: ReturnType<typeof useSites>;
  skipped: Set<string>;
  onToggle: (id: string, on: boolean) => void;
  onToggleAll: (on: boolean) => void;
}) {
  const { admin } = useOrg();
  const { sites } = props;
  const state = useSiteStates();
  const [adding, setAdding] = useState(false);
  const list = sites.data?.sites ?? [];
  const selectable = admin && list.length > 1;
  const ticked = list.length - props.skipped.size;
  // With no address at all, the form is the whole page: no button to find first.
  const formOpen = admin && (adding || (!sites.isLoading && !sites.error && list.length === 0));
  return (
    <Card className="mb-6" padded={false} tone="quiet">
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 px-5 py-3">
        <h2 className="font-semibold">Adresses</h2>
        <div className="flex items-center gap-4 text-sm">
          {selectable ? (
            <>
              <span className="text-muted">{ticked} sur {list.length} à lire</span>
              <button type="button" className="text-ink-soft hover:text-ink hover:underline" onClick={() => props.onToggleAll(ticked < list.length)}>
                {ticked < list.length ? "Tout cocher" : "Tout décocher"}
              </button>
            </>
          ) : null}
          {admin && !formOpen ? <Button size="sm" onClick={() => setAdding(true)}>Ajouter une adresse</Button> : null}
        </div>
      </div>
      {sites.isLoading ? (
        <div className="border-t border-line p-5"><Skeleton className="h-16" /></div>
      ) : sites.error ? (
        <p className="border-t border-line px-5 py-4 text-sm text-expired">{errorMessage(sites.error)}</p>
      ) : list.length ? (
        <ul className="divide-y divide-line border-t border-line">
          {list.map((site) => (
            <SiteRow key={site.id} site={site} state={state(site)} selectable={selectable} selected={!props.skipped.has(site.id)} onSelect={(on) => props.onToggle(site.id, on)} />
          ))}
        </ul>
      ) : (
        <p className="border-t border-line px-5 py-4 text-sm text-muted">
          {admin ? "Aucune adresse pour l'instant. Ajoutez la première ci-dessous." : "Aucune adresse pour l'instant. Un administrateur doit en ajouter une."}
        </p>
      )}
      {formOpen ? <AddSite onDone={list.length ? () => setAdding(false) : undefined} /> : null}
    </Card>
  );
}

type SiteState = "reading" | "queued" | "waiting" | "error" | "never" | "ok";

const STATE_PILL: Record<SiteState, { label: string; tone: string; note?: string }> = {
  reading: { label: "En cours", tone: "bg-soon-soft text-soon" },
  queued: { label: "En attente", tone: "bg-unknown-soft text-unknown" },
  waiting: { label: "En attente", tone: "bg-unknown-soft text-unknown", note: "Démarre dès qu'une place se libère sur le serveur." },
  error: { label: "Dernière lecture en échec", tone: "bg-expired-soft text-expired" },
  never: { label: "Jamais lue", tone: "bg-unknown-soft text-unknown" },
  ok: { label: "Lue", tone: "bg-ok-soft text-ok" },
};

/** Where each address stands: being read, waiting, failed last time, never read, or read. */
function useSiteStates(): (site: Site) => SiteState {
  const jobs = useJobs();
  const scans = useScans();
  const crawls = (jobs.data?.active ?? []).filter((job) => job.kind === "crawl");
  const newest = new Map<string, { at: string; status: string }>();
  for (const scan of scans.data?.scans ?? []) {
    if ((newest.get(scan.site_id)?.at ?? "") < scan.started_at) newest.set(scan.site_id, { at: scan.started_at, status: scan.status });
  }
  return (site) => {
    // A read started without addresses covers them all.
    const job = crawls.find((item) => {
      const ids = (item.params.site_ids as string[] | undefined) ?? [];
      return ids.length === 0 || ids.includes(site.id);
    });
    if (job) return job.status === "running" ? "reading" : job.waiting_for_capacity ? "waiting" : "queued";
    if (newest.get(site.id)?.status === "error") return "error";
    return site.last_crawled_at ? "ok" : "never";
  };
}

/** The row's actions behind one button: rare ones stay out of the way. */
function RowMenu(props: { onRename: () => void; onRemove: () => void; busy: boolean }) {
  const ref = useRef<HTMLDetailsElement>(null);
  const close = () => ref.current?.removeAttribute("open");
  useEffect(() => {
    const away = (event: MouseEvent) => {
      if (!ref.current?.contains(event.target as Node)) ref.current?.removeAttribute("open");
    };
    document.addEventListener("click", away);
    return () => document.removeEventListener("click", away);
  }, []);
  const item = "block w-full px-3 py-2 text-left text-sm hover:bg-sunk";
  return (
    <details ref={ref} className="relative" onKeyDown={(event) => event.key === "Escape" && close()}>
      <summary
        aria-label="Actions de l'adresse"
        className="grid size-8 cursor-pointer list-none place-items-center rounded-md text-muted hover:bg-sunk hover:text-ink [&::-webkit-details-marker]:hidden"
      >
        <Icon name="more" size={18} strokeWidth={2.4} />
      </summary>
      <div className="absolute right-0 z-10 mt-1 w-48 overflow-hidden rounded-lg border border-line-strong bg-paper py-1 shadow-lg">
        <button type="button" className={item} onClick={() => { close(); props.onRename(); }}>Modifier le libellé</button>
        <button type="button" disabled={props.busy} className={cx(item, "text-expired")} onClick={() => { close(); props.onRemove(); }}>Retirer l'adresse</button>
      </div>
    </details>
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

function SiteRow(props: { site: Site; state: SiteState; selectable: boolean; selected: boolean; onSelect: (on: boolean) => void }) {
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
        </p>
        {STATE_PILL[props.state].note ? <p className="text-xs text-muted">{STATE_PILL[props.state].note}</p> : null}
      </div>
      {site.last_crawled_at ? (
        <p className="hidden text-right text-xs text-muted sm:block">
          {plural(site.images, "image")}
          <span className="block">{formatDateTime(site.last_crawled_at)}</span>
        </p>
      ) : null}
      <span className={cx("rounded-md px-2 py-px text-xs font-medium whitespace-nowrap", STATE_PILL[props.state].tone)}>{STATE_PILL[props.state].label}</span>
      {admin && !editing ? <RowMenu busy={remove.isPending} onRename={() => setEditing(true)} onRemove={() => void del()} /> : null}
    </li>
  );
}

function AddSite(props: { onDone?: () => void }) {
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
          props.onDone?.();
          toast.show({ tone: "success", message: "Adresse ajoutée", description: `${site.url} peut être lue dès maintenant.` });
        },
        onError: (error) => toast.show({ tone: "error", message: "L'adresse n'a pas été ajoutée", description: errorMessage(error) }),
      },
    );
  }

  return (
    <form className="grid gap-3 border-t border-line bg-sunk px-5 py-4 sm:grid-cols-[minmax(0,1fr)_140px_auto] sm:items-end" onSubmit={submit}>
      <div>
        <FieldLabel htmlFor="site-url">Nouvelle adresse</FieldLabel>
        <Input id="site-url" autoFocus type="url" required placeholder="https://www.exemple.com/fr" value={url} onChange={(event) => setUrl(event.target.value)} />
      </div>
      <div>
        <FieldLabel htmlFor="site-label" hint="facultatif">Libellé</FieldLabel>
        <Input id="site-label" maxLength={60} placeholder="FR, US, INT…" value={label} onChange={(event) => setLabel(event.target.value)} />
      </div>
      <div className="flex gap-2">
        <Button type="submit" variant="primary" loading={add.isPending} disabled={!url.trim()}>Ajouter</Button>
        {props.onDone ? <Button variant="ghost" onClick={props.onDone}>Annuler</Button> : null}
      </div>
    </form>
  );
}
