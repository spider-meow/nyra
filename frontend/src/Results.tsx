import { useState } from "react";
import { api, thumb } from "./api";
import type { Decision, Hit, MatchGroup, Matches, NotFoundItem } from "./types";
import { btn, btnGhost, card, field, label } from "./ui";

const statusLabel: Record<string, string> = {
  expire: "Expiré",
  "<30j": "Moins de 30 jours",
  "<90j": "Moins de 90 jours",
  ok: "Dans les délais",
  inconnue: "Date inconnue",
};

const confidenceLabel: Record<string, string> = {
  haut: "Confirmé",
  moyen: "Probable",
  a_verifier: "À vérifier",
};

type Props = {
  matches: Matches | null;
  withinDays: number;
  hideRejected: boolean;
  onWithinDays: (days: number) => void;
  onHideRejected: (value: boolean) => void;
  onReload: () => void;
  onExplore: () => void;
  onBanner: (message: string) => void;
};

function dayText(days: number | null): string {
  if (days === null) return "Date inconnue";
  if (days < 0) return `${Math.abs(days)} j de trop`;
  return `${days} j`;
}

function visibleHits(group: MatchGroup, hideRejected: boolean): Hit[] {
  if (!hideRejected) return group.hits;
  return group.hits.filter((hit) => hit.decision !== "ecarte");
}

