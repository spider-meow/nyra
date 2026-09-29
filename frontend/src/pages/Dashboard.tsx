import { Link } from "react-router";
import { Icon } from "../components/icons";
import { Button, Card, EmptyState, LinkButton, PageHeader, Skeleton, Stat, StatusBadge, cx } from "../components/ui";
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
          <h2 className="font-display text-3xl">Mise en route</h2>
          <p className="mt-1 text-sm text-muted">Trois étapes pour savoir quels visuels retirer du site.</p>
          <ol className="mt-5 grid gap-4 text-[15px]">
            <Step done={!noLibrary} index={1} title="Déposer les visuels sous droits et leur date d'expiration" to={link("bibliotheque")} cta="Ouvrir la bibliothèque" />
            <Step done={!neverCrawled} index={2} title="Ajouter les sites sur lesquels ils pourraient encore apparaître, puis les lire" to={link("lectures")} cta="Sites et lectures" disabled={noLibrary || !admin} />
            <Step done={false} index={3} title="Traiter les correspondances trouvées" to={link("a-traiter")} cta="Voir les correspondances" disabled={neverCrawled} />
          </ol>
        </Card>
      </>
    );
  }

  const hero = dashboard.expired_online;
  const readAt = lastCrawl ? lastCrawl.finished_at ?? lastCrawl.started_at : null;
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
        {lastCrawl ? (
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
        ) : null}
      </section>

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

      <Card padded={false} className="mt-6 overflow-hidden">
        <div className="flex items-center justify-between border-b border-line px-6 py-4">
          <h2 className="text-[17px] font-semibold tracking-tight">Échéances des 90 prochains jours</h2>
          <Link to={link("bibliotheque")} className="text-[13.5px] text-bark-700 hover:underline">Toute la bibliothèque</Link>
        </div>
        {dashboard.upcoming.length ? (
          <ul className="divide-y divide-side">
            {dashboard.upcoming.map((item) => {
              const [day, month] = dayMonth(item.expiry_date);
              return (
                <li key={item.reference_id} className="flex items-center gap-4 px-6 py-3 text-sm">
                  <span className="flex w-[52px] shrink-0 flex-col items-center rounded-[10px] bg-canvas py-1" aria-label={formatDate(item.expiry_date)}>
                    <span className="text-lg leading-tight font-semibold tabular">{day}</span>
                    <span className="text-[11px] tracking-wide text-muted uppercase">{month}</span>
                  </span>
                  <span className="min-w-0 flex-1 truncate">{item.filename}</span>
                  <StatusBadge status={item.days_left < 0 ? "expire" : item.days_left < 30 ? "<30j" : "<90j"} label={daysText(item.days_left)} />
                  <span className={cx("hidden w-24 shrink-0 text-right text-[13px] sm:block", item.online ? "font-medium text-expired" : "text-muted")}>
                    {item.online ? "En ligne" : "Pas trouvé"}
                  </span>
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="px-6 py-8 text-sm text-muted">Aucune échéance dans les 90 prochains jours.</p>
        )}
      </Card>
    </>
  );
}

const todayFormat = new Intl.DateTimeFormat("fr-FR", { weekday: "long", day: "numeric", month: "long" });
const monthFormat = new Intl.DateTimeFormat("fr-FR", { month: "short" });

function today(): string {
  const text = todayFormat.format(new Date());
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function dayMonth(iso: string): [string, string] {
  const date = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(date.getTime())) return ["·", ""];
  return [String(date.getDate()).padStart(2, "0"), monthFormat.format(date).replace(".", "")];
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
          "grid h-8 w-8 shrink-0 place-items-center rounded-full text-xs font-medium",
          props.done ? "bg-ok-soft text-ok" : "bg-peach-soft text-bark-700",
        )}
        aria-label={props.done ? "Fait" : "À faire"}
      >
        {props.done ? <Icon name="check" size={14} strokeWidth={2.2} /> : props.index}
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
