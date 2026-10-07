import { Link } from "react-router";
import { Icon } from "../../components/icons";
import { LinkButton, cx } from "../../components/ui";
import { formatDateTime, hostOf, plural } from "../../lib/format";
import { useOrg } from "../../lib/org";
import type { Overview } from "../../types";

/** The number of expired visuals still online, and what the last reading covered. */
export function Hero(props: { overview: Overview }) {
  const { link } = useOrg();
  const { dashboard, stats, last_crawl: lastCrawl } = props.overview;
  const hero = dashboard.expired_online;
  return (
    <section className="grid gap-8 rounded-xl border border-ink bg-paper p-6 md:p-8 lg:grid-cols-[minmax(0,1fr)_300px] lg:items-center">
      <div>
        <span className={cx("inline-flex items-center rounded-full border border-ink px-3 py-0.5 text-xs font-medium", hero ? "bg-yellow" : "bg-mint")}>
          {hero ? "À traiter en priorité" : "Tout est en règle"}
        </span>
        <h2 className="font-display mt-5 text-[40px] leading-[1.15] md:text-[56px]">
          <span className={cx("marker", !hero && "!bg-mint")}>{hero}</span>{" "}
          {hero ? `${hero > 1 ? "visuels expirés sont encore" : "visuel expiré est encore"} en ligne` : "visuel expiré en ligne"}
        </h2>
        <p className="mt-4 max-w-xl text-[15px] leading-relaxed text-ink-soft">
          {hero
            ? "Leurs droits sont échus mais ils apparaissent toujours sur le site. Chaque jour compte."
            : "D'après la dernière lecture. Les faux positifs et les visuels déjà retirés ne sont pas comptés."}
        </p>
        {hero ? (
          // The dashboard counts every deadline, so the list it opens does too (fenetre=3650).
          <LinkButton to={`${link("a-traiter")}?statut=expire&fenetre=3650`} variant="primary" size="lg" className="mt-6">
            Commencer le tri
            <Icon name="arrow" size={16} strokeWidth={2} />
          </LinkButton>
        ) : null}
        {dashboard.unreferenced_online ? (
          <p className="mt-5 max-w-xl border-t border-line pt-4 text-sm text-ink-soft">
            {hero ? "Par ailleurs, " : "Attention : "}
            <Link className="font-medium text-ink underline underline-offset-2" to={link("images-du-site")}>
              {plural(dashboard.unreferenced_online, "image en ligne n'a", "images en ligne n'ont")} jamais eu ses droits vérifiés
            </Link>
            . Certaines sont peut-être expirées : elles ne sont pas comptées ici tant qu'elles ne sont pas dans la bibliothèque.
          </p>
        ) : null}
      </div>
      {lastCrawl ? <LastCrawl lastCrawl={lastCrawl} dashboard={dashboard} stats={stats} /> : null}
    </section>
  );
}

function LastCrawl(props: { lastCrawl: NonNullable<Overview["last_crawl"]>; dashboard: Overview["dashboard"]; stats: Overview["stats"] }) {
  const { lastCrawl, dashboard, stats } = props;
  const readAt = lastCrawl.finished_at ?? lastCrawl.started_at;
  return (
    <div className="rounded-xl border border-ink bg-mint p-5">
      <p className="text-[13px] text-ink-soft">Dernière lecture</p>
      <p className="mt-1 truncate font-mono text-[13px]" title={lastCrawl.site_url}>
        {lastCrawl.site_label ? `${lastCrawl.site_label} · ` : ""}{hostOf(lastCrawl.site_url)}
      </p>
      <p className="text-[13px] text-ink-soft">{formatDateTime(readAt)}</p>
      <dl className="mt-4 grid gap-2 border-t border-ink/25 pt-4 text-sm">
        <Row label="Visuels trouvés en ligne" value={dashboard.references_online} />
        <Row label="Pages lues" value={stats.pages_crawled} />
        <Row label="Images comparées" value={stats.site_images} />
        {stats.references_pending_index ? <Row label="En cours d'indexation" value={stats.references_pending_index} /> : null}
      </dl>
    </div>
  );
}

function Row(props: { label: string; value: number }) {
  return (
    <div className="flex justify-between gap-3">
      <dt className="text-ink-soft">{props.label}</dt>
      <dd className="font-medium tabular">{props.value.toLocaleString("fr-FR")}</dd>
    </div>
  );
}
