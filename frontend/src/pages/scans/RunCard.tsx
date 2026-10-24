import { useState, type FormEvent } from "react";
import { useToast, type Toaster } from "../../components/feedback";
import { OrgHoldNotice, holdProps, useOrgHold } from "../../components/OrgHold";
import { Button, Checkbox, FieldLabel, Input } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import { approxDuration, hostOf, plural } from "../../lib/format";
import { useOrg } from "../../lib/org";
import { useCrawlEstimate, useJobs, useOverview, useStartJob } from "../../lib/queries";
import type { CrawlEstimate, Overview, Site } from "../../types";

function crawlOutcome(toast: Toaster, count: number) {
  return {
    onSuccess: () =>
      toast.show({
        tone: "success",
        message: count > 1 ? `Lecture de ${count} adresses programmée` : "Lecture programmée",
        description: "Elle démarre dès qu'un worker est libre ; son avancement s'affiche ici même, et vous serez prévenu à la fin.",
      }),
    onError: (error: Error) => toast.show({ tone: "error", message: "La lecture n'a pas été lancée", description: errorMessage(error) }),
  };
}

function compareOutcome(toast: Toaster) {
  return {
    onSuccess: () =>
      toast.show({ tone: "success", message: "Comparaison programmée", description: "Les images déjà lues sont comparées à la bibliothèque, sans relire les sites." }),
    onError: (error: Error) => toast.show({ tone: "error", message: "La comparaison n'a pas été lancée", description: errorMessage(error) }),
  };
}

/** The page's main action, on top: read the ticked addresses, or compare what was already read. Options stay folded. */
export function RunCard(props: { list: Site[]; selected: Site[] }) {
  const { admin } = useOrg();
  const { list, selected } = props;
  const overview = useOverview();
  const jobs = useJobs();
  const hold = useOrgHold();
  const start = useStartJob();
  const toast = useToast();
  const [maxPages, setMaxPages] = useState<number | "">("");
  const [newOnly, setNewOnly] = useState(false);
  const fresh = !newOnly; // the job's own word: `fresh` reads every page again, otherwise only the new ones
  const [thenMatch, setThenMatch] = useState(true);

  const defaults = overview.data?.defaults;
  const estimate = useCrawlEstimate({ siteIds: selected.map((site) => site.id), maxPages: maxPages || undefined, fresh, thenMatch });
  const busy = (jobs.data?.active ?? []).some((job) => job.kind === "crawl" || job.kind === "match");
  const noLibrary = overview.data?.stats.reference_images === 0;

  function submit(event: FormEvent) {
    event.preventDefault();
    const body = { site_ids: selected.map((site) => site.id), max_pages: maxPages || defaults?.max_pages, fresh, then_match: thenMatch };
    start.mutate({ kind: "crawl", body }, crawlOutcome(toast, selected.length));
  }

  function compareNow() {
    start.mutate({ kind: "match" }, compareOutcome(toast));
  }

  if (!admin || !list.length) return null;
  const label = busy
    ? "Une tâche est en cours"
    : !selected.length
      ? "Cochez une adresse"
      : list.length === 1
        ? "Lancer la lecture"
        : `Lire ${plural(selected.length, "adresse")}`;
  return (
    <form className="mb-6 rounded-xl border border-line bg-paper px-5 py-4" onSubmit={submit}>
      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit" variant="primary" loading={start.isPending && start.variables?.kind === "crawl"} disabled={busy || Boolean(hold) || start.isPending || noLibrary || !selected.length} {...holdProps(hold)}>
          {label}
        </Button>
        <Button onClick={compareNow} loading={start.isPending && start.variables?.kind === "match"} disabled={busy || Boolean(hold) || start.isPending || !overview.data?.stats.site_images} {...holdProps(hold)}>
          Comparer sans relire
        </Button>
        {estimate.data && selected.length ? <EstimateLine estimate={estimate.data} sites={selected} thenMatch={thenMatch} /> : null}
      </div>
      {hold ? <div className="mt-2"><OrgHoldNotice hold={hold} /></div> : null}
      {noLibrary ? <p className="mt-2 text-xs text-muted">Ajoutez d'abord des visuels à la bibliothèque.</p> : null}
      <details className="mt-3">
        <summary className="w-fit cursor-pointer text-sm text-muted hover:text-ink">Options de lecture</summary>
        <div className="mt-3 grid gap-5 sm:grid-cols-2">
          <div className="grid content-start gap-2">
            <Checkbox checked={thenMatch} onChange={setThenMatch} label="Comparer à la bibliothèque dès la fin de la lecture" />
            <Checkbox
              checked={newOnly}
              onChange={setNewOnly}
              label="Lire seulement les pages nouvelles"
              hint="Plus rapide, mais une image changée sur une page connue passe inaperçue. Sinon, toutes les pages sont relues."
            />
          </div>
          <MaxPages value={maxPages} onChange={setMaxPages} defaults={defaults} />
        </div>
      </details>
    </form>
  );
}

function MaxPages(props: { value: number | ""; onChange: (value: number | "") => void; defaults: Overview["defaults"] | undefined }) {
  const { defaults } = props;
  return (
    <div>
      <FieldLabel htmlFor="max-pages" hint={defaults ? `max. ${defaults.max_pages_limit}` : undefined}>Pages au maximum, par adresse</FieldLabel>
      <Input
        id="max-pages"
        type="number"
        min={1}
        max={defaults?.max_pages_limit}
        placeholder={String(defaults?.max_pages ?? 300)}
        value={props.value}
        onChange={(event) => props.onChange(event.target.value ? Number(event.target.value) : "")}
      />
      <p className="mt-1 text-xs text-muted">Un plafond : un site plus petit est lu en entier, et la lecture s'arrête d'elle-même.</p>
    </div>
  );
}

/** Roughly how long the read will take: the addresses with history add up, the others are named. */
function EstimateLine(props: { estimate: CrawlEstimate; sites: Site[]; thenMatch: boolean }) {
  const { estimate, sites } = props;
  const known = sites.filter((site) => estimate.sites[site.id]);
  const unknown = sites.filter((site) => !estimate.sites[site.id]);
  const seconds = known.reduce((sum, site) => sum + (estimate.sites[site.id]?.seconds ?? 0), 0) + (estimate.compare_seconds ?? 0);
  if (!known.length) return <p className="min-w-48 flex-1 text-xs text-muted">Durée inconnue : pas encore assez de lectures pour la prévoir. Elle le sera après la première.</p>;
  return (
    <p className="min-w-48 flex-1 text-xs text-muted">
      Durée estimée : {unknown.length ? "au moins " : "environ "}{approxDuration(seconds)}, d'après les dernières lectures.
      {unknown.length ? ` Sans historique : ${unknown.map((site) => site.label || hostOf(site.url)).join(", ")}.` : ""}
      {props.thenMatch && estimate.compare_seconds === null ? " La comparaison n'est pas comptée (jamais mesurée)." : ""}
    </p>
  );
}
