import { useEffect, useRef, useState } from "react";
import { ApiError, api } from "./api";
import { Explore } from "./Explore";
import { Library } from "./Library";
import { Results } from "./Results";
import type { Matches, Overview, View } from "./types";
import { btnGhost, card } from "./ui";

const jobLabel: Record<string, string> = {
  ingest: "Indexation",
  crawl: "Exploration",
  match: "Correspondance",
};

function exploreMeta(overview: Overview | null): string {
  const stats = overview?.stats;
  const job = overview?.job;
  if (!stats) return "Là où elles pourraient être";
  if (job?.kind === "crawl" && job.status === "running") {
    const done = job.progress.done || 0;
    const total = job.progress.total || 0;
    return total ? `Lecture en cours · ${done}/${total}` : "Lecture en cours";
  }
  if (job?.kind === "crawl" && job.status === "done" && job.message.startsWith("Arrêté")) {
    return `Lecture arrêtée · ${stats.pages_crawled || 0} pages en stock`;
  }
  if (job?.kind === "crawl" && job.status === "done") {
    return `Lecture terminée · ${stats.pages_crawled || 0} pages, ${stats.site_images || 0} images`;
  }
  return stats.pages_crawled
    ? `${stats.pages_crawled} pages en stock, ${stats.site_images || 0} images`
    : "Là où elles pourraient être";
}

