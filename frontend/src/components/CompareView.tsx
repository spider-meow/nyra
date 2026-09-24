import { useState } from "react";
import { Modal, useToast } from "./feedback";
import { Button, ConfidenceBadge, DecisionBadge, FieldLabel, Input, Segmented, StatusBadge, cx } from "./ui";
import { errorMessage } from "../lib/api";
import { useOrg } from "../lib/org";
import { useExclusionMutations } from "../lib/queries";
import { confidenceHelp, daysText, formatDate, hostOf, pathOf } from "../lib/format";
import type { Decision, Hit, MatchGroup } from "../types";

type Props = {
  group: MatchGroup;
  hit: Hit;
  onDecide: (decision: Decision | "", everywhere?: boolean) => void;
  busy?: boolean;
};

/** The reference and what was found on the site, big enough to judge a crop. */
export function CompareView({ group, hit, onDecide, busy }: Props) {
  const [mode, setMode] = useState<"side" | "overlay">("side");
  const [mix, setMix] = useState(50);
  const [zoom, setZoom] = useState<{ src: string; title: string } | null>(null);
  const [excluding, setExcluding] = useState(false);
  const [reason, setReason] = useState("");
  const { admin } = useOrg();
  const exclusions = useExclusionMutations();
  const toast = useToast();

  function exclude() {
    exclusions.add.mutate(
      { siteImageId: hit.site_image_id, reason },
      {
        onSuccess: (data) => {
          toast(`Image exclue. ${data.matches_removed} correspondance(s) retirée(s).`, "success");
          setExcluding(false);
          setReason("");
        },
        onError: (error) => toast(errorMessage(error), "error"),
      },
    );
  }
  const refSrc = group.ref_image || group.ref_thumb;
  const siteSrc = hit.site_image || hit.site_thumb;
  const others = group.hits.length - 1;

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

      {mode === "side" ? (
        <div className="grid grid-cols-2 gap-3">
          <Figure label="Référence" src={refSrc} onZoom={() => setZoom({ src: refSrc, title: `Référence : ${group.filename}` })} />
          <Figure found label={`Sur ${hostOf(hit.site_url)}`} src={siteSrc} onZoom={() => setZoom({ src: siteSrc, title: hit.site_url })} />
        </div>
      ) : (
        <div>
          <div className="relative aspect-[4/3] overflow-hidden rounded-2xl bg-side">
            <img src={refSrc} alt="Référence" className="absolute inset-0 h-full w-full object-contain" />
            <img src={siteSrc} alt="Image trouvée" className="absolute inset-0 h-full w-full object-contain" style={{ opacity: mix / 100 }} />
          </div>
          <label className="mt-2 flex items-center gap-3 text-[13px] text-muted">
            Référence
            <input type="range" min={0} max={100} value={mix} onChange={(event) => setMix(Number(event.target.value))} className="flex-1 accent-bark" aria-label="Mélange entre la référence et l'image trouvée" />
            Trouvée
          </label>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2.5 rounded-xl bg-peach-soft px-3.5 py-2.5 text-[13px] text-bark-800">
        <ConfidenceBadge confidence={hit.confidence} />
        <span>{confidenceHelp[hit.confidence]}</span>
      </div>

      <div className="rounded-xl border border-line">
        <p className="border-b border-line px-3.5 py-2.5 text-[13px] font-semibold">
          Visible sur {hit.page_count} page{hit.page_count > 1 ? "s" : ""}
        </p>
        <ul className="max-h-40 overflow-y-auto px-3.5 py-2 font-mono text-xs">
          {hit.pages.map((page) => (
            <li key={page} className="truncate py-0.5">
              <a href={page} target="_blank" rel="noreferrer noopener" className="text-bark-700 hover:text-bark-800 hover:underline">
                {pathOf(page)}
              </a>
            </li>
          ))}
          {hit.page_count > hit.pages.length ? <li className="py-0.5 text-muted">et {hit.page_count - hit.pages.length} autre(s)</li> : null}
        </ul>
      </div>

      <div className="rounded-2xl bg-sunk p-3.5">
        <div className="flex items-center justify-between gap-2">
          <p className="text-[13px] font-semibold">Décision</p>
          <DecisionBadge decision={hit.decision} />
        </div>
        <div className="mt-3 grid grid-cols-3 gap-2">
          <DecisionButton label="À retirer" shortcut="R" active={hit.decision === "retenu"} disabled={busy} onClick={() => onDecide(hit.decision === "retenu" ? "" : "retenu")} />
          <DecisionButton label="Faux positif" shortcut="F" active={hit.decision === "ecarte"} disabled={busy} onClick={() => onDecide(hit.decision === "ecarte" ? "" : "ecarte")} />
          <DecisionButton label="Retiré" shortcut="T" active={hit.decision === "traite"} disabled={busy} onClick={() => onDecide(hit.decision === "traite" ? "" : "traite")} />
        </div>
        {others > 0 ? (
          <div className="mt-3 flex flex-wrap items-center gap-2 text-[13px] text-muted">
            <span>{others} autre{others > 1 ? "s" : ""} occurrence{others > 1 ? "s" : ""} de ce visuel :</span>
            <button type="button" className="underline underline-offset-2 hover:text-ink" disabled={busy} onClick={() => onDecide("retenu", true)}>tout à retirer</button>
            <button type="button" className="underline underline-offset-2 hover:text-ink" disabled={busy} onClick={() => onDecide("ecarte", true)}>tout en faux positif</button>
          </div>
        ) : null}
      </div>

      {admin ? (
        <p className="text-[13px] text-muted">
          Logo ou visuel générique que le site réutilise partout ?{" "}
          <button type="button" className="underline underline-offset-2 hover:text-ink" onClick={() => setExcluding(true)}>
            Exclure cette image de toutes les comparaisons
          </button>
        </p>
      ) : null}

      <details className="text-[13px] text-muted">
        <summary className="cursor-pointer select-none">Détails techniques</summary>
        <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
          <dt>Adresse de l'image</dt>
          <dd className="break-all"><a className="underline" href={hit.site_url} target="_blank" rel="noreferrer noopener">{hit.site_url}</a></dd>
          <dt>Méthode</dt>
          <dd>{hit.level === "clip" ? "Similarité visuelle (CLIP)" : `Empreinte perceptuelle (${hit.level})`}</dd>
          <dt>Score</dt>
          <dd className="tabular">{Math.round(hit.score * 100)} %</dd>
          <dt>Variantes</dt>
          <dd>{hit.site_image_ids.length} adresse(s) servant le même fichier</dd>
        </dl>
      </details>

      <Modal
        open={excluding}
        onClose={() => setExcluding(false)}
        title="Exclure cette image"
        footer={
          <>
            <Button onClick={() => setExcluding(false)}>Annuler</Button>
            <Button variant="danger" disabled={exclusions.add.isPending} onClick={exclude}>Exclure</Button>
          </>
        }
      >
        <p className="text-sm text-ink-soft">
          Cette image, et ses copies redimensionnées ou recompressées, ne seront plus jamais proposées, pour aucun visuel.
          Ses correspondances actuelles disparaissent. Vous pourrez la réintégrer depuis les Réglages.
        </p>
        <div className="mt-4">
          <FieldLabel htmlFor="exclusion-reason" hint="facultatif">Raison</FieldLabel>
          <Input id="exclusion-reason" data-autofocus placeholder="Logo du site, pictogramme…" value={reason} onChange={(event) => setReason(event.target.value)} />
        </div>
      </Modal>

      <Modal open={zoom !== null} onClose={() => setZoom(null)} title={zoom?.title ?? ""} wide>
        {zoom ? <img src={zoom.src} alt="" className="mx-auto max-h-[75vh] w-auto object-contain" /> : null}
      </Modal>
    </div>
  );
}

function Figure(props: { label: string; src: string; onZoom: () => void; found?: boolean }) {
  return (
    <figure className="min-w-0">
      <button
        type="button"
        onClick={props.onZoom}
        className={cx("block w-full overflow-hidden rounded-2xl bg-side", props.found && "ring-3 ring-peach")}
        aria-label={`Agrandir : ${props.label}`}
      >
        {props.src ? <img src={props.src} alt="" className="aspect-square w-full object-contain" /> : <span className="block aspect-square" />}
      </button>
      <figcaption className="mt-2 truncate text-[13px] font-medium">{props.label}</figcaption>
    </figure>
  );
}

function DecisionButton(props: { label: string; shortcut: string; active: boolean; onClick: () => void; disabled?: boolean }) {
  return (
    <button
      type="button"
      aria-pressed={props.active}
      disabled={props.disabled}
      onClick={props.onClick}
      className={cx(
        "flex h-14 flex-col items-center justify-center gap-0.5 rounded-[14px] border text-[13.5px] font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-40",
        props.active ? "border-ink bg-ink text-paper" : "border-line-strong bg-paper text-ink hover:border-faint",
      )}
    >
      {props.label}
      <span className={cx("text-[11px] font-normal", props.active ? "text-paper/60" : "text-muted")} aria-hidden>
        {props.shortcut}
      </span>
    </button>
  );
}
