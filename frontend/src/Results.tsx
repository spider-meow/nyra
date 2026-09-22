import { useState } from "react";
import { api, thumb } from "./api";
import { CompareModal } from "./CompareModal";
import type { Decision, Hit, MatchGroup, MatchStatus, Matches, NotFoundItem } from "./types";
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

const decisionLabel: Record<string, string> = {
  retenu: "retenue",
  ecarte: "écartée",
  traite: "traitée",
};

const matchStatusLabel: Record<MatchStatus, string> = {
  pending: "En attente",
  confirmed: "Confirmée",
  rejected: "Rejetée",
};

const REPORT_STATUS_OPTIONS: { value: string; label: string }[] = [
  { value: "pending", label: "En attente (par défaut)" },
  { value: "confirmed", label: "Confirmées" },
  { value: "rejected", label: "Rejetées" },
  { value: "all", label: "Toutes" },
];

type ConfidenceFilter = "all" | "haut" | "moyen" | "a_verifier";

type UrgencyZone = { key: string; title: string; colorClasses: string; badgeClasses: string; defaultOpen: boolean };

const URGENCY_ZONES: UrgencyZone[] = [
  { key: "expire", title: "Expiré", colorClasses: "border-red-200 bg-red-50", badgeClasses: "border-red-300 bg-red-100 text-red-700", defaultOpen: true },
  { key: "<30j", title: "Moins de 30 jours", colorClasses: "border-orange-200 bg-orange-50", badgeClasses: "border-orange-300 bg-orange-100 text-orange-700", defaultOpen: true },
  { key: "<90j", title: "Moins de 90 jours", colorClasses: "border-amber-200 bg-amber-50", badgeClasses: "border-amber-300 bg-amber-100 text-amber-700", defaultOpen: false },
];
const RESIDUAL_ZONE: UrgencyZone = { key: "rest", title: "Dans les délais", colorClasses: "border-line bg-paper", badgeClasses: "border-line", defaultOpen: false };

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

function visibleHits(group: MatchGroup, hideRejected: boolean, confidenceFilter: ConfidenceFilter): Hit[] {
  return group.hits.filter((hit) => {
    if (hideRejected && hit.decision === "ecarte") return false;
    if (confidenceFilter !== "all" && hit.confidence !== confidenceFilter) return false;
    return true;
  });
}

function decisionSummary(hits: Hit[]): string {
  const counts: Record<string, number> = {};
  for (const hit of hits) {
    if (!hit.decision) continue;
    counts[hit.decision] = (counts[hit.decision] || 0) + 1;
  }
  const parts = Object.entries(counts).map(([decision, count]) => `${count} ${decisionLabel[decision] || decision}`);
  return parts.join(" · ");
}

function allSiteImageIds(hits: Hit[]): number[] {
  return hits.flatMap((hit) => (hit.site_image_ids?.length ? hit.site_image_ids : [hit.site_image_id]));
}

function zoneFor(group: MatchGroup): UrgencyZone {
  return URGENCY_ZONES.find((zone) => zone.key === group.status) || RESIDUAL_ZONE;
}

