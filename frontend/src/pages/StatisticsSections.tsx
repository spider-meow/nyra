import { ShareBar } from "../components/charts";
import { CompareFacts, Facts, MatchingTable, Section } from "../components/insights";
import { Bar, PageHeader, Skeleton, Stat } from "../components/ui";
import { bytes, duration, formatDateTime, num, percent } from "../lib/format";
import type { OrgInsights } from "../types";

/** The blocks of the Statistiques page that carry their own figures; the shared charts live in components/insights. */

/** A card of the page while its figures load: the title is there already, the body keeps the height the card has once loaded (`height`, in px: it depends on the data). */
function SectionSkeleton(props: { title: string; className?: string; height: number; described?: boolean; chart?: boolean }) {
  return (
    <Section className={props.className} title={props.title} description={props.described ? <Bar width="70%" /> : undefined}>
      <div style={{ height: props.height }}>
        {props.chart ? <Skeleton className="h-full" /> : <div className="grid gap-3 text-sm">{Array.from({ length: Math.max(1, Math.round(props.height / 40)) }, (_, key) => <Bar key={key} width={`${90 - (key % 3) * 15}%`} />)}</div>}
      </div>
    </Section>
  );
}

/** The first load of the page: the title, the two rows of figures in their tints, and each card with its real title and the height it takes once loaded. */
export function StatisticsSkeleton() {
  const tints = ["yellow", "mint", "blush", "teal"] as const;
  const stats = (row: string) => tints.map((tint) => <Stat key={row + tint} tint={tint} value={<Bar width="4rem" />} label={<><Bar /><Bar width="60%" /></>} hint={<><Bar /><Bar width="50%" /></>} />);
  return (
    <div aria-busy="true" aria-hidden>
      <PageHeader title="Statistiques" />
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">{stats("a")}</div>
      <div className="mt-4 grid grid-cols-2 gap-4 lg:grid-cols-4">{stats("b")}</div>
      <SectionSkeleton className="mt-6" title="Historique des lectures" described height={185} chart />
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <SectionSkeleton title="Où passe le temps" described height={250} />
        <SectionSkeleton title="Dernière lecture en détail" described height={250} />
      </div>
      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <SectionSkeleton title="Formats des images" described height={250} />
        <SectionSkeleton title="Anatomie des images" described height={250} />
        <SectionSkeleton title="Réponses HTTP des pages" described height={250} />
      </div>
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <SectionSkeleton title="Correspondances et décisions" height={320} />
        <div className="grid content-start gap-4">
          <SectionSkeleton title="Comparaison" height={20} />
          <SectionSkeleton title="Bibliothèque" height={216} />
        </div>
      </div>
      <SectionSkeleton className="mt-4" title="Tâches" described height={70} />
      <SectionSkeleton className="mt-4" title="Toutes les lectures" described height={350} />
    </div>
  );
}

/** Two rows of figures: site and crawl speed, then pages, storage and false positives. */
export function StatisticsStats(props: { insights: OrgInsights }) {
  const { site, library, crawls, matching } = props.insights;
  const { summary, last } = crawls;
  return (
    <>
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat tint="yellow" value={num(site.distinct_files)} label="images du site stockées" hint={`${num(site.image_urls)} URL, ${num(site.pages_read)} pages lues`} />
        <Stat tint="mint" value={bytes(site.avg_bytes)} label="poids moyen d'une image" hint={`médiane ${bytes(site.median_bytes)} · max ${bytes(site.max_bytes)}`} />
        <Stat tint="blush" value={last ? num(last.images_scanned_per_second, 1) : "—"} label="images vues par seconde" hint={`CLIP : ${num(summary.clip_images_per_second, 1)} images/s`} />
        <Stat tint="teal" value={duration(last?.duration_seconds)} label="durée de la dernière lecture" hint={`moyenne ${duration(summary.avg_duration_seconds)} sur ${num(summary.finished)} lecture(s)`} />
      </div>

      <div className="mt-4 grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat tint="yellow" value={num(summary.avg_pages_per_minute, 1)} label="pages lues par minute" hint={`${duration(summary.avg_seconds_per_page)} de rendu par page`} />
        <Stat tint="mint" value={num(summary.avg_images_per_page, 1)} label="images par page" hint={`${num(site.image_page_links)} liens image-page`} />
        <Stat tint="blush" value={bytes((site.stored_bytes ?? 0) + (library.total_bytes ?? 0))} label="stockage utilisé" hint={`copies des images ${bytes(site.stored_bytes)} · bibliothèque ${bytes(library.total_bytes)}`} />
        <Stat tint="teal" value={percent(matching.total.false_positive_rate)} label="de faux positifs" hint={`sur ${num(matching.total.reviewed)} correspondance(s) décidée(s)`} />
      </div>
    </>
  );
}

export function LastCrawlSection(props: { last: OrgInsights["crawls"]["last"] }) {
  const { last } = props;
  return (
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
  );
}

export function FormatsSection(props: { formats: OrgInsights["formats"] }) {
  const { formats } = props;
  const totalFormatFiles = formats.reduce((sum, row) => sum + row.files, 0);
  return (
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
  );
}

export function AnatomySection(props: { site: OrgInsights["site"] }) {
  const { site } = props;
  const dedupRatio = site.image_urls ? 1 - site.distinct_files / site.image_urls : null;
  return (
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
  );
}

function LibrarySection(props: { library: OrgInsights["library"] }) {
  const { library } = props;
  return (
    <Section title="Bibliothèque">
      <Facts
        rows={[
          ["Visuels", num(library.references_total)],
          ["Indexés (CLIP)", `${num(library.references_indexed)} · ${percent(library.references_total ? library.references_indexed / library.references_total : null)}`],
          ["Expirés", num(library.expired)],
          ["Expirent sous 30 / 90 jours", `${num(library.expiring_30_days)} / ${num(library.expiring_90_days)}`],
          ["Sans échéance", num(library.without_expiry)],
          ["Droits illimités", num(library.unlimited_rights)],
          ["Poids moyen d'un visuel", bytes(library.avg_bytes)],
          ["Mégapixels moyens", num(library.avg_megapixels, 1)],
        ]}
      />
    </Section>
  );
}

export function DecisionsAndLibrary(props: { matching: OrgInsights["matching"]; compare: OrgInsights["compare"]; library: OrgInsights["library"] }) {
  return (
    <div className="mt-4 grid gap-4 lg:grid-cols-2">
      <Section title="Correspondances et décisions">
        <MatchingTable matching={props.matching} />
      </Section>
      <div className="grid min-w-0 content-start gap-4">
        <Section title="Comparaison">
          <CompareFacts compare={props.compare} />
        </Section>
        <LibrarySection library={props.library} />
      </div>
    </div>
  );
}
