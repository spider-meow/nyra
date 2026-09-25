import { useState } from "react";
import { useToast } from "../components/feedback";
import { Button, Card, EmptyState, FieldLabel, PageHeader, Select, Skeleton } from "../components/ui";
import { errorMessage } from "../lib/api";
import { formatDateTime } from "../lib/format";
import { useOrg } from "../lib/org";
import { useJobs, useOverview, useReports, useStartJob } from "../lib/queries";

export function Reports() {
  const { admin } = useOrg();
  const overview = useOverview();
  const reports = useReports();
  const jobs = useJobs();
  const start = useStartJob();
  const toast = useToast();
  const [withinDays, setWithinDays] = useState<string>("");
  const range = withinDays || String(overview.data?.defaults.within_days ?? 90);
  const generating = (jobs.data?.active ?? []).some((job) => job.kind === "report");

  function generate() {
    start.mutate(
      { kind: "report", body: { within_days: Number(range) } },
      {
        onSuccess: () =>
          toast.show({ tone: "success", message: "Rapport en préparation", description: "Il apparaîtra dans la liste ci-dessous dans quelques instants." }),
        onError: (error) => toast.show({ tone: "error", message: "Le rapport n'a pas été lancé", description: errorMessage(error) }),
      },
    );
  }

  return (
    <>
      <PageHeader
        title="Rapports"
        description="Un instantané daté, à transmettre tel quel : un rapport lisible hors ligne (imprimable en PDF) et deux fichiers CSV. Les faux positifs n'y figurent pas."
      />

      {admin ? (
        <Card className="mb-6">
          <div className="flex flex-wrap items-end gap-3">
            <div className="w-64">
              <FieldLabel htmlFor="report-window">Visuels à inclure</FieldLabel>
              <Select id="report-window" value={range} onChange={(event) => setWithinDays(event.target.value)}>
                {[30, 90, 180, 365].map((days) => <option key={days} value={days}>Expirés ou sous {days} jours</option>)}
                <option value="3650">Toutes les échéances</option>
              </Select>
            </div>
            <Button variant="primary" onClick={generate} loading={generating || start.isPending}>
              {generating ? "Préparation…" : "Générer un rapport"}
            </Button>
          </div>
        </Card>
      ) : null}

      {reports.isLoading ? (
        <Skeleton className="h-40" />
      ) : reports.error ? (
        <EmptyState title="Rapports indisponibles" body={errorMessage(reports.error)} />
      ) : !reports.data?.reports.length ? (
        <EmptyState title="Aucun rapport" body={admin ? "Générez le premier rapport ci-dessus." : "Aucun rapport n'a encore été généré."} />
      ) : (
        <Card padded={false}>
          <ul className="divide-y divide-line">
            {reports.data.reports.map((report) => (
              <li key={report.id} className="flex flex-wrap items-center gap-x-6 gap-y-2 px-5 py-3.5">
                <div className="min-w-0 flex-1">
                  <p className="font-medium">{formatDateTime(report.generated_at)}</p>
                  <p className="text-[13px] text-muted">
                    {report.within_days >= 3650 ? "Toutes les échéances" : `Expirés ou sous ${report.within_days} jours`}
                    {typeof report.stats.expired_online === "number" ? ` · ${report.stats.expired_online} expiré(s) en ligne` : ""}
                    {typeof report.stats.to_verify === "number" ? ` · ${report.stats.to_verify} à vérifier` : ""}
                  </p>
                </div>
                <div className="flex flex-wrap gap-2 text-sm">
                  <FileLink href={report.files["report.html"]} label="Ouvrir le rapport" primary />
                  <FileLink href={report.files["matches.csv"]} label="CSV des occurrences" />
                  <FileLink href={report.files["not_found.csv"]} label="CSV des non trouvés" />
                </div>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </>
  );
}

function FileLink(props: { href: string; label: string; primary?: boolean }) {
  if (!props.href) return <span className="text-muted">{props.label} (indisponible)</span>;
  return (
    <a
      href={props.href}
      target="_blank"
      rel="noreferrer noopener"
      className={props.primary ? "font-medium underline underline-offset-2" : "text-ink-soft underline decoration-line-strong underline-offset-2 hover:text-ink"}
    >
      {props.label}
    </a>
  );
}
