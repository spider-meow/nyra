import { useState, type FormEvent } from "react";
import { useConfirm, useToast } from "../components/feedback";
import { Button, Card, Checkbox, EmptyState, FieldLabel, Input, PageHeader, Skeleton, cx } from "../components/ui";
import { errorMessage } from "../lib/api";
import { formatDateTime, hostOf, plural } from "../lib/format";
import { useOrg } from "../lib/org";
import { useJobs, useOverview, useScans, useSiteMutations, useSites, useStartJob } from "../lib/queries";
import type { Scan, Site } from "../types";

const scanStatus: Record<Scan["status"], { label: string; tone: string }> = {
  running: { label: "En cours", tone: "text-focus" },
  done: { label: "Terminée", tone: "text-ok" },
  error: { label: "Échec", tone: "text-expired" },
  cancelled: { label: "Arrêtée", tone: "text-muted" },
};

function siteName(site: { url: string; label: string }): string {
  return site.label ? `${site.label} · ${hostOf(site.url)}` : hostOf(site.url);
}

export function Scans() {
  const { admin, brand } = useOrg();
  const overview = useOverview();
  const sites = useSites();
  const scans = useScans();
  const jobs = useJobs();
  const start = useStartJob();
  const toast = useToast();
  // Every address is read unless unticked.
  const [skipped, setSkipped] = useState<Set<string>>(new Set());
  const [maxPages, setMaxPages] = useState<number | "">("");
  const [fresh, setFresh] = useState(false);
  const [thenMatch, setThenMatch] = useState(true);

  const defaults = overview.data?.defaults;
  const list = sites.data?.sites ?? [];
  const selected = list.filter((site) => !skipped.has(site.id));
  const busy = (jobs.data?.active ?? []).some((job) => job.kind === "crawl" || job.kind === "match");
  const noLibrary = overview.data?.stats.reference_images === 0;

  function toggle(id: string, on: boolean) {
    const next = new Set(skipped);
    if (on) next.delete(id);
    else next.add(id);
    setSkipped(next);
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    start.mutate(
      {
        kind: "crawl",
        body: { site_ids: selected.map((site) => site.id), max_pages: maxPages || defaults?.max_pages, fresh, then_match: thenMatch },
      },
      {
        onSuccess: () =>
          toast.show({
            tone: "success",
            message: selected.length > 1 ? `Lecture de ${selected.length} adresses programmée` : "Lecture programmée",
            description: "Elle démarre dès qu'un worker est libre ; son avancement s'affiche ici même, et vous serez prévenu à la fin.",
          }),
        onError: (error) => toast.show({ tone: "error", message: "La lecture n'a pas été lancée", description: errorMessage(error) }),
      },
    );
  }

  function compareNow() {
    start.mutate(
      { kind: "match" },
      {
        onSuccess: () =>
          toast.show({ tone: "success", message: "Comparaison programmée", description: "Les images déjà lues sont comparées à la bibliothèque, sans relire les sites." }),
        onError: (error) => toast.show({ tone: "error", message: "La comparaison n'a pas été lancée", description: errorMessage(error) }),
      },
    );
  }

  return (
    <>
      <PageHeader
        title="Sites et lectures"
        description={`Les adresses de ${brand.name}, un site par marché par exemple. Nyra les parcourt comme un visiteur (sitemaps puis liens internes, bandeaux cookies et contrôle d'âge compris), récupère chaque image et la compare à la bibliothèque de la marque.`}
      />

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
              <SiteRow key={site.id} site={site} selectable={admin && list.length > 1} selected={!skipped.has(site.id)} onSelect={(on) => toggle(site.id, on)} />
            ))}
          </ul>
        ) : (
          <p className="px-5 pt-2 pb-5 text-sm text-muted">
            {admin ? "Aucune adresse pour l'instant. Ajoutez la première ci-dessous." : "Aucune adresse pour l'instant. Un administrateur doit en ajouter une."}
          </p>
        )}
        {admin ? <AddSite /> : null}
      </Card>

      {admin && list.length ? (
        <Card className="mb-6">
          <form className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_240px]" onSubmit={submit}>
            <div className="grid content-start gap-2">
              <h2 className="mb-1 font-semibold">Lire</h2>
              <Checkbox checked={thenMatch} onChange={setThenMatch} label="Comparer à la bibliothèque dès la fin de la lecture" />
              <Checkbox
                checked={fresh}
                onChange={setFresh}
                label="Relire aussi les pages déjà lues"
                hint="Sinon, seules les pages nouvelles sont lues : plus rapide, mais une image changée sur une page connue passe inaperçue."
              />
            </div>
            <div className="grid content-start gap-4">
              <div>
                <FieldLabel htmlFor="max-pages" hint={defaults ? `max. ${defaults.max_pages_limit}` : undefined}>Pages au maximum, par adresse</FieldLabel>
                <Input
                  id="max-pages"
                  type="number"
                  min={1}
                  max={defaults?.max_pages_limit}
                  placeholder={String(defaults?.max_pages ?? 300)}
                  value={maxPages}
                  onChange={(event) => setMaxPages(event.target.value ? Number(event.target.value) : "")}
                />
                <p className="mt-1 text-xs text-muted">Un plafond : un site plus petit est lu en entier, et la lecture s'arrête d'elle-même.</p>
              </div>
              <Button type="submit" variant="primary" loading={start.isPending && start.variables?.kind === "crawl"} disabled={busy || start.isPending || noLibrary || !selected.length}>
                {busy ? "Une tâche est en cours" : list.length === 1 ? "Lancer la lecture" : `Lire ${plural(selected.length, "adresse")}`}
              </Button>
              <Button onClick={compareNow} loading={start.isPending && start.variables?.kind === "match"} disabled={busy || start.isPending || !overview.data?.stats.site_images}>
                Comparer sans relire
              </Button>
              {noLibrary ? <p className="text-xs text-muted">Ajoutez d'abord des visuels à la bibliothèque.</p> : null}
            </div>
          </form>
        </Card>
      ) : null}

      <h2 className="mb-3 font-semibold">Historique</h2>
      {scans.isLoading ? (
        <Skeleton className="h-40" />
      ) : scans.error ? (
        <EmptyState title="Historique indisponible" body={errorMessage(scans.error)} />
      ) : !scans.data?.scans.length ? (
        <EmptyState title="Aucune lecture pour l'instant" body={admin ? "Ajoutez une adresse puis lancez la lecture." : "Un administrateur doit lancer la première lecture."} />
      ) : (
        <Card padded={false} className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-sm">
            <thead>
              <tr className="border-b border-line text-left text-xs text-muted">
                <th className="px-4 py-2.5 font-medium">Site</th>
                <th className="px-2 py-2.5 font-medium">Début</th>
                <th className="px-2 py-2.5 font-medium">État</th>
                <th className="px-2 py-2.5 text-right font-medium">Pages</th>
                <th className="px-2 py-2.5 text-right font-medium">Nouvelles images</th>
                <th className="px-4 py-2.5 font-medium">Incidents</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {scans.data.scans.map((scan) => (
                <tr key={scan.id} className="align-top">
                  <td className="px-4 py-2.5 font-medium">{siteName({ url: scan.site_url, label: scan.site_label })}</td>
                  <td className="px-2 py-2.5 text-muted">{formatDateTime(scan.started_at)}</td>
                  <td className={cx("px-2 py-2.5", scanStatus[scan.status].tone)}>{scanStatus[scan.status].label}</td>
                  <td className="px-2 py-2.5 text-right tabular">{scan.pages_visited}</td>
                  <td className="px-2 py-2.5 text-right tabular">{scan.images_new}</td>
                  <td className="px-4 py-2.5 text-[13px] text-muted">
                    {scan.error_count || scan.blocked_by_robots ? (
                      <details>
                        <summary className="cursor-pointer">
                          {[scan.error_count ? plural(scan.error_count, "page en erreur", "pages en erreur") : "", scan.blocked_by_robots ? `${scan.blocked_by_robots} bloquée(s) par robots.txt` : ""].filter(Boolean).join(" · ")}
                        </summary>
                        <ul className="mt-1 grid gap-1">
                          {scan.errors.map((line) => <li key={line} className="break-all">{line}</li>)}
                        </ul>
                      </details>
                    ) : scan.status === "done" && scan.pages_visited > 0 && scan.images_found === 0 ? (
                      "Aucune image assez grande trouvée"
                    ) : (
                      "·"
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </>
  );
}

function SiteRow(props: { site: Site; selectable: boolean; selected: boolean; onSelect: (on: boolean) => void }) {
  const { site } = props;
  const { admin } = useOrg();
  const { rename, remove } = useSiteMutations();
  const confirm = useConfirm();
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
