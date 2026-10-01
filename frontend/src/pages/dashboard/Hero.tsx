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
    <section className="grid gap-8 rounded-[20px] border border-line bg-paper p-6 md:p-8 lg:grid-cols-[minmax(0,1fr)_300px] lg:items-center">
      <div>
        {hero ? (
          <span className="inline-flex items-center gap-1.5 rounded-full bg-expired-soft px-2.5 py-0.5 text-xs font-medium text-expired">
            <span className="h-1.5 w-1.5 rounded-full bg-expired" aria-hidden />À traiter en priorité
          </span>
        ) : (
          <span className="inline-flex items-center gap-1.5 rounded-full bg-ok-soft px-2.5 py-0.5 text-xs font-medium text-ok">
            <span className="h-1.5 w-1.5 rounded-full bg-ok" aria-hidden />Tout est en règle
          </span>
        )}
        <div className="mt-4 flex flex-wrap items-baseline gap-x-5 gap-y-2">
          <p className={cx("font-display text-[96px] leading-[0.85] tabular md:text-[112px]", hero ? "text-expired" : "text-ok")}>{hero}</p>
          <p className="font-display max-w-sm text-3xl leading-tight md:text-[34px]">
            {hero ? `${hero > 1 ? "visuels expirés sont" : "visuel expiré est"} encore en ligne` : "visuel expiré en ligne"}
          </p>
        </div>
        <p className="mt-4 max-w-xl text-[15px] leading-relaxed text-ink-soft">
          {hero
            ? "Leurs droits sont échus mais ils apparaissent toujours sur le site. Chaque jour compte."
            : "D'après la dernière lecture. Les faux positifs et les visuels déjà retirés ne sont pas comptés."}
        </p>
        {hero ? (
          <LinkButton to={`${link("a-traiter")}?statut=expire`} variant="primary" size="lg" className="mt-6">
            Commencer le tri
            <Icon name="arrow" size={16} strokeWidth={2} />
          </LinkButton>
        ) : null}
        {dashboard.unreferenced_online ? (
          <p className="mt-5 max-w-xl border-t border-line pt-4 text-sm text-ink-soft">
            {hero ? "Par ailleurs, " : "Attention : "}
            <Link className="font-medium text-urgent underline" to={link("images-du-site")}>
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
    <div className="rounded-2xl bg-sunk p-5">
      <p className="text-[13px] text-muted">Dernière lecture</p>
      <p className="mt-1 truncate font-mono text-[13px]" title={lastCrawl.site_url}>
        {lastCrawl.site_label ? `${lastCrawl.site_label} · ` : ""}{hostOf(lastCrawl.site_url)}
      </p>
      <p className="text-[13px] text-muted">{formatDateTime(readAt)}</p>
      <dl className="mt-4 grid gap-2 border-t border-line pt-4 text-sm">
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
      <dt className="text-muted">{props.label}</dt>
      <dd className="font-medium tabular">{props.value.toLocaleString("fr-FR")}</dd>
    </div>
  );
}