export function App() {
  const [view, setView] = useState<View>("library");
  const [overview, setOverview] = useState<Overview | null>(null);
  const [matches, setMatches] = useState<Matches | null>(null);
  const [banner, setBanner] = useState("");
  const [note, setNote] = useState(false);
  const [withinDays, setWithinDays] = useState(90);
  const [maxPages, setMaxPages] = useState(30);
  const [site, setSite] = useState("");
  const [ingestFast, setIngestFast] = useState(false);
  const [crawlFast, setCrawlFast] = useState(false);
  const [thenMatch, setThenMatch] = useState(true);
  const [fresh, setFresh] = useState(false);
  const [hideRejected, setHideRejected] = useState(true);
  const defaultsReady = useRef(false);
  const seenStatus = useRef<string>("idle");
  const viewRef = useRef(view);
  viewRef.current = view;

  async function refresh() {
    const next = await api<Overview>("/api/overview");
    if (!defaultsReady.current && next.defaults) {
      setMaxPages(next.defaults.max_pages);
      setWithinDays(next.defaults.within_days);
      defaultsReady.current = true;
    }
    setOverview(next);
    const status = next.job?.status ?? "idle";
    const changed = status !== seenStatus.current;
    seenStatus.current = status;
    if (changed && status === "done") {
      const finished = next.job?.result != null && "matches" in next.job.result;
      if (finished) setView("results");
      if (finished || viewRef.current === "results") {
        const days = defaultsReady.current ? undefined : next.defaults.within_days;
        await loadMatches(days);
      }
    }
    return next;
  }

  async function loadMatches(days?: number) {
    const windowDays = days ?? withinDays;
    const data = await api<Matches>(`/api/matches?within_days=${encodeURIComponent(String(windowDays))}`);
    setMatches(data);
    return data;
  }

  useEffect(() => {
    let stopped = false;
    void refresh().catch((error: unknown) => {
      if (!stopped) setBanner(error instanceof ApiError ? error.message : "La requête a échoué.");
    });
    return () => {
      stopped = true;
    };
    // First load only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (overview?.job?.status !== "running") return;
    const timer = window.setTimeout(() => {
      void refresh().catch((error: unknown) => {
        setBanner(error instanceof ApiError ? error.message : "La requête a échoué.");
      });
    }, 1000);
    return () => window.clearTimeout(timer);
    // Poll while a job is running. refresh is recreated each render; the timer is the clock.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [overview?.job?.status, overview?.job?.message, overview?.job?.progress?.done]);

  const stats = overview?.stats;
  const library = overview?.library ?? [];
  const waiting = library.filter((item) => !item.indexed).length;
  const running = overview?.job?.status === "running";
  const job = overview?.job;

  function show(next: View) {
    setBanner("");
    setNote(false);
    setView(next);
    if (next === "results") {
      void loadMatches().catch((error: unknown) => {
        setBanner(error instanceof Error ? error.message : "La requête a échoué.");
      });
    }
  }

  async function stopJob() {
    try {
      await api("/api/jobs/cancel", { method: "POST" });
      await refresh();
    } catch (error) {
      setBanner(error instanceof Error ? error.message : "La requête a échoué.");
    }
  }

  const progress = job?.progress;
  const total = Number(progress?.total || 0);
  const done = Number(progress?.done || 0);
  const width = total > 0 ? Math.max(4, Math.round((done / total) * 100)) : 0;

  return (
    <div className="grid min-h-screen grid-cols-1 md:grid-cols-[280px_minmax(0,1fr)]">
      <aside className="flex flex-col border-b border-line bg-side p-6 md:border-r md:border-b-0">
        <p className="text-sm font-medium">RightsWatch</p>
        <p className="mt-2 text-sm text-muted">Ce qui est encore en ligne, alors que les droits s'épuisent.</p>
        <nav className="mt-8 grid gap-2" aria-label="Parcours">
          <Step index="01" title="Bibliothèque" active={view === "library"} onClick={() => show("library")}
            meta={waiting ? `${stats?.reference_images || 0} indexée(s), ${waiting} en attente` : `${stats?.reference_images || 0} image(s) à protéger`} />
          <Step index="02" title="Le site" active={view === "explore"} onClick={() => show("explore")} meta={exploreMeta(overview)} />
          <Step index="03" title="Correspondances" active={view === "results"} onClick={() => show("results")}
            meta={stats?.matches ? `${stats.matches} correspondance(s)` : "Ce qui dépasse la date"} />
        </nav>
        <p className="mt-auto pt-8 text-xs text-muted">Interface locale. Pas de compte. Tout reste sur cette machine.</p>
      </aside>
      <main className="px-4 py-8 md:px-10">
        {job ? (
          <section className={`${card} mb-4`}>
            <p className="text-xs font-medium uppercase tracking-wide text-muted">{jobLabel[job.kind] || "Tâche"}</p>
            <p className={`mt-1 text-lg font-semibold tracking-tight ${job.status === "error" ? "text-ember" : ""}`}>
              {job.status === "error" ? job.error || job.message : job.message}
            </p>
            {job.status === "running" ? (
              <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-canvas">
                <div className="h-full bg-ink" style={{ width: total > 0 ? `${width}%` : "28%" }} />
              </div>
            ) : null}
            {job.status === "running" ? (
              <button type="button" className={`${btnGhost} mt-3`} onClick={() => void stopJob()}>Arrêter</button>
            ) : null}
          </section>
        ) : null}
        {banner ? <p className={`mb-4 text-sm font-medium ${note ? "text-ink" : "text-ember"}`}>{banner}</p> : null}
        {view === "library" ? (
          <Library
            items={library}
            indexed={stats?.reference_images || 0}
            running={Boolean(running)}
            ingestFast={ingestFast}
            onIngestFast={setIngestFast}
            onContinue={() => show("explore")}
            onBanner={(message, isNote) => { setBanner(message); setNote(Boolean(isNote)); }}
            onRefresh={() => refresh()}
          />
        ) : null}
        {view === "explore" ? (
          <Explore
            site={site}
            maxPages={maxPages}
            crawlFast={crawlFast}
            fresh={fresh}
            thenMatch={thenMatch}
            running={Boolean(running)}
            referenceImages={stats?.reference_images || 0}
            siteImages={stats?.site_images || 0}
            onSite={setSite}
            onMaxPages={setMaxPages}
            onCrawlFast={setCrawlFast}
            onFresh={setFresh}
            onThenMatch={setThenMatch}
            onBack={() => show("library")}
            onCompared={() => show("results")}
            onBanner={(message) => { setBanner(message); setNote(false); }}
            onRefresh={() => refresh()}
          />
        ) : null}
        {view === "results" ? (
          <Results
            matches={matches}
            withinDays={withinDays}
            hideRejected={hideRejected}
            onWithinDays={(days) => { setWithinDays(days); void loadMatches(days); }}
            onHideRejected={setHideRejected}
            onReload={() => void loadMatches()}
            onExplore={() => show("explore")}
            onBanner={(message) => { setBanner(message); setNote(false); }}
          />
        ) : null}
      </main>
    </div>
  );
}

function Step(props: { index: string; title: string; meta: string; active: boolean; onClick: () => void }) {
  return (
    <button type="button" onClick={props.onClick}
      className={`rounded-2xl px-3 py-3 text-left ${props.active ? "bg-paper shadow-[0_0_0_1px_rgba(23,23,23,0.05),0_1px_2px_rgba(0,0,0,0.06)]" : ""}`}>
      <span className="text-xs text-muted">{props.index}</span>
      <span className="mt-1 block text-sm font-medium">{props.title}</span>
      <span className="mt-1 block text-xs text-muted">{props.meta}</span>
    </button>
  );
}
