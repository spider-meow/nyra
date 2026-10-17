import { InfoTip } from "../../components/SettingsBlock";
import { Button, StatusBadge, Thumb } from "../../components/ui";
import { daysText, plural } from "../../lib/format";
import type { Presence } from "../../lib/presence";
import type { MatchGroup } from "../../types";

/** The visual, and how many pages still show it at the last read. */
export function Summary(props: { group: MatchGroup; presence: Presence | undefined; onBack: () => void }) {
  const { group, presence } = props;
  return (
    <div className="mb-6">
      <Button size="sm" variant="ghost" onClick={props.onBack}>← Retour</Button>
      <div className="mt-3 flex flex-wrap items-center gap-4">
        <Thumb src={group.ref_thumb} size={72} />
        <div className="min-w-0 flex-1 basis-64">
          <h1 className="font-display text-xl leading-tight sm:text-3xl [overflow-wrap:anywhere]">{group.filename}</h1>
          <div className="mt-1.5"><StatusBadge status={group.status} label={daysText(group.days_left)} /></div>
        </div>
        {presence ? <Headline totals={presence.totals} /> : null}
      </div>
    </div>
  );
}

function Headline(props: { totals: Presence["totals"] }) {
  const { still, gone, unknown, marked, pages } = props.totals;
  const share = (count: number) => `${(count / Math.max(pages, 1)) * 100}%`;
  // Pages not read since are not "gone": with none read, there is no figure yet.
  const unmeasured = unknown === pages;
  return (
    <div className="min-w-64 max-w-sm rounded-xl border border-line bg-sunk px-4 py-3">
      <p className="flex items-center gap-2 text-2xl font-semibold tabular">
        {unmeasured ? "Pas encore mesuré" : plural(still, "page")}
        <InfoTip>
          {unmeasured
            ? `Aucune des ${pages.toLocaleString("fr-FR")} pages où il a été vu n'a été relue depuis que le suivi existe. Une page pas relue n'est jamais comptée comme retirée.`
            : "Pages qui le montrent encore à la dernière lecture complète, sur celles où il a été vu. Une page pas relue depuis n'est jamais comptée comme retirée."}
        </InfoTip>
      </p>
      <p className="text-[13px] text-muted">
        {unmeasured ? "Lancez une lecture complète" : unknown && !still ? `confirmée en ligne · ${unknown.toLocaleString("fr-FR")} à relire` : `encore en ligne, sur ${pages.toLocaleString("fr-FR")}`}
      </p>
      <div aria-hidden className="mt-2 flex h-1.5 overflow-hidden rounded-full bg-line">
        <span className="bg-expired" style={{ width: share(still) }} />
        <span className="bg-line-strong" style={{ width: share(unknown) }} />
      </div>
      <p className="mt-1.5 text-xs text-muted">{plural(marked, "marquée remplacée", "marquées remplacées")} · {plural(gone, "retirée")} · {plural(unknown, "pas relue")}</p>
    </div>
  );
}
