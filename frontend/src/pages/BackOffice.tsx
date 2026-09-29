import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";
import {
  CompareFacts,
  CountList,
  HistoryChart,
  JobsTable,
  MatchingTable,
  MetricTabs,
  PhasesChart,
  Section,
  Table,
  httpLabel,
  type HistoryMetric,
} from "../components/insights";
import { Button, EmptyState, Skeleton, Spinner, Stat, cx } from "../components/ui";
import { api, errorMessage } from "../lib/api";
import { useAuth } from "../lib/auth";
import { bytes, duration, formatDateTime, jobLabel, num, percent } from "../lib/format";
import { useMe } from "../lib/org";
import type { PlatformInsights } from "../types";

/** /interne — every organization side by side and the health of the job queue. Nyra team only. */
export function BackOffice() {
  const me = useMe();
  const auth = useAuth();
  if (me.isLoading) return <Shell><Spinner /></Shell>;
  if (!me.data?.staff) {
    return (
      <Shell>
        <EmptyState title="Réservé à l'équipe Nyra" body={`Le compte ${auth.email} n'a pas accès au back office (nyra cloud-staff --email …).`} action={<Link to="/" className="text-sm underline">Revenir à l'accueil</Link>} />
      </Shell>
    );
  }
  return (
    <Shell>
      <BackOfficeContent />
    </Shell>
  );
}

function Shell(props: { children: React.ReactNode }) {
  const auth = useAuth();
  return (
    <div className="min-h-screen">
      <header className="border-b border-line bg-side">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-4 py-3 md:px-10">
          <div>
            <p className="text-[15px] font-semibold tracking-tight">Nyra · back office</p>
            <p className="text-xs text-muted">Toutes les marques, la file de tâches, les performances</p>
          </div>
          <div className="flex items-center gap-3 text-xs text-muted">
            <Link to="/" className="hover:text-ink">Espaces clients</Link>
            <span className="hidden sm:inline">{auth.email}</span>
            <Button size="sm" variant="ghost" onClick={() => void auth.signOut()}>Se déconnecter</Button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-4 py-6 md:px-10 md:py-8">{props.children}</main>
    </div>
  );
}

function health(queue: PlatformInsights["queue"], running: PlatformInsights["running"]): { tone: "ok" | "warn" | "alert"; label: string; detail: string } {
  const stale = running.some((job) => (job.heartbeat_age_seconds ?? 0) > 120);
  if (stale) return { tone: "alert", label: "Worker muet", detail: "Une tâche en cours n'a pas donné signe de vie depuis plus de 2 min." };
  if (queue.queued > 0 && (queue.oldest_queued_seconds ?? 0) > 600 && queue.running === 0) {
    return { tone: "alert", label: "File bloquée", detail: `${num(queue.queued)} tâche(s) attendent depuis ${duration(queue.oldest_queued_seconds)} et aucune ne tourne : un worker est-il lancé ?` };
  }
  if (queue.failed_24h > 0) return { tone: "warn", label: "Échecs récents", detail: `${num(queue.failed_24h)} tâche(s) en échec sur 24 h.` };
  return { tone: "ok", label: "Tout tourne", detail: queue.running ? `${num(queue.running)} tâche(s) en cours.` : "Aucune tâche en cours, rien en attente." };
}

const toneClass = {
  ok: "border-ok/25 bg-ok-soft text-ok",
  warn: "border-urgent/25 bg-urgent-soft text-urgent",
  alert: "border-expired/25 bg-expired-soft text-expired",
};

function BackOfficeContent() {
  const insights = useQuery({
    queryKey: ["staff-insights"],
    queryFn: () => api.get<PlatformInsights>("/staff/insights"),
    refetchInterval: 30_000,
  });
  const [metric, setMetric] = useState<HistoryMetric>("duration_seconds");

  if (insights.isLoading) {
    return (
      <div className="grid gap-4">
        <Skeleton className="h-20" />
        <div className="grid gap-4 md:grid-cols-4">{[0, 1, 2, 3].map((key) => <Skeleton key={key} className="h-24" />)}</div>
        <Skeleton className="h-72" />
      </div>
    );
  }
  if (insights.error || !insights.data) return <EmptyState title="Impossible de charger le back office" body={errorMessage(insights.error)} />;

  const data = insights.data;
  const { totals, brands, crawls, queue, running, failures } = data;
  const brandLabel = (row: { org_name: string; brand_name: string | null }) =>
    row.brand_name && row.brand_name !== row.org_name ? `${row.org_name} · ${row.brand_name}` : row.org_name;
  const state = health(queue, running);

  return (
    <>
      <section className={cx("flex flex-wrap items-center justify-between gap-4 rounded-xl border p-5", toneClass[state.tone])}>
        <div>
          <p className="text-lg font-semibold">
            <span aria-hidden>{state.tone === "ok" ? "● " : "▲ "}</span>
            {state.label}
          </p>
          <p className="text-sm text-ink-soft">{state.detail}</p>
        </div>
        <dl className="flex flex-wrap gap-6 text-sm text-ink">
          <Mini label="En attente" value={num(queue.queued)} />
          <Mini label="En cours" value={num(queue.running)} />
          <Mini label="Tâches 24 h" value={num(queue.jobs_24h)} />
          <Mini label="Échecs 24 h" value={num(queue.failed_24h)} />
          <Mini label="Dernier battement" value={queue.last_heartbeat_seconds === null ? "—" : `il y a ${duration(queue.last_heartbeat_seconds)}`} />
        </dl>
      </section>

      <div className="mt-4 grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat value={num(totals.brands)} label="marques" hint={`${num(totals.organizations)} organisation(s) · ${num(totals.members)} compte(s)`} />
        <Stat value={num(totals.site_files)} label="images de sites stockées" hint={`${num(totals.pages_read)} pages lues`} />
        <Stat value={num(totals.references)} label="visuels sous surveillance" hint={`${num(totals.matches)} correspondance(s)`} />
        <Stat value={bytes(totals.storage_bytes)} label="stockage total" hint="copies de travail des sites + bibliothèques" />
      </div>

      <div className="mt-4 grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat value={duration(crawls.summary.avg_duration_seconds)} label="durée moyenne d'une lecture" hint={`max ${duration(crawls.summary.max_duration_seconds)} · ${num(crawls.summary.finished)} lecture(s) sur 90 j`} />
        <Stat value={num(crawls.summary.avg_pages_per_minute, 1)} label="pages lues par minute" hint={`${num(crawls.summary.avg_images_per_page, 1)} images par page`} />
        <Stat value={num(crawls.summary.clip_images_per_second, 1)} label="images CLIP par seconde" hint={`${num(crawls.summary.avg_images_scanned_per_second, 1)} images vues / s en lecture`} />
        <Stat value={bytes(crawls.summary.avg_new_image_bytes)} label="poids moyen d'une nouvelle image" hint={`${bytes(crawls.summary.bytes_downloaded)} téléchargés sur 90 j`} />
      </div>

      <Section className="mt-6" title="Marques" description="Stockage, lectures sur 90 jours, qualité du matching, échecs sur 30 jours.">
        <Table
          head={["Marque", "Sites", "Images", "Poids moy.", "Visuels", "Expirés", "Stockage", "Lectures", "Dernière", "Durée moy.", "Pages/min", "CLIP img/s", "Corresp.", "Taux FP", "Échecs 30 j", ""]}
          rows={brands.map((org) => [
            <span>
              {org.name}
              {org.org_name !== org.name ? <span className="block text-xs font-normal text-muted">{org.org_name}</span> : null}
            </span>,
            num(org.sites),
            num(org.distinct_files),
            bytes(org.avg_image_bytes),
            num(org.references_total),
            org.expired ? <span className="text-expired">{num(org.expired)}</span> : "0",
            bytes(org.storage_bytes),
            num(org.crawls_90d),
            org.last_crawl_at ? (
              <span className={org.last_crawl_status === "error" ? "text-expired" : undefined} title={org.last_crawl_status ?? undefined}>
                {formatDateTime(org.last_crawl_at)}
              </span>
            ) : (
              <span className="text-muted">jamais</span>
            ),
            duration(org.avg_crawl_seconds),
            num(org.avg_pages_per_minute, 1),
            num(org.clip_images_per_second, 1),
            num(org.matches),
            percent(org.false_positive_rate),
            org.failed_jobs_30d ? <span className="text-expired">{num(org.failed_jobs_30d)} / {num(org.jobs_30d)}</span> : `0 / ${num(org.jobs_30d)}`,
            <Link to={`/o/${org.org_slug}/m/${org.slug}/statistiques`} className="text-xs text-muted underline-offset-2 hover:text-ink hover:underline">Détail</Link>,
          ])}
          empty="Aucune marque."
        />
        <p className="mt-3 text-xs text-muted">« Détail » ouvre la page Statistiques de la marque si votre compte en est administrateur.</p>
      </Section>

      <Section className="mt-4" title="Lectures, toutes marques" description="Les 60 dernières sur 90 jours. Survolez une barre pour la marque et le détail." aside={<MetricTabs value={metric} onChange={setMetric} />}>
        <HistoryChart runs={crawls.history} metric={metric} withOrg />
      </Section>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Section title="Où passe le temps" description="Cumul des lectures sur 90 jours, toutes marques.">
          <PhasesChart phases={crawls.summary.phases_seconds} />
        </Section>
        <Section title="Tâches sur 30 jours">
          <JobsTable jobs={data.jobs} />
        </Section>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Section title="Réponses HTTP">
          <CountList counts={crawls.summary.http_statuses} label={httpLabel} />
        </Section>
        <Section title="Formats des nouvelles images">
          <CountList counts={crawls.summary.formats_new} label={(format) => format.toUpperCase()} />
        </Section>
        <Section title="Dernière comparaison">
          <CompareFacts compare={data.compare} />
        </Section>
      </div>

      <Section className="mt-4" title="Correspondances, toutes marques">
        <MatchingTable matching={data.matching} />
      </Section>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Section title="En cours">
          <Table
            head={["Marque", "Tâche", "Depuis", "Battement", "Message"]}
            align={["left", "left", "right", "right", "left"]}
            rows={running.map((job) => [
              brandLabel(job),
              jobLabel[job.kind],
              formatDateTime(job.started_at),
              <span className={(job.heartbeat_age_seconds ?? 0) > 120 ? "text-expired" : undefined}>{duration(job.heartbeat_age_seconds)}</span>,
              <span className="block max-w-64 truncate text-muted">{job.message}</span>,
            ])}
            empty="Aucune tâche en cours."
          />
        </Section>
        <Section title="Derniers échecs">
          <Table
            head={["Marque", "Tâche", "Quand", "Erreur"]}
            align={["left", "left", "right", "left"]}
            rows={failures.map((job) => [
              brandLabel(job),
              jobLabel[job.kind],
              formatDateTime(job.finished_at),
              <span className="block max-w-48 truncate font-mono text-xs text-muted" title={job.error ?? undefined}>{job.error ?? "—"}</span>,
            ])}
            empty="Aucun échec."
          />
        </Section>
      </div>
    </>
  );
}

function Mini(props: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs text-ink-soft">{props.label}</dt>
      <dd className="font-semibold tabular">{props.value}</dd>
    </div>
  );
}
