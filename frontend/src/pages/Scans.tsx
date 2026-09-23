import { useState, type FormEvent } from "react";
import { useToast } from "../components/feedback";
import { Button, Card, Checkbox, EmptyState, FieldLabel, Input, PageHeader, Skeleton, cx } from "../components/ui";
import { errorMessage } from "../lib/api";
import { formatDateTime, hostOf, plural } from "../lib/format";
import { useOrg } from "../lib/org";
import { useJobs, useOverview, useScans, useStartJob } from "../lib/queries";
import type { Scan } from "../types";

const scanStatus: Record<Scan["status"], { label: string; tone: string }> = {
  running: { label: "En cours", tone: "text-focus" },
  done: { label: "Terminée", tone: "text-ok" },
  error: { label: "Échec", tone: "text-expired" },
  cancelled: { label: "Arrêtée", tone: "text-muted" },
};

export function Scans() {
  const { admin } = useOrg();
  const overview = useOverview();
  const scans = useScans();
  const jobs = useJobs();
  const start = useStartJob();
  const toast = useToast();
  const [site, setSite] = useState("");
  const [maxPages, setMaxPages] = useState<number | "">("");
  const [fresh, setFresh] = useState(false);
  const [thenMatch, setThenMatch] = useState(true);

  const defaults = overview.data?.defaults;
  const knownSites = overview.data?.sites ?? [];
  const busy = (jobs.data?.active ?? []).some((job) => job.kind === "crawl" || job.kind === "match");
  const noLibrary = overview.data?.stats.reference_images === 0;
  const siteValue = site || knownSites[0] || "";

  function submit(event: FormEvent) {
    event.preventDefault();
    start.mutate(
      { kind: "crawl", body: { site: siteValue, max_pages: maxPages || defaults?.max_pages, fresh, then_match: thenMatch } },
      {
        onSuccess: () => toast("Lecture programmée. Elle démarre dès qu'un worker est libre."),
        onError: (error) => toast(errorMessage(error), "error"),
      },
    );
  }

  function compareNow() {
    start.mutate({ kind: "match" }, { onSuccess: () => toast("Comparaison programmée."), onError: (error) => toast(errorMessage(error), "error") });
  }

  return (
    <>
      <PageHeader
        title="Lectures du site"
        description="Nyra parcourt le site comme un visiteur (sitemaps puis liens internes, bandeaux cookies et contrôle d'âge compris), récupère chaque image et la compare à la bibliothèque."
      />

      {admin ? (
        <Card className="mb-6">
          <form className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_240px]" onSubmit={submit}>
            <div className="grid content-start gap-4">
              <div>
                <FieldLabel htmlFor="site">Adresse du site</FieldLabel>
                <Input
                  id="site"
                  type="url"
                  required
                  placeholder="https://www.exemple.com"
                  list="known-sites"
                  value={siteValue}
                  onChange={(event) => setSite(event.target.value)}
                />
                <datalist id="known-sites">
                  {knownSites.map((url) => <option key={url} value={url} />)}
                </datalist>
              </div>
              <div className="grid gap-2">
                <Checkbox checked={thenMatch} onChange={setThenMatch} label="Comparer à la bibliothèque dès la fin de la lecture" />
                <Checkbox
                  checked={fresh}
                  onChange={setFresh}
                  label="Relire aussi les pages déjà lues"
                  hint="Sinon, seules les pages nouvelles sont lues : plus rapide, mais une image changée sur une page connue passe inaperçue."
                />
              </div>
            </div>
            <div className="grid content-start gap-4">
              <div>
                <FieldLabel htmlFor="max-pages" hint={defaults ? `max. ${defaults.max_pages_limit}` : undefined}>Pages au maximum</FieldLabel>
                <Input
                  id="max-pages"
                  type="number"
                  min={1}
                  max={defaults?.max_pages_limit}
                  placeholder={String(defaults?.max_pages ?? 300)}
                  value={maxPages}
                  onChange={(event) => setMaxPages(event.target.value ? Number(event.target.value) : "")}
                />
              </div>
              <Button type="submit" variant="primary" disabled={busy || start.isPending || noLibrary}>
                {busy ? "Une tâche est en cours" : "Lancer la lecture"}
              </Button>
              <Button onClick={compareNow} disabled={busy || start.isPending || !overview.data?.stats.site_images}>
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
        <EmptyState title="Aucune lecture pour l'instant" body={admin ? "Indiquez l'adresse du site ci-dessus." : "Un administrateur doit lancer la première lecture."} />
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
                  <td className="px-4 py-2.5 font-medium">{hostOf(scan.site_url)}</td>
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
                      "—"
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
