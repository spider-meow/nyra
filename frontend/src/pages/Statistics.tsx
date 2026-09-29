import { useState } from "react";
import { ShareBar } from "../components/charts";
import {
  CompareFacts,
  CountList,
  Facts,
  HistoryChart,
  JobsTable,
  MatchingTable,
  MetricTabs,
  PhasesChart,
  Section,
  Table,
  crawlRunHead,
  crawlRunRows,
  httpLabel,
  type HistoryMetric,
} from "../components/insights";
import { EmptyState, PageHeader, Skeleton, Stat } from "../components/ui";
import { errorMessage } from "../lib/api";
import { bytes, duration, formatDateTime, num, percent } from "../lib/format";
import { useOrg } from "../lib/org";
import { useInsights } from "../lib/queries";

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

  if (insights.isLoading) {
    return (
      <div className="grid gap-4">
        <Skeleton className="h-9 w-64" />
        <div className="grid gap-4 md:grid-cols-4">
          {[0, 1, 2, 3].map((key) => <Skeleton key={key} className="h-24" />)}
        </div>
        <Skeleton className="h-64" />
      </div>
    );
  }
  if (insights.error || !insights.data) return <EmptyState title="Impossible de charger les statistiques" body={errorMessage(insights.error)} />;

  const { site, formats, library, crawls, jobs, compare, matching } = insights.data;
  const { summary, last, history } = crawls;
  const dedupRatio = site.image_urls ? 1 - site.distinct_files / site.image_urls : null;
  const totalFormatFiles = formats.reduce((sum, row) => sum + row.files, 0);

  return (
    <>
      <PageHeader
        title="Statistiques"
        description={last ? <>Lectures des {num(site.sites)} site(s) de {brand.name}, bibliothèque et comparaisons. Dernière lecture le {formatDateTime(last.started_at)}.</> : `Lectures des sites de ${brand.name}, bibliothèque et comparaisons.`}
      />

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat value={num(site.distinct_files)} label="images du site stockées" hint={`${num(site.image_urls)} URL, ${num(site.pages_read)} pages lues`} />
        <Stat value={bytes(site.avg_bytes)} label="poids moyen d'une image" hint={`médiane ${bytes(site.median_bytes)} · max ${bytes(site.max_bytes)}`} />
        <Stat value={last ? num(last.images_scanned_per_second, 1) : "—"} label="images vues par seconde" hint={`CLIP : ${num(summary.clip_images_per_second, 1)} images/s`} />
        <Stat value={duration(last?.duration_seconds)} label="durée de la dernière lecture" hint={`moyenne ${duration(summary.avg_duration_seconds)} sur ${num(summary.finished)} lecture(s)`} />
      </div>

      <div className="mt-4 grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat value={num(summary.avg_pages_per_minute, 1)} label="pages lues par minute" hint={`${duration(summary.avg_seconds_per_page)} de rendu par page`} />
        <Stat value={num(summary.avg_images_per_page, 1)} label="images par page" hint={`${num(site.image_page_links)} liens image-page`} />
        <Stat value={bytes((site.stored_bytes ?? 0) + (library.total_bytes ?? 0))} label="stockage utilisé" hint={`copies des images ${bytes(site.stored_bytes)} · bibliothèque ${bytes(library.total_bytes)}`} />
        <Stat value={percent(matching.total.false_positive_rate)} label="de faux positifs" hint={`sur ${num(matching.total.reviewed)} correspondance(s) décidée(s)`} />
      </div>

      <Section className="mt-6" title="Historique des lectures" description="Survolez une barre pour le détail. Une mesure à la fois." aside={<MetricTabs value={metric} onChange={setMetric} />}>
        <HistoryChart runs={history} metric={metric} />
      </Section>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Section title="Où passe le temps" description={`Cumul sur les ${num(summary.finished)} dernière(s) lecture(s). Les pages sont lues en parallèle : le cumul dépasse la durée réelle.`}>
          <PhasesChart phases={summary.phases_seconds} />
        </Section>
        <Section title="Dernière lecture en détail" description={last ? formatDateTime(last.started_at) : undefined}>
          {last ? (
            <Facts
              rows={[
                ["URL dans les sitemaps", num(last.sitemap_urls)],
                ["Pages lues (dont en échec)", `${num(last.pages_visited)} (${num(last.pages_failed)})`],
                ["Images repérées", num(last.images_found)],
                ["Déjà connues, pas retéléchargées", num(last.images_known)],
                ["Téléchargées (dont en échec)", `${num(last.downloads)} (${num(last.downloads_failed)})`],
                ["Octets téléchargés", bytes(last.bytes_downloaded)],
                ["Mêmes octets qu'une autre URL", num(last.images_duplicate)],
                ["Écartées (trop petites, illisibles)", num(last.images_rejected)],
                ["Nouvelles images stockées", `${num(last.images_new)} · ${bytes(last.bytes_new)}`],
                ["Bloquées par robots.txt", num(last.blocked_by_robots)],
              ]}
            />
          ) : (
            <p className="text-sm text-muted">Pas encore de lecture.</p>
          )}
        </Section>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Section title="Formats des images" description={`${num(totalFormatFiles)} fichier(s) distinct(s)`}>
          {formats.length ? (
            <ul className="grid gap-2 text-sm">
              {formats.map((row) => (
                <li key={row.format} className="grid grid-cols-[3.5rem_minmax(0,1fr)] items-center gap-3">
                  <span className="uppercase text-ink-soft">{row.format}</span>
                  <ShareBar ratio={totalFormatFiles ? row.files / totalFormatFiles : 0}>
                    <span className="w-28 text-right text-xs tabular">
                      {num(row.files)} <span className="text-muted">· moy. {bytes(row.avg_bytes)}</span>
                    </span>
                  </ShareBar>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted">Pas encore d'image.</p>
          )}
        </Section>
        <Section title="Anatomie des images" description="Fichiers distincts du site">
          <Facts
            rows={[
              ["Dimensions moyennes", site.avg_width ? `${num(site.avg_width)} × ${num(site.avg_height)} px` : "—"],
              ["Mégapixels moyens", num(site.avg_megapixels, 2)],
              ["Poids total sur le site", bytes(site.total_bytes)],
              ["Stocké par Nyra (copies + vignettes)", bytes(site.stored_bytes)],
              ["Poids médian", bytes(site.median_bytes)],
              ["URL servant des octets déjà vus", percent(dedupRatio)],
              ["Poids mesuré pour", `${num(site.files_with_size)} / ${num(site.distinct_files)} fichiers`],
            ]}
          />
        </Section>
        <Section title="Réponses HTTP des pages" description="Cumul des lectures récentes">
          <CountList counts={summary.http_statuses} label={httpLabel} />
        </Section>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Section title="Correspondances et décisions">
          <MatchingTable matching={matching} />
        </Section>
        <div className="grid min-w-0 content-start gap-4">
          <Section title="Comparaison">
            <CompareFacts compare={compare} />
          </Section>
          <Section title="Bibliothèque">
            <Facts
              rows={[
                ["Visuels", num(library.references_total)],
                ["Indexés (CLIP)", `${num(library.references_indexed)} · ${percent(library.references_total ? library.references_indexed / library.references_total : null)}`],
                ["Expirés", num(library.expired)],
                ["Expirent sous 30 / 90 jours", `${num(library.expiring_30_days)} / ${num(library.expiring_90_days)}`],
                ["Sans échéance", num(library.without_expiry)],
                ["Poids moyen d'un visuel", bytes(library.avg_bytes)],
                ["Mégapixels moyens", num(library.avg_megapixels, 1)],
              ]}
            />
          </Section>
        </div>
      </div>

      <Section className="mt-4" title="Tâches" description="Toutes les tâches de l'organisation depuis le début.">
        <JobsTable jobs={jobs} />
      </Section>

      <Section className="mt-4" title="Toutes les lectures" description="Les 30 dernières, la plus récente en haut. Le tableau reprend les valeurs du graphique.">
        <Table head={crawlRunHead} rows={crawlRunRows(history)} empty="Pas encore de lecture." />
      </Section>
    </>
  );
}
