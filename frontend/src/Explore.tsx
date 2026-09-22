import { type FormEvent } from "react";
import { api } from "./api";
import type { Job } from "./types";
import { btn, btnGhost, card, field, label } from "./ui";

type Props = {
  site: string;
  maxPages: number;
  crawlFast: boolean;
  fresh: boolean;
  thenMatch: boolean;
  running: boolean;
  referenceImages: number;
  siteImages: number;
  waitingCount: number;
  job: Job | null;
  onSite: (value: string) => void;
  onMaxPages: (value: number) => void;
  onCrawlFast: (value: boolean) => void;
  onFresh: (value: boolean) => void;
  onThenMatch: (value: boolean) => void;
  onBack: () => void;
  onCompared: () => void;
  onBanner: (message: string) => void;
  onRefresh: () => Promise<unknown>;
};

function zeroImagesDiagnostic(job: Job | null): string | null {
  if (!job || job.kind !== "crawl" || job.status !== "done") return null;
  const result = job.result as { pages_visited?: number; images_stored?: number; blocked_by_robots?: number; errors?: string[] } | null;
  if (!result) return null;
  const pagesVisited = result.pages_visited || 0;
  const imagesStored = result.images_stored || 0;
  if (imagesStored > 0) return null;

  if (pagesVisited === 0) {
    return "Aucune page n'a pu être lue : site injoignable, ou tout bloqué dès la première page.";
  }
  const blocked = result.blocked_by_robots || 0;
  const errored = result.errors?.length || 0;
  if (blocked >= pagesVisited) {
    return `${pagesVisited} page(s) lue(s), mais toutes bloquées par robots.txt. Rien à comparer.`;
  }
  if (errored >= pagesVisited) {
    return `${pagesVisited} page(s) tentée(s), mais toutes en erreur de chargement.`;
  }
  return `${pagesVisited} page(s) lue(s), mais aucune image assez grande n'a été trouvée (icônes et petits visuels sont ignorés).`;
}

export function Explore(props: Props) {
  const needsLibrary = props.referenceImages === 0;
  const zeroImagesMessage = zeroImagesDiagnostic(props.job);

  async function crawl(event: FormEvent) {
    event.preventDefault();
    props.onBanner("");
    try {
      await api("/api/jobs/crawl", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          site: props.site,
          max_pages: Number(props.maxPages),
          fast: props.crawlFast,
          fresh: props.fresh,
          then_match: props.thenMatch,
        }),
      });
      await props.onRefresh();
    } catch (error) {
      props.onBanner(error instanceof Error ? error.message : "La requête a échoué.");
    }
  }

  async function match() {
    props.onBanner("");
    try {
      await api("/api/jobs/match", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ fast: props.crawlFast }),
      });
      props.onCompared();
      await props.onRefresh();
    } catch (error) {
      props.onBanner(error instanceof Error ? error.message : "La requête a échoué.");
    }
  }

  return (
    <section>
      <h2 className="text-4xl font-semibold tracking-tight">Où chercher</h2>
      <p className="mt-2 text-muted">On lit le sitemap, puis les liens internes. On ramène les images. On ne tranche pas encore.</p>
      {needsLibrary ? (
        <div className={`${card} mt-6`}>
          <h3 className="text-lg font-semibold">D'abord, les images</h3>
          <p className="mt-1 text-sm text-muted">Sans bibliothèque, le site n'a rien à quoi se comparer. Reviens à l'étape 01.</p>
          <button type="button" className={`${btn} mt-3`} onClick={props.onBack}>Retour à la bibliothèque</button>
        </div>
      ) : null}
      {!needsLibrary && props.waitingCount > 0 ? (
        <div className={`${card} mt-6`}>
          <h3 className="text-lg font-semibold">{props.waitingCount} image(s) pas encore indexée(s)</h3>
          <p className="mt-1 text-sm text-muted">
            Elles ne seront pas comparées tant que leur date n'est pas enregistrée. Retourne à la bibliothèque et clique "Enregistrer les dates".
          </p>
          <button type="button" className={`${btnGhost} mt-3`} onClick={props.onBack}>Retour à la bibliothèque</button>
        </div>
      ) : null}
      {zeroImagesMessage ? (
        <div className={`${card} mt-6`}>
          <h3 className="text-lg font-semibold">Rien ramené de ce site</h3>
          <p className="mt-1 text-sm text-muted">{zeroImagesMessage}</p>
        </div>
      ) : null}
      <form className={`${card} mt-6 grid max-w-xl gap-4`} onSubmit={(event) => void crawl(event)}>
        <label>
          <span className={label}>Adresse du site</span>
          <input className={field} type="url" required placeholder="https://www.exemple.com" value={props.site} onChange={(event) => props.onSite(event.target.value)} />
        </label>
        <label>
          <span className={label}>Pages à lire au maximum</span>
          <input className={field} type="number" min={1} max={5000} value={props.maxPages} onChange={(event) => props.onMaxPages(Number(event.target.value) || props.maxPages)} />
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={props.crawlFast} onChange={(event) => props.onCrawlFast(event.target.checked)} />
          Sans le modèle visuel : plus rapide, les recadrages passent à côté
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={props.fresh} onChange={(event) => props.onFresh(event.target.checked)} />
          Relire les pages déjà vues
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={props.thenMatch} onChange={(event) => props.onThenMatch(event.target.checked)} />
          Comparer dès que la lecture est finie
        </label>
        <div className="flex flex-wrap gap-2">
          <button type="submit" className={btn} disabled={props.running || needsLibrary}>Lire le site</button>
          <button type="button" className={btnGhost} disabled={props.running || props.siteImages === 0} onClick={() => void match()}>Comparer aux références</button>
        </div>
        <p className="text-sm text-muted">
          {props.siteImages
            ? `${props.siteImages} image(s) déjà ramenées. La comparaison peut partir de là.`
            : "La comparaison s'active une fois que des images ont été ramenées."}
        </p>
      </form>
    </section>
  );
}
