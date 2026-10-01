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
  httpLabel,
  type HistoryMetric,
} from "../components/insights";
import { Button, EmptyState, Spinner } from "../components/ui";
import { api, errorMessage } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useMe } from "../lib/org";
import type { PlatformInsights } from "../types";
import { BackOfficeSkeleton, BrandsSection, HealthBanner, PlatformStats, RunningAndFailures } from "./BackOfficeSections";

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

function BackOfficeContent() {
  const insights = useQuery({
    queryKey: ["staff-insights"],
    queryFn: () => api.get<PlatformInsights>("/staff/insights"),
    refetchInterval: 30_000,
  });
  const [metric, setMetric] = useState<HistoryMetric>("duration_seconds");

  if (insights.isLoading) return <BackOfficeSkeleton />;
  // A failed background refresh keeps the data already on screen; the error shows only when there is none.
  if (!insights.data) return <EmptyState title="Impossible de charger le back office" body={errorMessage(insights.error)} />;

  const data = insights.data;
  const { totals, brands, crawls, queue, running, failures } = data;

  return (
    <>
      <HealthBanner queue={queue} running={running} />
      <PlatformStats totals={totals} crawls={crawls} />
      <BrandsSection brands={brands} total={data.total_brands} />

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

      <RunningAndFailures running={running} failures={failures} />
    </>
  );
}
