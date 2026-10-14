import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, Outlet } from "react-router";
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
import { Frame } from "../components/Layout";
import { StaffSidebar } from "../components/layout/StaffSidebar";
import { EmptyState, PageHeader, Spinner } from "../components/ui";
import { api, errorMessage } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useMe } from "../lib/org";
import type { PlatformInsights } from "../types";
import { BackOfficeSkeleton, BrandsSection, HealthBanner, PlatformStats, RunningAndFailures } from "./BackOfficeSections";

/** /interne/*: the back office's frame (side column, then the page); Nyra team only. */
export function BackOffice() {
  const me = useMe();
  const auth = useAuth();
  if (me.isLoading) return <div className="p-8"><Spinner /></div>;
  if (!me.data?.staff) {
    return (
      <div className="p-8">
        <EmptyState title="Réservé à l'équipe Nyra" body={`Le compte ${auth.email} n'est pas platform admin (nyra cloud-staff --email …).`} action={<Link to="/" className="text-sm underline">Revenir à l'accueil</Link>} />
      </div>
    );
  }
  return (
    <Frame title="Back office" sidebar={(close) => <StaffSidebar onNavigate={close} />}>
      <Outlet />
    </Frame>
  );
}

/** /interne — every organization side by side and the health of the job queue. */
export function Overview() {
  return (
    <>
      <PageHeader title="Vue d'ensemble" />
      <BackOfficeContent />
    </>
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
      <RunningAndFailures running={running} failures={failures} />
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

    </>
  );
}
