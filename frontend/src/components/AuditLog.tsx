import { useInfiniteQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Select } from "./Dropdown";
import { Button, EmptyState, Input, Skeleton } from "./ui";
import { api, errorMessage } from "../lib/api";
import { decisionLabel, formatDateTime, jobLabel, plural } from "../lib/format";
import type { AuditEvent, AuditPage } from "../types";

const PAGE = 50;

/** The stable action codes of the API, in the words of the interface. An unknown code is shown as it is. */
const ACTION_LABEL: Record<string, string> = {
  "library.upload": "Ajout de visuels",
  "library.update": "Modification d'un visuel",
  "library.delete": "Suppression de visuels",
  "library.expiry": "Changement d'échéance",
  "library.credit": "Changement de crédit",
  "library.tags": "Changement de tags",
  "library.import_csv": "Import CSV",
  "library.adopt": "Images des sites ajoutées à la bibliothèque",
  "library.collection": "Classement dans une collection",
  "collection.create": "Collection créée",
  "collection.rename": "Collection renommée",
  "collection.move": "Collection déplacée",
  "collection.delete": "Collection supprimée",
  "exclusion.add": "Image ignorée",
  "exclusion.delete": "Image remise dans la comparaison",
  "review.set": "Décision sur une correspondance",
  "visual.replaced": "Pages marquées comme remplacées",
  "settings.update": "Réglages modifiés",
  "logos.setting": "Réglage des logos",
  "logos.keep": "Logo gardé ou mis de côté",
  "brand.create": "Marque créée",
  "brand.update": "Marque renommée",
  "brand.delete": "Marque supprimée",
  "brand.logo_set": "Logo de la marque changé",
  "brand.logo_remove": "Logo de la marque retiré",
  "site.add": "Adresse ajoutée",
  "site.update": "Adresse modifiée",
  "site.delete": "Adresse supprimée",
  "label.create": "Étiquette créée",
  "label.delete": "Étiquette supprimée",
  "label.decide": "Décision sur une étiquette",
  "member.invite": "Personne invitée",
  "member.role": "Rôle modifié",
  "member.remove": "Personne retirée",
  "job.cancel": "Tâche annulée",
  "job.crawl": `Lancement : ${jobLabel.crawl}`,
  "job.check": `Lancement : ${jobLabel.check}`,
  "job.match": `Lancement : ${jobLabel.match}`,
  "job.index": `Lancement : ${jobLabel.index}`,
  "job.report": `Lancement : ${jobLabel.report}`,
  "job.locate": `Lancement : ${jobLabel.locate}`,
  "organization.create": "Organisation créée",
  "quota.set": "Limites d'usage modifiées",
  "organization.export": "Export des données",
  "schedule.run": "Lecture mensuelle automatique",
  "schedule.enable": "Lecture mensuelle activée",
  "schedule.disable": "Lecture mensuelle désactivée",
  "digest.sent": "E-mail mensuel envoyé",
  "digest.skipped": "E-mail mensuel non envoyé",
  "digest.failed": "E-mail mensuel : échec d'envoi",
  "notifications.update": "Préférence d'e-mail modifiée",
  "notifications.staff_update": "Envoi d'e-mail modifié par l'équipe Nyra",
  "email.test": "E-mail de test envoyé",
};

const actionLabel = (code: string) => ACTION_LABEL[code] ?? code;

/** What a decision field says: a review's decision, or what a person did with a label. */
const DECISION_WORD: Record<string, string> = { ...decisionLabel, cleared: "Décision annulée", yes: "Étiquette posée", no: "Étiquette refusée", clear: "Étiquette retirée" };
/** Why a monthly read queued nothing (`reason` of `schedule.run`). */
const RUN_REASON: Record<string, string> = { no_site: "aucune adresse à lire", waiting: "une lecture était déjà prévue" };
/** Why a monthly e-mail was not sent (`reason` of `digest.skipped`). */
const SKIP_REASON: Record<string, string> = { nothing: "rien à signaler", no_admin: "aucun administrateur", no_recipient: "aucun destinataire" };
const ROLE_WORD: Record<string, string> = { admin: "Administrateur", client: "Lecture et validation" };
/** The API refuses an offset past this (`MAX_OFFSET`); the interface stops offering more before that. */
const MAX_OFFSET = 50_000;

