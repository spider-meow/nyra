import { LoadError, Refreshing } from "../components/busy";
import { useState } from "react";
import { useToast } from "../components/feedback";
import { OrgHoldNotice, holdProps, useOrgHold } from "../components/OrgHold";
import { Bar, Button, Card, Checkbox, EmptyState, PageHeader, Skeleton, StatusBadge } from "../components/ui";
import { errorMessage } from "../lib/api";
import { formatDateTime, hostOf, plural } from "../lib/format";
import { useOrg } from "../lib/org";
import { useJobs, useReports, useSites, useStartJob } from "../lib/queries";
import type { Report, ReportContent } from "../types";

/** The report covers every deadline: its sections say what is urgent (AXE-865). The server still accepts a narrower `within_days`. */
const ALL_DEADLINES = 3650;

const CONTENTS: [ReportContent, string, string][] = [
  ["expired", "Expirés encore en ligne", "Droits échus, image vue à la dernière lecture"],
  ["soon", "Expirent sous 90 jours", ""],
  ["check", "À vérifier", "Correspondances incertaines"],
  ["unreferenced", "Images non référencées", "Celles de « Images en ligne › À qualifier », toutes, sur leur page"],
];
const CONTENT_LABEL: Record<ReportContent, string> = { expired: "Expirés", soon: "Sous 90 j", check: "À vérifier", unreferenced: "Non référencées" };

export function Reports() {
  const { admin } = useOrg();
  const jobs = useJobs();
  const hold = useOrgHold();
  const start = useStartJob();
  const toast = useToast();
  const sites = useSites();
  const [skipped, setSkipped] = useState<Set<string>>(new Set());
  const [include, setInclude] = useState<Set<ReportContent>>(new Set(CONTENTS.map(([name]) => name)));
  const generating = (jobs.data?.active ?? []).some((job) => job.kind === "report");
  const versions = sites.data?.sites ?? [];
  const chosen = versions.filter((site) => !skipped.has(site.id));
  const empty = !include.size || (versions.length > 0 && !chosen.length);

  function generate() {
    // Every version ticked: no list, so a version added later is in the next report of « everything ».
    const site_ids = chosen.length < versions.length ? chosen.map((site) => site.id) : [];
    start.mutate(
      { kind: "report", body: { within_days: ALL_DEADLINES, site_ids, include: CONTENTS.map(([name]) => name).filter((name) => include.has(name)) } },
      {
        onSuccess: () =>
          toast.show({ tone: "success", message: "Rapport en préparation", description: "Il apparaîtra dans la liste ci-dessous dans quelques instants, avec son PDF." }),
        onError: (error) => toast.show({ tone: "error", message: "Le rapport n'a pas été lancé", description: errorMessage(error) }),
      },
    );
  }
  const toggle = <T,>(set: Set<T>, item: T, on: boolean) => { const next = new Set(set); if (on) next.delete(item); else next.add(item); return next; };
  const generateProps = { onClick: generate, loading: generating || start.isPending, disabled: Boolean(hold) || empty, ...holdProps(hold) };

  return (
    <>
      <PageHeader title="Rapports" />

      {admin ? (
        <Card className="mb-6">
          <div className="grid gap-6 sm:grid-cols-2">
            {versions.length > 1 ? (
              <fieldset className="grid content-start gap-1.5">
                <legend className="mb-2 font-semibold">Versions du site</legend>
                {versions.map((site) => (
                  <Checkbox key={site.id} checked={!skipped.has(site.id)} onChange={(on) => setSkipped(toggle(skipped, site.id, on))} label={site.label || hostOf(site.url)} hint={site.url} />
                ))}
              </fieldset>
            ) : null}
            <fieldset className="grid content-start gap-1.5">
              <legend className="mb-2 font-semibold">Contenu</legend>
              {CONTENTS.map(([name, label, hint]) => (
                <Checkbox key={name} checked={include.has(name)} onChange={(on) => setInclude(toggle(include, name, !on))} label={label} hint={hint || undefined} />
              ))}
            </fieldset>
          </div>
          <p className="mt-4 text-[13px] text-muted">Le rapport est classé par version du site, puis par rubrique (/collections, /news…) dans l'ordre alphabétique ; la première page liste ce qui est à traiter d'abord. Il se télécharge en PDF.</p>
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <Button variant="primary" {...generateProps}>{generating ? "Préparation…" : "Générer le rapport"}</Button>
            {empty ? <span className="text-[13px] text-muted">Cochez au moins {include.size ? "une version" : "un contenu"}.</span> : null}
          </div>
          {hold ? <div className="mt-3"><OrgHoldNotice hold={hold} /></div> : null}
        </Card>
      ) : null}

      <ReportList generate={admin ? generateProps : null} />
    </>
  );
}

/** The figures of a report, in the words of the dashboard and the monthly e-mail. */
function figures(report: Report): string[] {
  const { expired_online: expired, urgent_online: urgent, to_verify: toVerify } = report.stats;
  const parts: string[] = [];
  if (typeof expired === "number") parts.push(`${expired} ${expired === 1 ? "expiré" : "expirés"} en ligne`);
  if (typeof urgent === "number") parts.push(`${urgent} sous 30 jours`);
  if (typeof toVerify === "number") parts.push(`${toVerify} à vérifier`);
  const { unreferenced, pages } = report.stats;
  if (typeof unreferenced === "number" && report.stats.scope?.include.includes("unreferenced")) parts.push(`${unreferenced} non référencées`);
  if (typeof pages === "number") parts.push(plural(pages, "page"));
  return parts;
}