export function Results(props: Props) {
  const [shown, setShown] = useState(40);
  const [openKey, setOpenKey] = useState("");
  const [openHit, setOpenHit] = useState("");
  const [query, setQuery] = useState("");
  const [confidenceFilter, setConfidenceFilter] = useState<ConfidenceFilter>("all");
  const [openZones, setOpenZones] = useState<Record<string, boolean>>(
    Object.fromEntries([...URGENCY_ZONES, RESIDUAL_ZONE].map((zone) => [zone.key, zone.defaultOpen])),
  );
  const [modal, setModal] = useState<{ group: MatchGroup; hit: Hit } | null>(null);
  const [reportStatus, setReportStatus] = useState("pending");
  const data = props.matches;
  const confirmed = data?.confirmed ?? [];
  const toVerify = data?.to_verify ?? [];
  const later = data?.later ?? [];
  const notFound = data?.not_found ?? [];
  const needle = query.trim().toLowerCase();

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

  async function setMatchStatus(matchId: number, status: "confirmed" | "rejected", note?: string) {
    try {
      await api(`/api/matches/${matchId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status, note }),
      });
      setModal(null);
      props.onReload();
    } catch (error) {
      props.onBanner(error instanceof Error ? error.message : "La requête a échoué.");
    }
  }

  async function excludeMatch(matchId: number, reason?: string) {
    try {
      await api("/api/exclude", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ match_id: matchId, reason }),
      });
      setModal(null);
      props.onReload();
    } catch (error) {
      props.onBanner(error instanceof Error ? error.message : "La requête a échoué.");
    }
  }

  function toggleZone(key: string) {
    setOpenZones((prev) => ({ ...prev, [key]: !prev[key] }));
  }

  function renderGroup(group: MatchGroup) {
    const hits = visibleHits(group, props.hideRejected, confidenceFilter);
    if (!hits.length) return null;
    const key = `ref-${group.reference_id}`;
    const late = typeof group.days_left === "number" && group.days_left < 0;
    const summary = decisionSummary(group.hits);
    const ids = allSiteImageIds(hits);
    return (
      <article key={key} className={card}>
        <div className="grid grid-cols-[40px_minmax(0,1fr)_auto] items-center gap-3">
          <button type="button" className="contents text-left" onClick={() => setOpenKey(openKey === key ? "" : key)}>
            <img className="h-10 w-10 rounded-lg object-cover" alt="" loading="lazy" src={thumb(group.ref_image, 80)} />
            <span className="min-w-0">
              <span className="block truncate text-sm font-medium">{group.filename}</span>
              <span className={`block text-sm font-semibold ${late ? "text-ember" : ""}`}>{dayText(group.days_left)} · {hits.length} occurrence(s)</span>
              {summary ? <span className="block text-xs text-muted">{summary}</span> : null}
            </span>
          </button>
          <span className={`rounded-full border px-2.5 py-1 text-xs ${group.status === "expire" ? "border-transparent text-ember" : "border-line"}`}>
            {statusLabel[group.status] || group.status}
          </span>
        </div>
        <div className="mt-2 flex flex-wrap gap-2 pl-12">
          <button type="button" className={btnGhost} onClick={() => void decide(group.reference_id, ids, "retenu")}>Tout retenir</button>
          <button type="button" className={btnGhost} onClick={() => void decide(group.reference_id, ids, "ecarte")}>Tout écarter</button>
        </div>
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
                <span className="rounded-full border border-line px-2 py-0.5 text-xs">{matchStatusLabel[hit.match_status] || hit.match_status}</span>
                <button type="button" className={btnGhost} onClick={() => setModal({ group, hit })}>Comparer en grand</button>
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
  }

  function filterGroups(allGroups: MatchGroup[]): MatchGroup[] {
    return needle ? allGroups.filter((group) => group.filename.toLowerCase().includes(needle)) : allGroups;
  }

  function plainList(title: string, allGroups: MatchGroup[]) {
    const groups = filterGroups(allGroups);
    if (!groups.length) return null;
    const visible = groups.slice(0, shown);
    const rendered = visible.map(renderGroup).filter(Boolean);
    if (!rendered.length) return null;
    return (
      <div className="mt-6">
        <h3 className="text-lg font-semibold">{title}</h3>
        <div className="mt-2 grid gap-2">{rendered}</div>
        {groups.length > shown ? (
          <button type="button" className={`${btnGhost} mt-3`} onClick={() => setShown((value) => value + 40)}>
            Afficher la suite · {shown}/{groups.length}
          </button>
        ) : null}
      </div>
    );
  }

  function urgencyZones(allGroups: MatchGroup[]) {
    const groups = filterGroups(allGroups);
    if (!groups.length) return null;
    const zones = [...URGENCY_ZONES, RESIDUAL_ZONE];
    const sections = zones.map((zone) => {
      const inZone = groups.filter((group) => zoneFor(group).key === zone.key);
      const rendered = inZone.map(renderGroup).filter(Boolean);
      if (!rendered.length) return null;
      const open = openZones[zone.key];
      return (
        <div key={zone.key} className={`mt-4 rounded-3xl border p-3 ${zone.colorClasses}`}>
          <button type="button" className="flex w-full items-center justify-between gap-3 text-left" onClick={() => toggleZone(zone.key)}>
            <span className="flex items-center gap-2">
              <span className={`rounded-full border px-2.5 py-1 text-xs font-semibold uppercase tracking-wide ${zone.badgeClasses}`}>
                {zone.key === "expire" ? "EXPIRÉ" : `${rendered.length} groupe(s)`}
              </span>
              <span className="text-sm font-semibold">{zone.title}</span>
            </span>
            <span className="text-xs text-muted">{open ? "Replier" : "Déplier"}</span>
          </button>
          {open ? <div className="mt-3 grid gap-2">{rendered}</div> : null}
        </div>
      );
    });
    return (
      <div className="mt-6">
        <h3 className="text-lg font-semibold">{groups.length} dans la fenêtre</h3>
        {sections}
      </div>
    );
  }

  function notFoundList(title: string, allItems: NotFoundItem[]) {
    const items = needle ? allItems.filter((item) => item.filename.toLowerCase().includes(needle)) : allItems;
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
      <p className="mt-2 text-muted">Une ligne par visuel. Ouvre la ligne pour voir les deux images, en petit — ou "Comparer en grand" pour le détail.</p>
      <div className="mt-4 flex flex-wrap items-end gap-3">
        <label>
          <span className={label}>Montrer jusqu'à</span>
          <input className={field} type="number" min={0} max={3650} value={props.withinDays} onChange={(event) => props.onWithinDays(Number(event.target.value))} />
        </label>
        <label className="min-w-40">
          <span className={label}>Chercher un fichier</span>
          <input className={field} value={query} placeholder="Nom de fichier" onChange={(event) => setQuery(event.target.value)} />
        </label>
        <label>
          <span className={label}>Niveau de confiance</span>
          <select className={field} value={confidenceFilter} onChange={(event) => setConfidenceFilter(event.target.value as ConfidenceFilter)}>
            <option value="all">Tous</option>
            <option value="haut">Confirmé</option>
            <option value="moyen">Probable</option>
            <option value="a_verifier">À vérifier</option>
          </select>
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={props.hideRejected} onChange={(event) => props.onHideRejected(event.target.checked)} />
          Masquer les écartés
        </label>
        <button type="button" className={btnGhost} onClick={props.onReload}>Actualiser</button>
      </div>
      <div className="mt-3 flex flex-wrap items-end gap-3">
        <label>
          <span className={label}>Statut à exporter</span>
          <select className={field} value={reportStatus} onChange={(event) => setReportStatus(event.target.value)}>
            {REPORT_STATUS_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </select>
        </label>
        <a className={btn} href={`/api/downloads/report.html?within_days=${encodeURIComponent(String(props.withinDays))}&status=${encodeURIComponent(reportStatus)}`}>Exporter HTML</a>
        <a className={btnGhost} href={`/api/downloads/matches.csv?within_days=${encodeURIComponent(String(props.withinDays))}&status=${encodeURIComponent(reportStatus)}`}>Exporter CSV</a>
        <a className={btnGhost} href={`/api/downloads/not_found.csv?within_days=${encodeURIComponent(String(props.withinDays))}`}>CSV non trouvées</a>
      </div>
      <p className="mt-2 text-xs text-muted">
        <strong>Confirmé</strong> : hachage identique ou quasi (même image, recadrée ou recompressée). <strong>Probable</strong> et <strong>à vérifier</strong> : ressemblance visuelle, groupées dans "à regarder de près", triées par confiance. L'export ne reprend par défaut que les correspondances encore <strong>en attente</strong> de revue.
      </p>
      {!data ? <p className="mt-6 text-sm text-muted">Les correspondances arrivent.</p> : null}
      {data ? urgencyZones(confirmed) : null}
      {data && confirmed.length === 0 ? (
        <p className="mt-4 text-sm text-muted">
          {later.length ? `Rien dans les ${props.withinDays} jours. Le reste est listé plus bas.` : "Aucune correspondance pour l'instant."}
        </p>
      ) : null}
      {data && confirmed.length === 0 && later.length === 0 && notFound.length === 0 ? (
        <button type="button" className={`${btnGhost} mt-3`} onClick={props.onExplore}>Retour au site</button>
      ) : null}
      {data ? plainList(`${toVerify.length} à regarder de près`, toVerify) : null}
      {data ? notFoundList(`${notFound.length} non trouvées`, notFound) : null}
      {data ? plainList(`${later.length} plus loin`, later) : null}
      {modal ? (
        <CompareModal
          group={modal.group}
          hit={modal.hit}
          onClose={() => setModal(null)}
          onStatusChange={setMatchStatus}
          onExclude={excludeMatch}
        />
      ) : null}
    </section>
  );
}
