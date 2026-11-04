import { Link } from "react-router";
import { LoadError, RefreshFailed, Refreshing } from "../components/busy";
import { Bar, LinkButton, PageHeader, Stat } from "../components/ui";
import { plural } from "../lib/format";
import { useOrg } from "../lib/org";
import { useOverview } from "../lib/queries";
import type { Overview } from "../types";
import { Hero, HeroSkeleton } from "./dashboard/Hero";
import { Start } from "./dashboard/Start";
import { Upcoming, UpcomingSkeleton } from "./dashboard/Upcoming";

export function Dashboard() {
  const { link, platformAdmin } = useOrg();
  const overview = useOverview();

  const header = (
    <PageHeader
      eyebrow={today()}
      title="Tableau de bord"
      actions={
        <>
          <LinkButton to={link("reports")}>Rapports</LinkButton>
          {platformAdmin ? <LinkButton to={link("scans")} variant="primary">Relire les sites</LinkButton> : null}
        </>
      }
    />
  );
  if (overview.isLoading) {
    return (
      <div aria-busy="true">
        {header}
        <HeroSkeleton />
        <StatsSkeleton />
        <UpcomingSkeleton />
      </div>
    );
  }
  if (!overview.data) return <LoadError title="Impossible de charger le tableau de bord" error={overview.error} onRetry={() => void overview.refetch()} retrying={overview.isFetching} />;

  const { dashboard, stats, last_crawl: lastCrawl } = overview.data;
  const noLibrary = stats.reference_images === 0;
  const neverCrawled = !lastCrawl;
  if (noLibrary || neverCrawled) return <Start noLibrary={noLibrary} neverCrawled={neverCrawled} />;

  return (
    <Refreshing active={overview.isFetching}>
      {overview.error ? <RefreshFailed error={overview.error} onRetry={() => void overview.refetch()} retrying={overview.isFetching} /> : null}
      {header}
      <Hero overview={overview.data} />
      <Stats dashboard={dashboard} stats={stats} />
      <Upcoming items={dashboard.upcoming} />
    </Refreshing>
  );
}

const todayFormat = new Intl.DateTimeFormat("fr-FR", { weekday: "long", day: "numeric", month: "long" });

function today(): string {
  const text = todayFormat.format(new Date());
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** The first load of `Stats`: the same four cards, in their tints, with a bar in place of every word. */
function StatsSkeleton() {
  const label = <><Bar /><Bar width="60%" /></>;
  return (
    <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4" aria-hidden>
      {(["yellow", "teal", "blush", "mint"] as const).map((tint) => <Stat key={tint} tint={tint} value={<Bar width="4rem" />} label={label} hint={<Bar width="50%" />} />)}
    </div>
  );
}

/** The numbers count every deadline, not a window: the links to "À traiter" ask for all of them (window=3650), whatever window was remembered there. */
function Stats(props: { dashboard: Overview["dashboard"]; stats: Overview["stats"] }) {
  const { link } = useOrg();
  const { dashboard, stats } = props;
  return (
    <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <Stat
        value={dashboard.urgent_online}
        label="expirent sous 30 jours et sont en ligne"
        tint="yellow"
        hint={dashboard.urgent_online ? <Link className="font-medium text-ink underline underline-offset-2" to={`${link("review")}?status=%3C30j&window=3650`}>Anticiper</Link> : undefined}
      />
      <Stat tint="teal" value={dashboard.pending_review} label="occurrences attendent une décision" hint={dashboard.pending_review ? <Link className="font-medium text-ink underline underline-offset-2" to={`${link("review")}?window=3650`}>Les traiter</Link> : "Tout est décidé."} />
      <Stat
        value={dashboard.unreferenced_online}
        label="images en ligne dont les droits n'ont jamais été vérifiés"
        tint="blush"
        hint={
          dashboard.unreferenced_online ? (
            <>
              Certaines sont peut-être expirées.{" "}
              <Link className="font-medium text-ink underline underline-offset-2" to={link("online-images")}>Les vérifier</Link>
            </>
          ) : (
            "Toutes les images en ligne ont des droits vérifiés."
          )
        }
      />
      <Stat tint="mint" value={stats.reference_images} label="visuels sous surveillance" hint={`${plural(stats.site_images, "image lue", "images lues")} sur ${plural(stats.pages_crawled, "page")}`} />
    </div>
  );
}
