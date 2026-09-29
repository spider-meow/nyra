import type { ReactNode } from "react";
import { bytes, confidenceLabel, duration, formatDateTime, hostOf, jobLabel, num, percent } from "../lib/format";
import type { CompareStats, Confidence, CrawlRun, CrawlSummary, JobKindStats, MatchingStats } from "../types";
import { BarHistory, ShareBar, SplitBar, type Bar } from "./charts";
import { Card, cx } from "./ui";

/** Pieces shared by the organization's Statistiques page and the Nyra back office. */

export function Section(props: { title: string; description?: ReactNode; children: ReactNode; className?: string; aside?: ReactNode }) {
  return (
    <Card className={cx("min-w-0", props.className)}>
      <div className="mb-4 flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 className="font-semibold">{props.title}</h2>
          {props.description ? <p className="mt-0.5 text-sm text-muted">{props.description}</p> : null}
        </div>
        {props.aside}
      </div>
      {props.children}
    </Card>
  );
}

export function Facts(props: { rows: [string, ReactNode][] }) {
  return (
    <dl className="grid gap-2 text-sm">
      {props.rows.map(([label, value]) => (
        <div key={label} className="flex justify-between gap-3">
          <dt className="text-muted">{label}</dt>
          <dd className="text-right font-medium tabular">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Table(props: { head: string[]; rows: ReactNode[][]; align?: ("left" | "right")[]; empty?: string }) {
  if (!props.rows.length) return <p className="py-4 text-sm text-muted">{props.empty ?? "Rien pour l'instant."}</p>;
  const align = (index: number) => (props.align?.[index] ?? (index === 0 ? "left" : "right")) === "right" ? "text-right" : "text-left";
  return (
    <div className="-mx-5 overflow-x-auto px-5">
      <table className="w-full min-w-max text-sm">
        <thead>
          <tr className="border-b border-line text-xs text-muted">
            {props.head.map((cell, index) => (
              <th key={cell} scope="col" className={cx("py-2 pr-4 font-medium last:pr-0", align(index))}>{cell}</th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {props.rows.map((row, rowIndex) => (
            <tr key={rowIndex}>
              {row.map((cell, index) => (
                <td key={index} className={cx("py-2 pr-4 tabular last:pr-0", align(index), index === 0 && "font-medium")}>{cell}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const shortDate = new Intl.DateTimeFormat("fr-FR", { day: "numeric", month: "short" });

function dayLabel(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : shortDate.format(date);
}

type HistoryRun = Pick<CrawlRun, "started_at" | "status" | "duration_seconds" | "pages_visited" | "images_found" | "images_new" | "pages_per_minute" | "images_scanned_per_second" | "clip_images_per_second"> & { org_name?: string; brand_name?: string | null; site_url?: string };

export type HistoryMetric = "duration_seconds" | "pages_per_minute" | "images_scanned_per_second" | "clip_images_per_second" | "images_new";

export const historyMetrics: Record<HistoryMetric, { label: string; format: (value: number) => string }> = {
  duration_seconds: { label: "Durée", format: duration },
  pages_per_minute: { label: "Pages / min", format: (value) => num(value, 1) },
  images_scanned_per_second: { label: "Images vues / s", format: (value) => num(value, 1) },
  clip_images_per_second: { label: "CLIP images / s", format: (value) => num(value, 1) },
  images_new: { label: "Nouvelles images", format: (value) => num(value) },
};

function runOwner(run: HistoryRun, withOrg: boolean): string {
  if (withOrg) {
    const name = run.brand_name && run.brand_name !== run.org_name ? `${run.org_name} · ${run.brand_name}` : run.org_name;
    return name ? `${name} · ` : "";
  }
  // One brand, several sites: say which one.
  return run.site_url ? `${hostOf(run.site_url)} · ` : "";
}

export function historyBars(runs: HistoryRun[], metric: HistoryMetric, withOrg = false): Bar[] {
  return runs.map((run, index) => ({
    key: `${run.started_at}-${index}`,
    value: run[metric] ?? null,
    label: `${withOrg ? runOwner(run, true) : ""}${dayLabel(run.started_at)}`,
    longLabel: `${runOwner(run, withOrg)}${formatDateTime(run.started_at)}`,
    details: [
      { label: "Durée", value: duration(run.duration_seconds) },
      { label: "Pages", value: num(run.pages_visited) },
      { label: "Images vues", value: num(run.images_found) },
      { label: "Nouvelles", value: num(run.images_new) },
      { label: "Pages / min", value: num(run.pages_per_minute, 1) },
      { label: "CLIP img / s", value: num(run.clip_images_per_second, 1) },
      ...(run.status !== "done" ? [{ label: "État", value: run.status === "error" ? "Échec" : run.status === "cancelled" ? "Arrêtée" : "En cours" }] : []),
    ],
  }));
}

/** A metric picker above the history chart: one measure at a time, never two scales on one plot. */
export function MetricTabs(props: { value: HistoryMetric; onChange: (metric: HistoryMetric) => void }) {
  return (
    <div className="flex flex-wrap gap-1" role="tablist" aria-label="Mesure affichée">
      {(Object.keys(historyMetrics) as HistoryMetric[]).map((metric) => (
        <button
          key={metric}
          type="button"
          role="tab"
          aria-selected={props.value === metric}
          onClick={() => props.onChange(metric)}
          className={cx(
            "rounded-md px-2 py-1 text-xs",
            props.value === metric ? "bg-ink text-white" : "text-ink-soft hover:bg-canvas",
          )}
        >
          {historyMetrics[metric].label}
        </button>
      ))}
    </div>
  );
}

export function HistoryChart(props: { runs: HistoryRun[]; metric: HistoryMetric; withOrg?: boolean }) {
  const spec = historyMetrics[props.metric];
  return <BarHistory bars={historyBars(props.runs, props.metric, props.withOrg)} format={spec.format} title={`${spec.label} par lecture`} />;
}

const phaseLabels: [keyof CrawlSummary["phases_seconds"], string][] = [
  ["render_seconds", "Chargement des pages (Chromium)"],
  ["download_seconds", "Téléchargement des images"],
  ["process_seconds", "Décodage, hash, vignettes"],
  ["embed_seconds", "Embeddings CLIP"],
  ["store_seconds", "Envoi au stockage + base"],
];

export function PhasesChart(props: { phases: CrawlSummary["phases_seconds"] }) {
  return (
    <SplitBar
      title="Répartition du temps de lecture"
      format={duration}
      segments={phaseLabels.map(([key, label]) => ({ key, label, value: props.phases[key] ?? 0 }))}
    />
  );
}

export function CountList(props: { counts: Record<string, number>; label?: (key: string) => string }) {
  const entries = Object.entries(props.counts);
  const total = entries.reduce((sum, [, count]) => sum + count, 0);
  if (!total) return <p className="text-sm text-muted">Pas encore mesuré.</p>;
  return (
    <ul className="grid gap-2 text-sm">
      {entries.map(([key, count]) => (
        <li key={key} className="grid grid-cols-[4.5rem_minmax(0,1fr)] items-center gap-3">
          <span className="text-ink-soft">{props.label ? props.label(key) : key}</span>
          <ShareBar ratio={count / total}>
            <span className="w-24 text-right text-xs tabular">
              {num(count)} <span className="text-muted">· {percent(count / total)}</span>
            </span>
          </ShareBar>
        </li>
      ))}
    </ul>
  );
}

export function httpLabel(status: string): string {
  return status === "0" ? "Échec" : status;
}

export function JobsTable(props: { jobs: JobKindStats[] }) {
  return (
    <Table
      head={["Tâche", "Total", "Réussies", "Échecs", "Durée moy.", "p95", "Attente moy."]}
      rows={props.jobs.map((job) => [
        jobLabel[job.kind],
        num(job.total),
        num(job.done),
        <span className={job.failed ? "text-expired" : undefined}>{num(job.failed)}</span>,
        duration(job.avg_run_seconds),
        duration(job.p95_run_seconds),
        duration(job.avg_wait_seconds),
      ])}
      empty="Aucune tâche pour l'instant."
    />
  );
}

const confidenceOrder: Confidence[] = ["haut", "moyen", "a_verifier"];

export function MatchingTable(props: { matching: MatchingStats }) {
  const rows = confidenceOrder
    .filter((confidence) => props.matching.by_confidence[confidence])
    .map((confidence) => {
      const bucket = props.matching.by_confidence[confidence]!;
      return [
        confidenceLabel[confidence],
        num(bucket.matches),
        num(bucket.reviewed),
        num(bucket.to_remove + bucket.removed),
        num(bucket.false_positives),
        percent(bucket.false_positive_rate),
      ];
    });
  const levels = Object.entries(props.matching.by_level).map(([level, bucket]) => [
    level === "phash" ? "pHash" : level === "dhash" ? "dHash" : level === "clip" ? "CLIP" : level,
    num(bucket.matches),
    num(bucket.reviewed),
    num(bucket.to_remove + bucket.removed),
    num(bucket.false_positives),
    percent(bucket.false_positive_rate),
  ]);
  const head = ["", "Trouvées", "Décidées", "Vraies", "Faux positifs", "Taux de FP"];
  return (
    <div className="grid gap-4">
      <Table head={["Confiance", ...head.slice(1)]} rows={rows} empty="Aucune correspondance pour l'instant." />
      {levels.length ? <Table head={["Niveau", ...head.slice(1)]} rows={levels} /> : null}
      <p className="text-xs text-muted">
        Le taux de faux positifs n'est calculé que sur les correspondances déjà décidées : c'est une mesure de précision, pas de rappel.
      </p>
    </div>
  );
}

export function CompareFacts(props: { compare: CompareStats | null }) {
  const compare = props.compare;
  if (!compare) return <p className="text-sm text-muted">Pas encore de comparaison mesurée.</p>;
  return (
    <Facts
      rows={[
        ["Dernière comparaison", formatDateTime(compare.finished_at)],
        ["Mode", compare.full ? "Complète (tout recomparé)" : "Incrémentale (seulement le nouveau)"],
        ["Paires comparées", num(compare.pairs)],
        ["Durée", duration(compare.seconds)],
        ["dont comparaison des empreintes", duration(compare.compare_seconds)],
        ["dont vérification par points-clés", compare.verified_candidates ? `${duration(compare.verify_seconds)} · ${num(compare.verified_candidates)} candidat(s)` : "—"],
        ["Débit", compare.pairs_per_second === null ? "—" : `${num(compare.pairs_per_second)} paires / s`],
        ["Correspondances trouvées", num(compare.hits)],
        ["Images exclues (faux positifs récurrents)", num(compare.excluded)],
      ]}
    />
  );
}

export function crawlRunRows(runs: CrawlRun[]): ReactNode[][] {
  return [...runs].reverse().map((run) => [
    <span>
      {formatDateTime(run.started_at)}
      <span className="block text-xs font-normal text-muted">{hostOf(run.site_url)}</span>
    </span>,
    run.status === "done" ? duration(run.duration_seconds) : run.status === "error" ? <span className="text-expired">Échec</span> : run.status === "cancelled" ? "Arrêtée" : "En cours",
    num(run.pages_visited),
    num(run.pages_per_minute, 1),
    num(run.images_found),
    num(run.images_new),
    num(run.images_scanned_per_second, 1),
    num(run.clip_images_per_second, 1),
    bytes(run.avg_new_image_bytes),
    run.error_count ? <span className="text-urgent">{num(run.error_count)}</span> : "0",
  ]);
}

export const crawlRunHead = ["Lecture", "Durée", "Pages", "Pages/min", "Images vues", "Nouvelles", "Img vues/s", "CLIP img/s", "Poids moy.", "Erreurs"];
