import { useInfiniteQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Select } from "./Dropdown";
import { Button, EmptyState, Input, Skeleton } from "./ui";
import { api, errorMessage } from "../lib/api";
import { decisionLabel, formatDateTime, jobLabel, plural } from "../lib/format";
import type { AuditEvent, AuditPage, Decision, JobKind } from "../types";

const PAGE = 50;

/** The stable action codes of the API, in the words of the interface. An unknown code is shown as it is. */
const ACTION_LABEL: Record<string, string> = {
  "library.upload": "Ajout de visuels",
  "library.update": "Modification d'un visuel",
  "library.delete": "Suppression de visuels",
  "library.expiry": "Changement d'échéance",
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
  "organization.create": "Organisation créée",
  "organization.export": "Export des données",
};

const actionLabel = (code: string) => ACTION_LABEL[code] ?? (code.startsWith("job.") ? `Lancement : ${jobLabel[code.slice(4) as JobKind] ?? code.slice(4)}` : code);

/** What the row adds to its title: a decision, a number of items. */
function summary(event: AuditEvent): string {
  const { decision, count, saved, added } = event.detail;
  const parts: string[] = [];
  if (typeof decision === "string") parts.push(decisionLabel[decision as Decision] ?? decision);
  const number = [count, saved, added].find((value) => typeof value === "number");
  if (typeof number === "number") parts.push(plural(number, "élément"));
  return parts.join(" · ");
}

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
      if (since) query.set("since", since);
      return api.get<AuditPage>(`${props.path}?${query.toString()}`);
    },
    getNextPageParam: (last, pages) => (last.has_more ? pages.length * PAGE : undefined),
  });
  const events = log.data?.pages.flatMap((page) => page.events) ?? [];
  return (
    <div>
      <div className="flex flex-wrap items-center gap-2 px-5 py-3">
        <Select className="w-64" aria-label="Type d'action" value={action} onChange={(event) => setAction(event.target.value)}>
          <option value="">Toutes les actions</option>
          {Object.entries(ACTION_LABEL).map(([code, label]) => <option key={code} value={code}>{label}</option>)}
        </Select>
        <Input type="date" aria-label="Depuis le" className="w-44" value={since} onChange={(event) => setSince(event.target.value)} />
      </div>
      {log.isLoading ? <Skeleton className="m-5 h-24" /> : null}
      {log.error ? <EmptyState title="Impossible de charger le journal" body={errorMessage(log.error)} /> : null}
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
            <span className="truncate text-muted">{event.email || "Compte supprimé"}</span>
          </li>
        ))}
      </ul>
      {log.hasNextPage ? (
        <div className="px-5 py-3">
          <Button size="sm" loading={log.isFetchingNextPage} onClick={() => void log.fetchNextPage()}>Voir plus</Button>
        </div>
      ) : null}
    </div>
  );
}
