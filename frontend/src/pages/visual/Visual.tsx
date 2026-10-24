import { useMemo, useState } from "react";
import { OrgHoldNotice, holdProps } from "../../components/OrgHold";
import { InfoTip } from "../../components/SettingsBlock";
import { Button, DecisionBadge, EmptyState, Skeleton, Thumb } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import { hostOf, plural } from "../../lib/format";
import { useOrg } from "../../lib/org";
import { usePresence, type Narrowing } from "../../lib/presence";
import { useMatches } from "../../lib/queries";
import { SuspectReadNotice, useFullRead } from "../review/Removal";
import { photoRows } from "../review/rows";
import type { MatchGroup } from "../../types";
import { Pages } from "./Pages";
import { Summary } from "./Summary";
import { Versions } from "./Versions";

/** The page of one visual: how many pages still show it, where, and which ones. */
export function Visual(props: { referenceId: string; onBack: () => void }) {
  const { admin } = useOrg();
  const matches = useMatches(3650);
  const [narrowing, setNarrowing] = useState<Narrowing>({});
  const [offset, setOffset] = useState(0);
  const presence = usePresence(props.referenceId, narrowing, offset);
  const read = useFullRead();
  const group = useMemo(() => {
    const data = matches.data;
    return [...(data?.confirmed ?? []), ...(data?.to_verify ?? []), ...(data?.later ?? []), ...(data?.unlimited ?? [])].find((item) => item.reference_id === props.referenceId);
  }, [matches.data, props.referenceId]);
  const narrow = (next: Narrowing) => {
    setNarrowing(next);
    setOffset(0);
  };

  if (matches.isLoading) return <Skeleton className="h-96" />;
  if (!group) return <EmptyState title="Visuel introuvable" body="Il n'a pas de correspondance en ligne, ou il n'appartient pas à cette marque." action={<Button onClick={props.onBack}>Retour</Button>} />;
  return (
    <>
      <Summary group={group} presence={presence.data} onBack={props.onBack} />
      {presence.error ? <p className="mb-4 text-sm text-expired">{errorMessage(presence.error)}</p> : null}
      {presence.data?.versions.filter((version) => version.suspect).map((version) => <SuspectReadNotice key={version.site_id} site={version.label || hostOf(version.site_url)} />)}
      {presence.data ? (
        <>
          <div className="mb-3 flex items-center justify-between gap-3">
            <h2 className="flex items-center gap-2 text-base font-semibold">
              Où il est
              <InfoTip>Pages qui le montrent encore, sur celles où il a été vu, par version du site puis par rubrique. Un clic filtre la liste. Les chiffres viennent de la dernière lecture complète des sites.</InfoTip>
            </h2>
            {admin ? <Button size="sm" variant="primary" loading={read.busy} disabled={Boolean(read.hold)} {...holdProps(read.hold)} onClick={read.read}>Relire les sites</Button> : null}
          </div>
          {admin && read.hold ? <div className="mb-3"><OrgHoldNotice hold={read.hold} /></div> : null}
          <Versions versions={presence.data.versions} narrowing={narrowing} onNarrow={narrow} />
          <h2 className="mt-8 mb-3 text-base font-semibold">Les pages</h2>
          <Pages referenceId={props.referenceId} data={presence.data} narrowing={narrowing} onNarrow={narrow} onOffset={setOffset} />
        </>
      ) : <Skeleton className="h-64" />}
      <h2 className="mt-8 mb-3 text-base font-semibold">Les photos</h2>
      <Photos group={group} />
    </>
  );
}

/** The photos found online for this visual: one row per photo, its files together, and where its decision stands. */
function Photos(props: { group: MatchGroup }) {
  const rows = photoRows([props.group], () => true);
  return (
    <ul className="divide-y divide-line rounded-xl border border-ink bg-paper">
      {rows.map((row) => (
        <li key={row.key} className="flex flex-wrap items-center gap-3 px-4 py-2.5">
          <Thumb src={row.hit.site_thumb} size={46} />
          <span className="min-w-0 flex-1 basis-40">
            <span className="block truncate font-mono text-xs">{row.hit.versions.join(", ")}</span>
            <span className="block truncate text-xs text-muted">{plural(row.hit.file_count, "fichier")}</span>
          </span>
          <DecisionBadge decision={row.hit.decision} />
        </li>
      ))}
    </ul>
  );
}
