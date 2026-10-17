import { useState } from "react";
import { Link } from "react-router";
import { Button, EmptyState, PageHeader, Segmented, Skeleton, StatusBadge, Thumb } from "../components/ui";
import { InfoTip } from "../components/SettingsBlock";
import { errorMessage } from "../lib/api";
import { daysText } from "../lib/format";
import { useOrg } from "../lib/org";
import { useVisuals, type VisualItem } from "../lib/presence";
import { useFullRead } from "./review/Removal";

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
  if (visuals.error || !visuals.data) return <EmptyState title="Visuels indisponibles" body={errorMessage(visuals.error)} />;
  const all = [...visuals.data.visuals].sort(byUrgency);
  const todo = all.filter((item) => !item.replaced);
  const shown = tab === "todo" ? todo : tab === "done" ? all.filter((item) => item.replaced) : all;
  return (
    <>
      <PageHeader
        title="Visuels à remplacer"
        actions={admin ? <Button variant="primary" loading={read.busy} onClick={read.read}>Relire les sites</Button> : undefined}
      />
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
        <InfoTip>Les visuels dont une correspondance a été confirmée dans « À traiter ». Un visuel est remplacé quand toutes ses pages ont été relues et qu'aucune ne le montre plus.</InfoTip>
      </div>
      {shown.length ? (
        <ul className="divide-y divide-line rounded-xl border border-ink bg-paper">
          {shown.map((item) => <VisualRow key={item.reference_id} item={item} to={`${link("review")}?window=3650&visual=${item.reference_id}`} />)}
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

function VisualRow(props: { item: VisualItem; to: string }) {
  const { item } = props;
  const unmeasured = item.unknown === item.pages;
  return (
    <li>
      <Link to={props.to} className="flex items-center gap-3 px-4 py-3 hover:bg-sunk">
        <Thumb src={item.thumb} size={46} />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-medium">{item.filename}</span>
          <span className="mt-1 block"><StatusBadge status={item.status} label={daysText(item.days_left)} /></span>
        </span>
        <span className="flex shrink-0 flex-col items-end gap-1 text-right">
          <span className="text-sm font-semibold tabular">{item.replaced ? "Remplacé" : unmeasured ? "Pas encore mesuré" : `${item.still.toLocaleString("fr-FR")} page${item.still > 1 ? "s" : ""}`}</span>
          <span className="text-xs text-muted">{item.replaced ? `${item.pages.toLocaleString("fr-FR")} pages relues` : unmeasured ? "à relire" : `encore en ligne sur ${item.pages.toLocaleString("fr-FR")}`}</span>
          <span aria-hidden className="h-1.5 w-28 overflow-hidden rounded-full bg-sunk">
            <span className="block h-full bg-expired" style={{ width: `${item.pages ? (item.still / item.pages) * 100 : 0}%` }} />
          </span>
        </span>
      </Link>
    </li>
  );
}
