import { useState, type FormEvent } from "react";
import { useToast, type Toaster } from "../../components/feedback";
import { Button, Card, Checkbox, FieldLabel, Input } from "../../components/ui";
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

/** Starts a read of the ticked addresses, or a comparison of what was already read. */
export function RunCard(props: { list: Site[]; selected: Site[] }) {
  const { admin } = useOrg();
  const { list, selected } = props;
  const overview = useOverview();
  const jobs = useJobs();
  const start = useStartJob();
  const toast = useToast();
  const [maxPages, setMaxPages] = useState<number | "">("");
  const [fresh, setFresh] = useState(false);
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
  return (
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
          <MaxPages value={maxPages} onChange={setMaxPages} defaults={defaults} />
          <Button type="submit" variant="primary" loading={start.isPending && start.variables?.kind === "crawl"} disabled={busy || start.isPending || noLibrary || !selected.length}>
            {busy ? "Une tâche est en cours" : list.length === 1 ? "Lancer la lecture" : `Lire ${plural(selected.length, "adresse")}`}
          </Button>
          <Button onClick={compareNow} loading={start.isPending && start.variables?.kind === "match"} disabled={busy || start.isPending || !overview.data?.stats.site_images}>
            Comparer sans relire
          </Button>
          {estimate.data ? <EstimateLine estimate={estimate.data} sites={selected} thenMatch={thenMatch} /> : null}
          {noLibrary ? <p className="text-xs text-muted">Ajoutez d'abord des visuels à la bibliothèque.</p> : null}
        </div>
      </form>
    </Card>
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
  if (!known.length) return <p className="text-xs text-muted">Durée inconnue : pas encore assez de lectures pour la prévoir. Elle le sera après la première.</p>;
  return (
    <p className="text-xs text-muted">
      Durée estimée : {unknown.length ? "au moins " : "environ "}{approxDuration(seconds)}, d'après les dernières lectures.
      {unknown.length ? ` Sans historique : ${unknown.map((site) => site.label || hostOf(site.url)).join(", ")}.` : ""}
      {props.thenMatch && estimate.compare_seconds === null ? " La comparaison n'est pas comptée (jamais mesurée)." : ""}
    </p>
  );
}