/** What the row adds to its title: a decision, a role, a number of items. */
function summary(event: AuditEvent): string {
  const { decision, role, count, replaced, renamed, reason } = event.detail ?? {};
  const parts: string[] = [];
  if (typeof decision === "string") parts.push(DECISION_WORD[decision] ?? decision);
  if (typeof role === "string") parts.push(ROLE_WORD[role] ?? role);
  if (typeof count === "number") parts.push(plural(count, "élément"));
  if (event.action === "schedule.run" && typeof reason === "string" && reason) parts.push(RUN_REASON[reason] ?? reason);
  if (event.action === "digest.skipped" && typeof reason === "string" && reason) parts.push(SKIP_REASON[reason] ?? reason);
  if (replaced === true) parts.push("remplace un visuel du même nom");
  if (renamed === true) parts.push("renommé : le nom était déjà pris");
  return parts.join(" · ");
}

/** Who did it: an account's e-mail; the monthly read and e-mail (no account) are automatic; another operator action is the Nyra team's. */
const automatic = (action: string) => action === "schedule.run" || action.startsWith("digest.");
const author = (event: AuditEvent) => event.email || (event.user_id === null ? (automatic(event.action) ? "Lecture automatique" : "Équipe Nyra") : "Compte supprimé");

/** The activity log of an organization: who did what, newest first. `path` is the API address (admin or back office). */
export function AuditLog(props: { path: string; cacheKey: string }) {
  const [action, setAction] = useState("");
  const [since, setSince] = useState("");
  const log = useInfiniteQuery({
    queryKey: ["audit", props.cacheKey, action, since],
    initialPageParam: 0,
    queryFn: ({ pageParam }) => {
      const query = new URLSearchParams({ limit: String(PAGE), offset: String(pageParam) });
      if (action) query.set("action", action);
      const from = since ? new Date(`${since}T00:00`) : null; // the person's midnight, not the server's
      if (from && !Number.isNaN(from.getTime())) query.set("since", from.toISOString());
      return api.get<AuditPage>(`${props.path}?${query.toString()}`);
    },
    getNextPageParam: (last, pages) => (last.has_more && pages.length * PAGE <= MAX_OFFSET ? pages.length * PAGE : undefined),
  });
  const events = log.data?.pages.flatMap((page) => page.events) ?? [];
  const capped = Boolean(log.data?.pages.at(-1)?.has_more) && !log.hasNextPage;
  return (
    <div aria-busy={log.isFetching}>
      <div className="flex flex-wrap items-center gap-2 px-5 py-3">
        <Select className="w-64" aria-label="Type d'action" value={action} onChange={(event) => setAction(event.target.value)}>
          <option value="">Toutes les actions</option>
          {Object.entries(ACTION_LABEL).map(([code, label]) => <option key={code} value={code}>{label}</option>)}
        </Select>
        <Input type="date" aria-label="Depuis le" className="w-44" value={since} onChange={(event) => setSince(event.target.value)} />
      </div>
      {log.isLoading ? <Skeleton className="m-5 h-24" /> : null}
      {log.error && !log.data ? <EmptyState title="Impossible de charger le journal" body={errorMessage(log.error)} action={<Button size="sm" onClick={() => void log.refetch()}>Réessayer</Button>} /> : null}
      {log.error && log.data ? (
        <p role="alert" className="flex flex-wrap items-center gap-3 px-5 py-2 text-sm text-terracotta">
          {errorMessage(log.error)}
          <Button size="sm" onClick={() => void (log.isFetchNextPageError ? log.fetchNextPage() : log.refetch())}>Réessayer</Button>
        </p>
      ) : null}
      {log.data && !events.length ? <p className="px-5 pb-5 text-sm text-muted">Aucune action enregistrée pour ces critères. Le journal garde 12 mois.</p> : null}
      <ul className="divide-y divide-line">
        {events.map((event) => (
          <li key={event.id} className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 px-5 py-2.5 text-sm">
            <span className="w-32 shrink-0 text-muted">{formatDateTime(event.at)}</span>
            <span className="min-w-0 flex-1">
              <span className="font-medium">{actionLabel(event.action)}</span>
              {summary(event) ? <span className="text-muted"> · {summary(event)}</span> : null}
              {event.brand ? <span className="text-muted"> · {event.brand}</span> : null}
            </span>
            <span className="truncate text-muted" title={author(event)}>{author(event)}</span>
          </li>
        ))}
      </ul>
      {capped ? <p className="px-5 py-3 text-sm text-muted">Affinez par date pour voir la suite.</p> : null}
      {log.hasNextPage ? (
        <div className="px-5 py-3">
          <Button size="sm" loading={log.isFetchingNextPage} onClick={() => void log.fetchNextPage()}>Voir plus</Button>
        </div>
      ) : null}
    </div>
  );
}
