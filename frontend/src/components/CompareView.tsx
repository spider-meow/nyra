import { useState } from "react";
import { Modal, useToast } from "./feedback";
import { Button, ConfidenceBadge, DecisionBadge, FieldLabel, Input, Kbd, StatusBadge, cx } from "./ui";
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
          toast.show({
            tone: "success",
            message: "Image exclue",
            description: data.matches_removed > 1
              ? `${data.matches_removed} correspondances retirées. Vous pouvez la réintégrer depuis les Réglages.`
              : "Elle ne sera plus proposée. Vous pouvez la réintégrer depuis les Réglages.",
          });
          setExcluding(false);
          setReason("");
        },
        onError: (error) => toast.show({ tone: "error", message: "L'image n'a pas été exclue", description: errorMessage(error) }),
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
          <h2 className="truncate font-semibold" title={group.filename}>{group.filename}</h2>
          <p className="mt-1 flex flex-wrap items-center gap-2 text-sm text-muted">
            <StatusBadge status={group.status} />
            <span>{daysText(group.days_left)}</span>
            {group.expiry_date ? <span>· échéance {formatDate(group.expiry_date)}</span> : null}
          </p>
          {group.credit || group.notes ? (
            <p className="mt-1 text-[13px] text-muted">{[group.credit, group.notes].filter(Boolean).join(" · ")}</p>
          ) : null}
        </div>
        <div className="flex rounded-lg border border-line-strong p-0.5 text-[13px]" role="group" aria-label="Affichage">
          {(["side", "overlay"] as const).map((value) => (
            <button
              key={value}
              type="button"
              aria-pressed={mode === value}
              className={cx("rounded-md px-2.5 py-1", mode === value ? "bg-ink text-white" : "text-ink-soft hover:text-ink")}
              onClick={() => setMode(value)}
            >
              {value === "side" ? "Côte à côte" : "Superposition"}
            </button>
          ))}
        </div>
      </div>

      {mode === "side" ? (
        <div className="grid grid-cols-2 gap-3">
          <Figure label="Référence" src={refSrc} onZoom={() => setZoom({ src: refSrc, title: `Référence : ${group.filename}` })} />
          <Figure label={`Sur ${hostOf(hit.site_url)}`} src={siteSrc} onZoom={() => setZoom({ src: siteSrc, title: hit.site_url })} />
        </div>
      ) : (
        <div>
          <div className="relative aspect-[4/3] overflow-hidden rounded-lg border border-line bg-canvas">
            <img src={refSrc} alt="Référence" className="absolute inset-0 h-full w-full object-contain" />
            <img src={siteSrc} alt="Image trouvée" className="absolute inset-0 h-full w-full object-contain" style={{ opacity: mix / 100 }} />
          </div>
          <label className="mt-2 flex items-center gap-3 text-[13px] text-muted">
            Référence
            <input type="range" min={0} max={100} value={mix} onChange={(event) => setMix(Number(event.target.value))} className="flex-1 accent-ink" aria-label="Mélange entre la référence et l'image trouvée" />
            Trouvée
          </label>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2 text-sm">
        <ConfidenceBadge confidence={hit.confidence} />
        <span className="text-muted">{confidenceHelp[hit.confidence]}</span>
      </div>

      <div className="rounded-lg border border-line">
        <p className="border-b border-line px-3 py-2 text-[13px] font-medium">
          Visible sur {hit.page_count} page{hit.page_count > 1 ? "s" : ""}
        </p>
        <ul className="max-h-40 overflow-y-auto px-3 py-2 text-[13px]">
          {hit.pages.map((page) => (
            <li key={page} className="truncate py-0.5">
              <a href={page} target="_blank" rel="noreferrer noopener" className="text-ink-soft underline decoration-line-strong underline-offset-2 hover:text-ink">
                {pathOf(page)}
              </a>
            </li>
          ))}
          {hit.page_count > hit.pages.length ? <li className="py-0.5 text-muted">et {hit.page_count - hit.pages.length} autre(s)</li> : null}
        </ul>
      </div>

      <div className="rounded-lg bg-canvas p-3">
        <div className="flex items-center justify-between gap-2">
          <p className="text-[13px] font-medium">Décision</p>
          <DecisionBadge decision={hit.decision} />
        </div>
        <div className="mt-2 grid grid-cols-3 gap-2">
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
          <dd>{methodLabel(hit.level, hit.confidence)}</dd>
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
            <Button variant="danger" loading={exclusions.add.isPending} onClick={exclude}>Exclure</Button>
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

function Figure(props: { label: string; src: string; onZoom: () => void }) {
  return (
    <figure className="min-w-0">
      <button type="button" onClick={props.onZoom} className="block w-full overflow-hidden rounded-lg border border-line bg-canvas" aria-label={`Agrandir : ${props.label}`}>
        {props.src ? <img src={props.src} alt="" className="aspect-square w-full object-contain" /> : <span className="block aspect-square" />}
      </button>
      <figcaption className="mt-1.5 truncate text-[13px] text-muted">{props.label}</figcaption>
    </figure>
  );
}

function DecisionButton(props: { label: string; shortcut: string; active: boolean; onClick: () => void; disabled?: boolean }) {
  return (
    <Button
      size="sm"
      variant={props.active ? "primary" : "secondary"}
      aria-pressed={props.active}
      disabled={props.disabled}
      onClick={props.onClick}
      className="justify-between"
    >
      {props.label}
      <Kbd>{props.shortcut}</Kbd>
    </Button>
  );
}

function methodLabel(level: string, confidence: string): string {
  if (level === "geo") {
    return confidence === "haut"
      ? "Même photo, vérifiée point par point (recadrage ou retouche possible)"
      : "Élément commun vérifié point par point (même détourage produit, autre composition ?)";
  }
  if (level === "clip") return "Ressemblance visuelle (CLIP), non vérifiée";
  return `Empreinte perceptuelle (${level})`;
}