export function Results(props: Props) {
  const [shown, setShown] = useState(40);
  const [openKey, setOpenKey] = useState("");
  const [openHit, setOpenHit] = useState("");
  const data = props.matches;
  const confirmed = data?.confirmed ?? [];
  const toVerify = data?.to_verify ?? [];
  const later = data?.later ?? [];
  const notFound = data?.not_found ?? [];

  async function decide(referenceId: number, ids: number[], decision: Decision) {
    try {
      await api("/api/reviews", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reference_id: referenceId, site_image_ids: ids, decision }),
      });
      props.onReload();
    } catch (error) {
      props.onBanner(error instanceof Error ? error.message : "La requête a échoué.");
    }
  }

  function list(title: string, groups: MatchGroup[]) {
    if (!groups.length) return null;
    const visible = groups.slice(0, shown);
    return (
      <div className="mt-6">
        <h3 className="text-lg font-semibold">{title}</h3>
        <div className="mt-2 grid gap-2">
          {visible.map((group) => {
            const hits = visibleHits(group, props.hideRejected);
            if (!hits.length) return null;
            const key = `ref-${group.reference_id}`;
            const late = typeof group.days_left === "number" && group.days_left < 0;
            return (
              <article key={key} className={card}>
                <button type="button" className="grid w-full grid-cols-[40px_minmax(0,1fr)_auto] items-center gap-3 text-left" onClick={() => setOpenKey(openKey === key ? "" : key)}>
                  <img className="h-10 w-10 rounded-lg object-cover" alt="" loading="lazy" src={thumb(group.ref_image, 80)} />
                  <span className="min-w-0">
                    <span className="block truncate text-sm font-medium">{group.filename}</span>
                    <span className={`block text-sm font-semibold ${late ? "text-ember" : ""}`}>{dayText(group.days_left)} · {hits.length} occurrence(s)</span>
                  </span>
                  <span className={`rounded-full border px-2.5 py-1 text-xs ${group.status === "expire" ? "border-transparent text-ember" : "border-line"}`}>
                    {statusLabel[group.status] || group.status}
                  </span>
                </button>
                {openKey === key ? hits.map((hit, index) => {
                  const hitKey = `${key}-${index}`;
                  const shownHit = openHit === hitKey;
                  const ids = hit.site_image_ids?.length ? hit.site_image_ids : [hit.site_image_id];
                  const decision = hit.decision === "ecarte" ? "écarté" : hit.decision === "retenu" ? "retenu" : hit.decision === "traite" ? "traité" : "";
                  return (
                    <div key={hitKey} className="mt-3 pl-12">
                      <div className="flex flex-wrap items-center gap-2">
                        <button type="button" className={btnGhost} onClick={() => setOpenHit(shownHit ? "" : hitKey)}>{shownHit ? "Fermer" : "Voir"}</button>
                        <p className="text-xs text-muted">
                          Score {Math.round((hit.score || 0) * 100)} % · {hit.level}{decision ? ` · ${decision}` : ""} · {hit.page_count || hit.pages.length} page(s)
                        </p>
                        <span className="rounded-full border border-line px-2 py-0.5 text-xs">{confidenceLabel[hit.confidence] || hit.confidence}</span>
                        <button type="button" className={btnGhost} onClick={() => void decide(group.reference_id, ids, "retenu")}>Retenir</button>
                        <button type="button" className={btnGhost} onClick={() => void decide(group.reference_id, ids, "ecarte")}>Écarter</button>
                        <button type="button" className={btnGhost} onClick={() => void decide(group.reference_id, ids, "traite")}>Traité</button>
                      </div>
                      {shownHit ? (
                        <div className="mt-3 flex gap-3">
                          <img className="h-[120px] w-[120px] rounded-lg object-cover" alt="" src={thumb(group.ref_image, 160)} />
                          <img className="h-[120px] w-[120px] rounded-lg object-cover" alt="" src={thumb(hit.site_image, 160)} />
                        </div>
                      ) : null}
                      {shownHit ? (
                        <ul className="mt-2 grid gap-1">
                          {hit.pages.map((url) => (
                            <li key={url}><a className="text-sm underline" href={url} target="_blank" rel="noreferrer">{url}</a></li>
                          ))}
                        </ul>
                      ) : null}
                    </div>
                  );
                }) : null}
              </article>
            );
          })}
        </div>
        {groups.length > shown ? (
          <button type="button" className={`${btnGhost} mt-3`} onClick={() => setShown((value) => value + 40)}>
            Afficher la suite · {shown}/{groups.length}
          </button>
        ) : null}
      </div>
    );
  }

  function notFoundList(title: string, items: NotFoundItem[]) {
    if (!items.length) return null;
    return (
      <div className="mt-6">
        <h3 className="text-lg font-semibold">{title}</h3>
        <p className="mt-1 text-sm text-muted">
          Comparées à tout le site, sans résultat. Une ligne "pas encore comparée" n'a pas été vérifiée — ce n'est pas une confirmation d'absence.
        </p>
        <div className="mt-2 grid gap-2">
          {items.map((item) => (
            <article key={`nf-${item.reference_id}`} className={card}>
              <div className="grid grid-cols-[40px_minmax(0,1fr)_auto] items-center gap-3">
                <img className="h-10 w-10 rounded-lg object-cover" alt="" loading="lazy" src={thumb(item.ref_image, 80)} />
                <span className="min-w-0">
                  <span className="block truncate text-sm font-medium">{item.filename}</span>
                  <span className="block text-sm text-muted">{dayText(item.days_left)}</span>
                </span>
                <span className={`rounded-full border px-2.5 py-1 text-xs ${item.compared ? "border-line" : "border-line text-muted italic"}`}>
                  {item.compared ? "Rien trouvé" : "Pas encore comparée"}
                </span>
              </div>
            </article>
          ))}
        </div>
      </div>
    );
  }

  return (
    <section>
      <h2 className="text-4xl font-semibold tracking-tight">Ce qui reste en ligne</h2>
      <p className="mt-2 text-muted">Une ligne par visuel. Ouvre la ligne pour voir les deux images, en petit.</p>
      <div className="mt-4 flex flex-wrap items-end gap-3">
        <label>
          <span className={label}>Montrer jusqu'à</span>
          <input className={field} type="number" min={0} max={3650} value={props.withinDays} onChange={(event) => props.onWithinDays(Number(event.target.value))} />
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={props.hideRejected} onChange={(event) => props.onHideRejected(event.target.checked)} />
          Masquer les écartés
        </label>
        <button type="button" className={btnGhost} onClick={props.onReload}>Actualiser</button>
        <a className={btn} href={`/api/downloads/report.html?within_days=${encodeURIComponent(String(props.withinDays))}`}>Rapport</a>
        <a className={btnGhost} href={`/api/downloads/matches.csv?within_days=${encodeURIComponent(String(props.withinDays))}`}>CSV</a>
        <a className={btnGhost} href={`/api/downloads/not_found.csv?within_days=${encodeURIComponent(String(props.withinDays))}`}>CSV non trouvées</a>
      </div>
      {!data ? <p className="mt-6 text-sm text-muted">Les correspondances arrivent.</p> : null}
      {data ? list(`${confirmed.length} dans la fenêtre`, confirmed) : null}
      {data && confirmed.length === 0 ? (
        <p className="mt-4 text-sm text-muted">
          {later.length ? `Rien dans les ${props.withinDays} jours. Le reste est listé plus bas.` : "Aucune correspondance pour l'instant."}
        </p>
      ) : null}
      {data && confirmed.length === 0 && later.length === 0 && notFound.length === 0 ? (
        <button type="button" className={`${btnGhost} mt-3`} onClick={props.onExplore}>Retour au site</button>
      ) : null}
      {data ? list(`${toVerify.length} à regarder de près`, toVerify) : null}
      {data ? notFoundList(`${notFound.length} non trouvées`, notFound) : null}
      {data ? list(`${later.length} plus loin`, later) : null}
    </section>
  );
}
