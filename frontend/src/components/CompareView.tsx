import { useState } from "react";
import { Modal } from "./feedback";
import { DecisionPanel } from "./compare/Decision";
import { PageList, TechnicalDetails } from "./compare/Details";
import { ExcludeModal } from "./compare/Exclude";
import { Pictures, type Mode, type Zoom } from "./compare/Pictures";
import { ConfidenceBadge, Segmented, StatusBadge } from "./ui";
import { useOrg } from "../lib/org";
import { confidenceHelp, daysText, formatDate } from "../lib/format";
import type { Decision, Hit, MatchGroup } from "../types";

type Props = {
  group: MatchGroup;
  hit: Hit;
  onDecide: (decision: Decision | "", everywhere?: boolean) => void;
  busy?: boolean;
};

/** The reference and what was found on the site, big enough to judge a crop. */
export function CompareView({ group, hit, onDecide, busy }: Props) {
  const [mode, setMode] = useState<Mode>("side");
  const [zoom, setZoom] = useState<Zoom | null>(null);
  const [excluding, setExcluding] = useState(false);
  const { admin } = useOrg();

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="truncate text-lg font-semibold tracking-tight" title={group.filename}>{group.filename}</h2>
          <p className="mt-1.5 flex flex-wrap items-center gap-2 text-[13px] text-muted">
            <StatusBadge status={group.status} label={daysText(group.days_left)} />
            {group.expiry_date ? <span>Échéance {formatDate(group.expiry_date)}</span> : null}
          </p>
          {group.credit || group.notes ? (
            <p className="mt-1 text-[13px] text-muted">{[group.credit, group.notes].filter(Boolean).join(" · ")}</p>
          ) : null}
        </div>
        <Segmented
          label="Affichage"
          value={mode}
          onChange={setMode}
          options={[
            { value: "side", label: "Côte à côte" },
            { value: "overlay", label: "Superposition" },
          ]}
        />
      </div>

      <Pictures group={group} hit={hit} mode={mode} onZoom={setZoom} />

      <div className="flex flex-wrap items-center gap-2.5 rounded-xl bg-peach-soft px-3.5 py-2.5 text-[13px] text-bark-800">
        <ConfidenceBadge confidence={hit.confidence} />
        <span>{confidenceHelp[hit.confidence]}</span>
      </div>

      <PageList hit={hit} />
      <DecisionPanel hit={hit} others={group.hits.length - 1} busy={busy} onDecide={onDecide} />

      {admin ? (
        <p className="text-[13px] text-muted">
          Logo ou visuel générique que le site réutilise partout ?{" "}
          <button type="button" className="underline underline-offset-2 hover:text-ink" onClick={() => setExcluding(true)}>
            Exclure cette image de toutes les comparaisons
          </button>
        </p>
      ) : null}

      <TechnicalDetails hit={hit} />
      <ExcludeModal open={excluding} onClose={() => setExcluding(false)} siteImageId={hit.site_image_id} />
      <Modal open={zoom !== null} onClose={() => setZoom(null)} title={zoom?.title ?? ""} wide>
        {zoom ? <img src={zoom.src} alt="" className="mx-auto max-h-[75vh] w-auto object-contain" /> : null}
      </Modal>
    </div>
  );
}
