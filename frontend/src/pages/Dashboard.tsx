import { Link } from "react-router";
import { Button, Card, EmptyState, LinkButton, PageHeader, Skeleton, Stat, cx } from "../components/ui";
import { errorMessage } from "../lib/api";
import { daysText, formatDate, formatDateTime, hostOf, plural } from "../lib/format";
import { useOrg } from "../lib/org";
import { useOverview } from "../lib/queries";

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

  if (noLibrary || neverCrawled) {
    return (
      <>
        <PageHeader title="Tableau de bord" description="Les visuels dont les droits expirent, et ceux qui sont encore en ligne." />
        <Card>
          <h2 className="font-semibold">Mise en route</h2>
          <ol className="mt-4 grid gap-3 text-sm">
            <Step done={!noLibrary} index={1} title="Déposer les visuels sous droits et leur date d'expiration" to={link("bibliotheque")} cta="Ouvrir la bibliothèque" />
            <Step done={!neverCrawled} index={2} title="Ajouter les sites sur lesquels ils pourraient encore apparaître, puis les lire" to={link("lectures")} cta="Sites et lectures" disabled={noLibrary || !admin} />
            <Step done={false} index={3} title="Traiter les correspondances trouvées" to={link("a-traiter")} cta="Voir les correspondances" disabled={neverCrawled} />
          </ol>
        </Card>
      </>
    );
  }

  const hero = dashboard.expired_online;
  return (
    <>
      <PageHeader
        title="Tableau de bord"
        description={lastCrawl ? <>Dernière lecture de {lastCrawl.site_label || hostOf(lastCrawl.site_url)} le {formatDateTime(lastCrawl.finished_at ?? lastCrawl.started_at)}.</> : null}
        actions={<LinkButton to={link("rapports")}>Rapports</LinkButton>}
      />

      <section
        className={cx(
          "rounded-xl border p-6",
          hero ? "border-expired/25 bg-expired-soft" : "border-line bg-paper",
        )}
      >
        <p className={cx("text-5xl font-semibold tracking-tight tabular", hero ? "text-expired" : "text-ok")}>{hero}</p>
        <p className="mt-2 text-lg font-medium">
          {hero ? `${hero > 1 ? "visuels expirés sont" : "visuel expiré est"} encore en ligne` : "Aucun visuel expiré n'est en ligne"}
        </p>
        <p className="mt-1 text-sm text-ink-soft">
          {hero
            ? "Leurs droits sont échus mais ils apparaissent toujours sur le site. Chaque jour compte."
            : "D'après la dernière lecture. Les faux positifs et les visuels déjà retirés ne sont pas comptés."}
        </p>
        {hero ? (
          <LinkButton to={`${link("a-traiter")}?statut=expire`} variant="primary" className="mt-4">
            Voir les visuels expirés
          </LinkButton>
        ) : null}
        {dashboard.unreferenced_online ? (
          <p className="mt-4 border-t border-line/70 pt-3 text-sm text-ink-soft">
            {hero ? "Par ailleurs, " : "Attention : "}
            <Link className="font-medium text-urgent underline" to={link("images-du-site")}>
              {plural(dashboard.unreferenced_online, "image en ligne n'a", "images en ligne n'ont")} jamais eu ses droits vérifiés
            </Link>
            . Certaines sont peut-être expirées : elles ne sont pas comptées ici tant qu'elles ne sont pas dans la bibliothèque.
          </p>
        ) : null}
      </section>

      <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat value={dashboard.urgent_online} label="expirent sous 30 jours et sont en ligne" tone={dashboard.urgent_online ? "warn" : undefined} />
        <Stat value={dashboard.pending_review} label="visuels trouvés sans décision" hint={dashboard.pending_review ? <Link className="underline" to={link("a-traiter")}>Les traiter</Link> : "Tout est décidé."} />
        <Stat
          value={dashboard.unreferenced_online}
          label="images en ligne dont les droits n'ont jamais été vérifiés"
          tone={dashboard.unreferenced_online ? "warn" : undefined}
          hint={
            dashboard.unreferenced_online ? (
              <>
                Certaines sont peut-être expirées : ajoutez-les à la bibliothèque avec leur échéance, ou ignorez-les si elles ne sont pas sous droits.{" "}
                <Link className="underline" to={link("images-du-site")}>Les vérifier</Link>
              </>
            ) : (
              "Toutes les images en ligne ont des droits vérifiés."
            )
          }
        />
        <Stat value={stats.reference_images} label="visuels sous surveillance" hint={`${plural(stats.site_images, "image lue", "images lues")} sur ${plural(stats.pages_crawled, "page")}`} />
      </div>

      <div className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
        <Card padded={false}>
          <div className="flex items-center justify-between border-b border-line px-5 py-3.5">
            <h2 className="font-semibold">Échéances des 90 prochains jours</h2>
            <Link to={link("bibliotheque")} className="text-sm text-muted hover:text-ink">Bibliothèque</Link>
          </div>
          {dashboard.upcoming.length ? (
            <ul className="divide-y divide-line">
              {dashboard.upcoming.map((item) => (
                <li key={item.reference_id} className="flex items-center gap-3 px-5 py-2.5 text-sm">
                  <span className="w-24 shrink-0 text-muted tabular">{formatDate(item.expiry_date)}</span>
                  <span className="min-w-0 flex-1 truncate">{item.filename}</span>
                  <span className={cx("shrink-0 text-xs", item.days_left < 30 ? "text-urgent" : "text-muted")}>{daysText(item.days_left)}</span>
                  <span className={cx("w-24 shrink-0 text-right text-xs", item.online ? "font-medium text-expired" : "text-muted")}>
                    {item.online ? "En ligne" : "Pas trouvé"}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="px-5 py-6 text-sm text-muted">Aucune échéance dans les 90 prochains jours.</p>
          )}
        </Card>

        <Card>
          <h2 className="font-semibold">Couverture</h2>
          <dl className="mt-3 grid gap-2 text-sm">
            <Row label="Visuels trouvés en ligne" value={dashboard.references_online} />
            <Row label="Pages lues" value={stats.pages_crawled} />
            <Row label="Images comparées" value={stats.site_images} />
            {stats.references_pending_index ? <Row label="Visuels en cours d'indexation" value={stats.references_pending_index} /> : null}
          </dl>
          {admin ? (
            <LinkButton to={link("lectures")} className="mt-4 w-full">
              Relire les sites
            </LinkButton>
          ) : null}
        </Card>
      </div>
    </>
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

function Step(props: { index: number; title: string; done: boolean; to: string; cta: string; disabled?: boolean }) {
  return (
    <li className="flex flex-wrap items-center gap-3">
      <span
        className={cx(
          "grid h-7 w-7 shrink-0 place-items-center rounded-full text-xs font-medium",
          props.done ? "bg-ok-soft text-ok" : "border border-line-strong text-muted",
        )}
        aria-label={props.done ? "Fait" : "À faire"}
      >
        {props.done ? "✓" : props.index}
      </span>
      <span className={cx("min-w-0 flex-1", props.done && "text-muted line-through decoration-1")}>{props.title}</span>
      {!props.done ? (
        props.disabled ? (
          <Button size="sm" disabled>{props.cta}</Button>
        ) : (
          <LinkButton to={props.to} size="sm" variant="primary">{props.cta}</LinkButton>
        )
      ) : null}
    </li>
  );
}