function ReportList(props: { generate: { onClick: () => void; loading: boolean; disabled: boolean } | null }) {
  const { admin } = useOrg();
  const reports = useReports();
  if (reports.isLoading) {
    return (
      <Card padded={false}>
        <ul className="divide-y divide-line" aria-busy="true" aria-hidden>
          {[0, 1, 2].map((key) => (
            <li key={key} className="flex flex-wrap items-center gap-x-6 gap-y-2 px-5 py-3.5">
              <div className="min-w-[16rem] flex-1"><p className="font-medium"><Bar width="12rem" /></p><p className="text-[13px]"><Bar width="60%" /></p><p className="text-[13px]"><Bar width="40%" /></p></div>
              <div className="flex flex-wrap gap-2 text-sm"><Skeleton className="h-9 w-36 rounded-lg" /><Skeleton className="h-9 w-40 rounded-lg" /></div>
            </li>
          ))}
        </ul>
      </Card>
    );
  }
  if (reports.error && !reports.data) return <LoadError title="Rapports indisponibles" error={reports.error} onRetry={() => void reports.refetch()} retrying={reports.isFetching} />;
  if (!reports.data?.reports.length) return <EmptyState title="Aucun rapport" body={admin ? "Générez le premier rapport ci-dessus." : "Aucun rapport n'a encore été généré."} />;
  return (
    <Refreshing active={reports.isFetching}>
    <Card padded={false}>
      <ul className="divide-y divide-line">
        {reports.data.reports.map((report, index) => (
          <li key={report.id} className="flex flex-wrap items-center gap-x-6 gap-y-2 px-5 py-3.5">
            <div className="min-w-[16rem] flex-1">
              <p className="flex flex-wrap items-center gap-2 font-medium">
                {formatDateTime(report.generated_at)}
                {report.stale ? <StatusBadge status="<90j" label="Périmé" /> : null}
              </p>
              <p className="text-[13px] text-muted">
                {figures(report).join(" · ")}
                {report.within_days < ALL_DEADLINES ? ` · échéances sous ${report.within_days} jours` : ""}
              </p>
              <p className="text-[13px] text-muted">{report.stats.site_read_on ? `Sites lus ${report.stats.site_read_on}` : "Date de lecture des sites inconnue"}</p>
              {report.stats.scope ? (
                <p className="mt-1.5 flex flex-wrap gap-1.5">
                  {[...(report.stats.scope.sites.length ? report.stats.scope.sites : ["Tous les sites"]), ...report.stats.scope.include.map((name) => CONTENT_LABEL[name])].map((item) => (
                    <span key={item} className="rounded-full border border-line px-2 py-px text-xs text-ink-soft">{item}</span>
                  ))}
                </p>
              ) : null}
              {report.stale && index === 0 && props.generate ? <Button size="sm" className="mt-2" {...props.generate}>Générer un nouveau rapport</Button> : null}
            </div>
            <div className="flex flex-wrap gap-2 text-sm">
              {report.files["report.pdf"] ? <FileLink href={report.files["report.pdf"]} label="Télécharger le PDF" primary filename={`rapport-${report.generated_at.slice(0, 10)}.pdf`} /> : null}
              <FileLink href={report.files["report.html"]} label={report.files["report.pdf"] ? "Ouvrir à l'écran" : "Ouvrir le rapport"} primary={!report.files["report.pdf"]} open />
              <FileLink href={report.files["matches.csv"]} label="CSV des occurrences" filename="occurrences.csv" />
              <FileLink href={report.files["not_found.csv"]} label="CSV des non trouvés" filename="non-trouves.csv" />
            </div>
          </li>
        ))}
      </ul>
    </Card>
    </Refreshing>
  );
}

/**
 * Storage serves every stored file as plain text without a charset (an HTML
 * file would otherwise run on its domain), so a direct link shows the
 * report's source with broken accents. The file is fetched and handed to the
 * browser with the right type instead: the report opens as a page, the CSVs
 * download under their name.
 */
function FileLink(props: { href: string; label: string; primary?: boolean; open?: boolean; filename?: string }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  if (!props.href) return <span className="text-muted">{props.label} (indisponible)</span>;

  async function go() {
    // Opened right away, inside the click: a tab opened after an await is blocked as a pop-up.
    const tab = props.open ? window.open("", "_blank") : null;
    setBusy(true);
    try {
      const response = await fetch(props.href);
      if (!response.ok) throw new Error(response.status === 400 || response.status === 403 ? "Le lien a expiré : rechargez la page." : "Le fichier est introuvable.");
      const type = props.open ? "text/html;charset=utf-8" : props.filename?.endsWith(".pdf") ? "application/pdf" : "text/csv;charset=utf-8";
      const blob = new Blob([await response.arrayBuffer()], { type });
      const url = URL.createObjectURL(blob);
      if (tab) {
        tab.location.href = url;
      } else {
        const link = document.createElement("a");
        link.href = url;
        link.download = props.filename ?? "export.csv";
        link.click();
      }
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch (error) {
      tab?.close();
      toast.show({ tone: "error", message: "Le fichier n'a pas pu être ouvert", description: errorMessage(error) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <button
      type="button"
      onClick={() => void go()}
      disabled={busy}
      className={
        props.primary
          ? "font-medium underline underline-offset-2 disabled:cursor-progress disabled:opacity-60"
          : "text-ink-soft underline decoration-line-strong underline-offset-2 hover:text-ink disabled:cursor-progress disabled:opacity-60"
      }
    >
      {busy ? (props.open ? "Ouverture…" : "Téléchargement…") : props.label}
    </button>
  );
}
