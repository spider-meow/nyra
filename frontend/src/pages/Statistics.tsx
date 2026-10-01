import { useState } from "react";
import {
  CountList,
  HistoryChart,
  JobsTable,
  MetricTabs,
  PhasesChart,
  Section,
  Table,
  crawlRunHead,
  crawlRunRows,
  httpLabel,
  type HistoryMetric,
} from "../components/insights";
import { EmptyState, PageHeader } from "../components/ui";
import { errorMessage } from "../lib/api";
import { formatDateTime, num } from "../lib/format";
import { useOrg } from "../lib/org";
import { useInsights } from "../lib/queries";
import { AnatomySection, DecisionsAndLibrary, FormatsSection, LastCrawlSection, StatisticsSkeleton, StatisticsStats } from "./StatisticsSections";

/** /o/:slug/statistiques — how the organization's site and pipeline behave, for its admins. */
export function Statistics() {
  const { admin } = useOrg();
  if (!admin) return <EmptyState title="Réservé aux administrateurs" body="Les statistiques détaillées sont visibles par les administrateurs de l'organisation." />;
  return <StatisticsContent />;
}

function StatisticsContent() {
  const { brand } = useOrg();
  const insights = useInsights();
  const [metric, setMetric] = useState<HistoryMetric>("duration_seconds");

  if (insights.isLoading) return <StatisticsSkeleton />;
  // A failed background refresh keeps the data already on screen; the error shows only when there is none.
  if (!insights.data) return <EmptyState title="Impossible de charger les statistiques" body={errorMessage(insights.error)} />;

  const { site, formats, library, crawls, jobs, compare, matching } = insights.data;
  const { summary, last, history } = crawls;

  return (
    <>
      <PageHeader
        title="Statistiques"
        description={last ? <>Lectures des {num(site.sites)} site(s) de {brand.name}, bibliothèque et comparaisons. Dernière lecture le {formatDateTime(last.started_at)}.</> : `Lectures des sites de ${brand.name}, bibliothèque et comparaisons.`}
      />

      <StatisticsStats insights={insights.data} />

      <Section className="mt-6" title="Historique des lectures" description="Survolez une barre pour le détail. Une mesure à la fois." aside={<MetricTabs value={metric} onChange={setMetric} />}>
        <HistoryChart runs={history} metric={metric} />
      </Section>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Section title="Où passe le temps" description={`Cumul sur les ${num(summary.finished)} dernière(s) lecture(s). Les pages sont lues en parallèle : le cumul dépasse la durée réelle.`}>
          <PhasesChart phases={summary.phases_seconds} />
        </Section>
        <LastCrawlSection last={last} />
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <FormatsSection formats={formats} />
        <AnatomySection site={site} />
        <Section title="Réponses HTTP des pages" description="Cumul des lectures récentes">
          <CountList counts={summary.http_statuses} label={httpLabel} />
        </Section>
      </div>

      <DecisionsAndLibrary matching={matching} compare={compare} library={library} />

      <Section className="mt-4" title="Tâches" description="Toutes les tâches de l'organisation depuis le début.">
        <JobsTable jobs={jobs} />
      </Section>

      <Section className="mt-4" title="Toutes les lectures" description="Les 30 dernières, la plus récente en haut. Le tableau reprend les valeurs du graphique.">
        <Table head={crawlRunHead} rows={crawlRunRows(history)} empty="Pas encore de lecture." />
      </Section>
    </>
  );
}
