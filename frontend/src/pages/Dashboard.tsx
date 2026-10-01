import { Link } from "react-router";
import { EmptyState, LinkButton, PageHeader, Skeleton, Stat } from "../components/ui";
import { errorMessage } from "../lib/api";
import { plural } from "../lib/format";
import { useOrg } from "../lib/org";
import { useOverview } from "../lib/queries";
import type { Overview } from "../types";
import { Hero } from "./dashboard/Hero";
import { Start } from "./dashboard/Start";
import { Upcoming } from "./dashboard/Upcoming";

export function Dashboard() {
  const { link, admin } = useOrg();
  const overview = useOverview();

  if (overview.isLoading) {
    return (
      <div className="grid gap-4">
        <Skeleton className="h-9 w-64" />
        <Skeleton className="h-36" />
        <div className="grid gap-4 md:grid-cols-3">
          <Skeleton className="h-24" />
          <Skeleton className="h-24" />
          <Skeleton className="h-24" />
        </div>
      </div>
    );
  }
  if (overview.error || !overview.data) return <EmptyState title="Impossible de charger le tableau de bord" body={errorMessage(overview.error)} />;

  const { dashboard, stats, last_crawl: lastCrawl } = overview.data;
  const noLibrary = stats.reference_images === 0;
  const neverCrawled = !lastCrawl;
  if (noLibrary || neverCrawled) return <Start noLibrary={noLibrary} neverCrawled={neverCrawled} />;

  return (
    <>
      <PageHeader
        eyebrow={today()}
        title="Tableau de bord"
        actions={
          <>
            <LinkButton to={link("rapports")}>Rapports</LinkButton>
            {admin ? <LinkButton to={link("lectures")} variant="primary">Relire les sites</LinkButton> : null}
          </>
        }
      />
      <Hero overview={overview.data} />
      <Stats dashboard={dashboard} stats={stats} />
      <Upcoming items={dashboard.upcoming} />
    </>
  );
}

const todayFormat = new Intl.DateTimeFormat("fr-FR", { weekday: "long", day: "numeric", month: "long" });

function today(): string {
  const text = todayFormat.format(new Date());
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function Stats(props: { dashboard: Overview["dashboard"]; stats: Overview["stats"] }) {
  const { link } = useOrg();
  const { dashboard, stats } = props;
  return (
    <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <Stat
        value={dashboard.urgent_online}
        label="expirent sous 30 jours et sont en ligne"
        tone={dashboard.urgent_online ? "warn" : undefined}
        hint={dashboard.urgent_online ? <Link className="text-bark-700 hover:underline" to={`${link("a-traiter")}?statut=%3C30j`}>Anticiper</Link> : undefined}
      />
      <Stat value={dashboard.pending_review} label="occurrences attendent une décision" hint={dashboard.pending_review ? <Link className="text-bark-700 hover:underline" to={link("a-traiter")}>Les traiter</Link> : "Tout est décidé."} />
      <Stat
        value={dashboard.unreferenced_online}
        label="images en ligne dont les droits n'ont jamais été vérifiés"
        tone={dashboard.unreferenced_online ? "warn" : undefined}
        hint={
          dashboard.unreferenced_online ? (
            <>
              Certaines sont peut-être expirées.{" "}
              <Link className="text-bark-700 hover:underline" to={link("images-du-site")}>Les vérifier</Link>
            </>
          ) : (
            "Toutes les images en ligne ont des droits vérifiés."
          )
        }
      />
      <Stat value={stats.reference_images} label="visuels sous surveillance" hint={`${plural(stats.site_images, "image lue", "images lues")} sur ${plural(stats.pages_crawled, "page")}`} />
    </div>
  );
}
