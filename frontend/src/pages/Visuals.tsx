import { useState } from "react";
import { Link } from "react-router";
import { OrgHoldNotice, holdProps } from "../components/OrgHold";
import { Button, EmptyState, PageHeader, Segmented, Skeleton, StatusBadge, Thumb } from "../components/ui";
import { InfoTip } from "../components/SettingsBlock";
import { errorMessage } from "../lib/api";
import { daysText, hostOf, plural } from "../lib/format";
import { useOrg } from "../lib/org";
import { useVisuals, type VisualItem } from "../lib/presence";
import { SuspectReadNotice, useFullRead } from "./review/Removal";

type Tab = "todo" | "done" | "all";

/** Soonest expiry first; no expiry last. */
const byUrgency = (a: VisualItem, b: VisualItem) => (a.days_left ?? Infinity) - (b.days_left ?? Infinity) || a.filename.localeCompare(b.filename);

/** The visuals to replace: one line each, soonest expiry first, with how many pages still show it. */
export function Visuals() {
  const { admin, link } = useOrg();
  const visuals = useVisuals();
  const read = useFullRead();
  const [tab, setTab] = useState<Tab>("todo");

  if (visuals.isLoading) return <Skeleton className="h-96" />;
  if (visuals.error || !visuals.data) return <EmptyState title="Visuels indisponibles" body={errorMessage(visuals.error)} action={<Button onClick={() => void visuals.refetch()}>Réessayer</Button>} />;
  const all = [...visuals.data.visuals].sort(byUrgency);
  const todo = all.filter((item) => !item.replaced);
  const shown = tab === "todo" ? todo : tab === "done" ? all.filter((item) => item.replaced) : all;
  return (
    <>
      <PageHeader
        title="Visuels à remplacer"
        actions={admin ? <Button variant="primary" loading={read.busy} disabled={Boolean(read.hold)} {...holdProps(read.hold)} onClick={read.read}>Relire les sites</Button> : undefined}
      />
      {admin && read.hold ? <div className="mb-4"><OrgHoldNotice hold={read.hold} /></div> : null}
      {visuals.data.suspect_sites.map((site) => <SuspectReadNotice key={site.site_id} site={site.label || hostOf(site.site_url)} reason={site.reason} />)}
      <div className="mb-4 flex items-center gap-2">
        <Segmented<Tab>
          label="Visuels"
          value={tab}
          onChange={setTab}
          options={[
            { value: "todo", label: `À remplacer · ${todo.length}` },
            { value: "done", label: `Remplacés · ${all.length - todo.length}` },
            { value: "all", label: `Tous · ${all.length}` },
          ]}
        />
        <InfoTip>Les visuels dont une correspondance a été confirmée dans « À traiter ». Les pages comptées sont celles de toutes ses photos non écartées. Un visuel est remplacé quand toutes ses pages ont été relues et qu'aucune ne le montre plus.</InfoTip>
      </div>
      {shown.length ? (
        <ul className="divide-y divide-line rounded-xl border border-ink bg-paper">
          {shown.map((item) => <VisualRow key={item.reference_id} item={item} concluded={Boolean(visuals.data.redirects_concluded)} to={`${link("visuals")}/${item.reference_id}`} />)}
        </ul>
      ) : (
        <EmptyState
          title={tab === "done" ? "Aucun visuel remplacé pour l'instant" : "Aucun visuel à remplacer"}
          body="Confirmez des correspondances dans « À traiter » : les visuels concernés apparaissent ici."
          action={<Link className="text-sm font-medium underline underline-offset-2" to={`${link("review")}?window=3650`}>Aller à « À traiter »</Link>}
        />
      )}
    </>
  );
}

/** What a visual's line says: the pages still showing it, and the ones not read again (never counted as gone). */
function figures(item: VisualItem): [string, string] {
  const pages = (count: number) => `${count.toLocaleString("fr-FR")} page${count > 1 ? "s" : ""}`;
  const marked = item.marked ? ` · ${plural(item.marked, "marquée remplacée", "marquées remplacées")}` : "";
  if (item.replaced) return ["Remplacé", `${pages(item.pages)} relues${marked}`];
  if (item.still) return [pages(item.still), (item.unknown ? `encore en ligne · ${item.unknown.toLocaleString("fr-FR")} à relire` : `encore en ligne sur ${item.pages.toLocaleString("fr-FR")}`) + marked];
  return item.unknown === item.pages ? ["Pas encore mesuré", `à relire${marked}`] : ["0 page confirmée", `${item.unknown.toLocaleString("fr-FR")} à relire${marked}`];
}

/** The pages sent elsewhere that nothing concluded: said on the line, never a silent block. The home page or a parent page waits for a person once the rule is on. */
function redirectNote(item: VisualItem, concluded: boolean): string {
  const home = item.redirected?.home ?? 0;
  const other = Object.entries(item.redirected ?? {}).reduce((sum, [kind, count]) => sum + (kind === "home" ? 0 : count), 0);
  const parts = [
    home ? `${plural(home, "page renvoyée vers l'accueil", "pages renvoyées vers l'accueil")} : ${concluded ? "à valider sur la page du visuel" : "rien n'est conclu"}` : "",
    other ? `${plural(other, "page renvoyée ailleurs", "pages renvoyées ailleurs")} : rien n'est conclu` : "",
  ];
  return parts.filter(Boolean).join(" · ");
}

function VisualRow(props: { item: VisualItem; to: string; concluded: boolean }) {
  const { item } = props;
  const [title, detail] = figures(item);
  const note = redirectNote(item, props.concluded);
  const share = (count: number) => `${item.pages ? (count / item.pages) * 100 : 0}%`;
  return (
    <li>
      <Link to={props.to} className="flex flex-wrap items-center gap-x-3 gap-y-2 px-4 py-3 hover:bg-sunk">
        <Thumb src={item.thumb} size={46} />
        <span className="min-w-0 flex-1 basis-40">
          <span className="block text-sm font-medium [overflow-wrap:anywhere] sm:truncate">{item.filename}</span>
          <span className="mt-1 block"><StatusBadge status={item.status} label={daysText(item.days_left)} /></span>
        </span>
        <span className="flex w-full shrink-0 flex-col items-start gap-1 sm:w-auto sm:items-end sm:text-right">
          <span className="text-sm font-semibold tabular">{title}</span>
          <span className="text-xs text-muted">{detail}</span>
          {note ? <span className="text-xs text-soon">{note}</span> : null}
          <span aria-hidden className="flex h-1.5 w-28 overflow-hidden rounded-full bg-sunk">
            <span className="bg-expired" style={{ width: share(item.still) }} />
            <span className="bg-line-strong" style={{ width: share(item.unknown) }} />
          </span>
        </span>
      </Link>
    </li>
  );
}
