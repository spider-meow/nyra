import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router";
import { Present, usePresence, useToast } from "../components/feedback";
import { ContentFilter, ContentTagger } from "../components/Labels";
import { LoadError, RefreshFailed, Refreshing, SkeletonGrid } from "../components/busy";
import { Button, EmptyState, FieldLabel, Input, PageHeader, cx } from "../components/ui";
import { errorMessage } from "../lib/api";
import { useLastKnown } from "../lib/busy";
import { plural } from "../lib/format";
import { LABELS_ENABLED, useAssignments, useLabels } from "../lib/labels";
import { useOrg } from "../lib/org";
import { useExclusionMutations } from "../lib/queries";
import { useLogoMutations, useRequalify, useSiteImages } from "../lib/siteImages";
import type { ImageLabel, LabelInfo, SiteImage, SiteImagesPage, SiteImageType, SiteImageView } from "../types";
import { AddToLibrary } from "./site-images/AddToLibrary";
import { ImageCard, Zoom } from "./site-images/ImageCard";
import { LabelingMode, TypeButtons, TypeFilter, typeOf } from "./site-images/Typing";

const TABS: [SiteImageView, string][] = [["qualify", "À qualifier"], ["found", "Retrouvées"], ["ignored", "Ignorées"]];
const TYPES: [SiteImageType, string][] = [["packshots", "Packshots"], ["logos", "Logos"], ["photos", "Photos"]];
const isView = (value: string | null): value is SiteImageView => TABS.some(([name]) => name === value);
const isType = (value: string | null): value is SiteImageType => TYPES.some(([name]) => name === value);
// The old links (one tab per type, and "Toutes") land on the nearest state + type.
const LEGACY: Record<string, { view: SiteImageView; type: SiteImageType | null }> = { logos: { view: "qualify", type: "logos" }, packshots: { view: "ignored", type: "packshots" }, all: { view: "qualify", type: null } };

/**
 * Everything read on the brand's sites. The tabs are the state of an image (what still has to be qualified: it matches nothing
 * in the library, so each one either joins the library with its expiry date or is ignored; what was found in the library; what
 * was set aside) and the pills under them its type (packshot, logo, photo). An image is in one state.
 */
export function SiteImages() {
  const { admin, link } = useOrg();
  const [params, setParams] = useSearchParams();
  const asked = params.get("view");
  const legacy = asked ? LEGACY[asked] : undefined;
  const view: SiteImageView = legacy?.view ?? (isView(asked) ? asked : "qualify");
  const askedType = params.get("type");
  const type: SiteImageType | null = legacy ? legacy.type : isType(askedType) ? askedType : null;
  const search = useSearch();
  const images = useSiteImages({ view, type, site: search.site, text: search.applied, min: search.min });
  const items = useMemo(() => images.data?.pages.flatMap((page) => page.items) ?? [], [images.data]);
  const last = images.data?.pages.at(-1);
  const page: SiteImagesPage | undefined = last && { ...last, items };
  // A search, a site or a refresh with cards on screen: they stay (never passed off as the new answer: see `stale`), and so do the counters of the tabs.
  const refreshing = images.isFetching && !images.isFetchingNextPage && !images.isLoading;
  const counts = useLastKnown(page?.counts);
  const typeCounts = useLastKnown(page?.type_counts); // the pills keep their numbers while another type loads
  const packshotsOn = useLastKnown(page?.packshots_enabled) ?? false; // the pill exists only for a brand that turned the category on (it stays while another tab loads)
  const [adding, setAdding] = useState<SiteImage[] | null>(null);
  // The open sheet is in the address (?image=<id>, an identifier only): the link shares it, Back closes it.
  const zoom = items.find((item) => item.id === params.get("image")) ?? null;
  const setZoom = (item: SiteImage | null) => setParams((previous) => { const next = new URLSearchParams(previous); if (item) next.set("image", item.id); else next.delete("image"); return next; }, { replace: false });
  const [typing, setTyping] = useState(false);
  const { ignore, ignoring } = useIgnore();
  const { labels, labelsOf } = useSiteLabels();
  const filter = useLabelFilter(items, labelsOf);
  const pick = useSelection(items);
  const chosen = items.filter((item) => pick.selected.has(item.id));
  const qualifying = view === "qualify" && admin;
  // The one action of a card outside "À qualifier": put a logo back (Logos filter), or go and decide a found image in "À traiter".
  const footerFor = admin && type === "logos" ? (item: SiteImage) => <KeepLogo item={item} setAside={page?.logos_set_aside ?? false} />
    : admin && view === "ignored" ? (item: SiteImage) => (item.status === "ignored" ? <Requalify item={item} /> : null)
    : view === "found" ? () => <Link className="text-[13px] font-medium text-ink underline underline-offset-2" to={link("review")}>Voir dans À traiter</Link> : undefined;
  // The address holds the state and the type, by identifier only; the default ("À qualifier", every type) leaves it clean.
  function go(next: SiteImageView, nextType: SiteImageType | null) {
    setParams((previous) => {
      const out = new URLSearchParams(previous);
      out.delete("view"); out.delete("type");
      if (next !== "qualify") out.set("view", next);
      if (nextType) out.set("type", nextType);
      return out;
    }, { replace: true });
    pick.clear();
  }
  function changeView(next: SiteImageView) {
    go(next, type);
    search.reset();
  }
  // Redirect an old link to its new address once.
  useEffect(() => {
    if (legacy) go(legacy.view, legacy.type);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [legacy]);
  return (
    <div className={cx(qualifying && chosen.length > 0 && "pb-20")}>
      <PageHeader title="Images en ligne" actions={admin && LABELS_ENABLED ? <Button onClick={() => setTyping(true)}>Étiqueter à la main</Button> : undefined} />
      <ViewTabs view={view} counts={counts} onChange={changeView} />
      <TypePills type={type} counts={typeCounts} total={counts?.[view]} packshots={packshotsOn || type === "packshots"} onChange={(next) => go(view, next)} />
      <ViewNote view={view} logosSetAside={page?.logos_set_aside} />
      {images.isLoading ? (
        <SkeletonGrid className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4" cell="aspect-[1/2]" />
      ) : !page ? (
        <LoadError title="Impossible de charger les images" error={images.error} onRetry={() => void images.refetch()} retrying={images.isFetching} />
      ) : (
        <>
          {images.error ? <RefreshFailed error={images.error} onRetry={() => void (images.isFetchNextPageError ? images.fetchNextPage() : images.refetch())} retrying={images.isFetching} /> : null}
          <FilterBar page={page} search={search} labels={labels} filter={filter} stale={images.isPlaceholderData} selecting={qualifying ? { selected: pick.selected, onSelect: pick.setSelected, items } : undefined} />
          <Refreshing active={refreshing} label="Mise à jour des images…">
          <ImageGrid
            page={page}
            items={filter.visible}
            labelsOf={labelsOf}
            selected={pick.selected}
            onToggle={admin && view === "qualify" ? pick.toggle : undefined}
            onZoom={setZoom}
            onMore={() => void images.fetchNextPage()}
            loadingMore={images.isFetchingNextPage}
            onAdd={qualifying ? (item) => setAdding([item]) : undefined}
            onIgnore={qualifying ? (item) => ignore([item], () => pick.toggle(item.id, false)) : undefined}
            footerFor={footerFor}
            filtered={search.applied !== "" || search.site !== "all" || search.min > 0 || type !== null}
            onClear={() => { search.reset(); go(view, null); }}
          />
          </Refreshing>
        </>
      )}
      <Present show={Boolean(qualifying && chosen.length)}>
        <BulkBar chosen={chosen} labels={labels} ignoring={ignoring} onAdd={() => setAdding(chosen)} onIgnore={() => ignore(chosen, (failedIds) => pick.setSelected(new Set(failedIds)))} onClear={pick.clear} />
      </Present>
      <Present show={adding !== null}>
        <AddToLibrary items={adding} onClose={() => setAdding(null)} onDone={(ids) => pick.setSelected(new Set([...pick.selected].filter((id) => !ids.includes(id))))} />
      </Present>
      <Zoom item={zoom} onClose={() => setZoom(null)} />
      {LABELS_ENABLED ? <LabelingMode open={typing} onClose={() => setTyping(false)} /> : null}
    </div>
  );
}

/** The labels of the site images (switched off until calibrated): all of them, and each image's own. */
function useSiteLabels() {
  const assignments = useAssignments("site").data?.assignments;
  const labelsOf = useCallback((item: SiteImage) => assignments?.[item.id] ?? [], [assignments]);
  return { labels: useLabels().data?.labels ?? [], labelsOf };
}

/** What can be done to the cards ticked: add them to the library, ignore them, or (labels on) type them. */
function BulkBar(props: { chosen: SiteImage[]; labels: LabelInfo[]; ignoring: boolean; onAdd: () => void; onIgnore: () => void; onClear: () => void }) {
  const { chosen, labels } = props;
  return (
    <SelectionBar count={chosen.length} ignoring={props.ignoring} onAdd={props.onAdd} onIgnore={props.onIgnore} onClear={props.onClear}>
      {LABELS_ENABLED ? <TypeButtons types={labels.filter((label) => label.kind === "type")} items={chosen} onDone={props.onClear} /> : null}
      {LABELS_ENABLED ? <ContentTagger labels={labels} images={{ siteImageIds: chosen.flatMap((item) => item.ids) }} dark /> : null}
    </SelectionBar>
  );
}

/** The cards ticked (qualify tab only): Shift + click ticks the range since the last click, Escape unticks all. */
function useSelection(order: SiteImage[]) {
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [last, setLast] = useState<string | null>(null);
  useEffect(() => {
    if (!selected.size) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") setSelected(new Set()); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selected.size]);
  return {
    selected,
    setSelected,
    clear: () => setSelected(new Set()),
    toggle: (id: string, on: boolean, range = false) => {
      const next = new Set(selected);
      const from = last ? order.findIndex((item) => item.id === last) : -1;
      const to = order.findIndex((item) => item.id === id);
      const ids = range && from >= 0 && to >= 0 ? order.slice(Math.min(from, to), Math.max(from, to) + 1).map((item) => item.id) : [id];
      for (const one of ids) { if (on) next.add(one); else next.delete(one); }
      setLast(id);
      setSelected(next);
    },
  };
}

/** The text, the site version and the minimum size asked for: all in the address (identifiers and numbers only), so a link shares the list. The text is sent after a short pause. */
function useSearch() {
  const [params, setParams] = useSearchParams();
  const put = (key: string, value: string, empty: string) =>
    setParams((previous) => { const next = new URLSearchParams(previous); if (value === empty) next.delete(key); else next.set(key, value); return next; }, { replace: true });
  const [text, setText] = useState(params.get("q") ?? "");
  const [applied, setApplied] = useState(text.trim());
  useEffect(() => {
    const timer = setTimeout(() => { setApplied(text.trim()); put("q", text.trim(), ""); }, 300);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text]);
  return {
    site: params.get("site") ?? "all", text, applied, min: Number(params.get("min")) || 0,
    changeSite: (id: string) => put("site", id, "all"),
    changeText: setText,
    changeMin: (px: number) => put("min", String(px), "0"),
    reset: () => { setText(""); setApplied(""); setParams((previous) => { const next = new URLSearchParams(previous); ["q", "site", "min"].forEach((key) => next.delete(key)); return next; }, { replace: true }); },
  };
}
type Search = ReturnType<typeof useSearch>;

/** The type and the content label asked for, among the cards loaded (labels are switched off until calibrated). */
function useLabelFilter(items: SiteImage[], labelsOf: (item: SiteImage) => ImageLabel[]) {
  const [type, setType] = useState("all");
  const [content, setContent] = useState("all");
  const visible = useMemo(
    () => items.filter((item) => (type === "all" || (typeOf(labelsOf(item))?.name ?? "none") === type) && (content === "all" || labelsOf(item).some((label) => label.label_id === content))),
    [items, type, content, labelsOf],
  );
  // How many images carry each type ("none": no type yet) and each content label.
  const counts = useMemo(() => {
    const out: Record<string, number> = {};
    for (const item of items) {
      const found = labelsOf(item);
      const key = typeOf(found)?.name ?? "none";
      out[key] = (out[key] ?? 0) + 1;
      for (const label of found) if (label.kind === "content") out[label.label_id] = (out[label.label_id] ?? 0) + 1;
    }
    return out;
  }, [items, labelsOf]);
  return { type, content, counts, visible, changeType: setType, changeContent: setContent };
}
type LabelFilter = ReturnType<typeof useLabelFilter>;

/** The tabs of the page, each with its total. */
function ViewTabs(props: { view: SiteImageView; counts: SiteImagesPage["counts"] | undefined; onChange: (view: SiteImageView) => void }) {
  return (
    <div className="mb-4 flex w-full overflow-x-auto border-b border-line [scrollbar-width:none]" role="tablist" aria-label="Images en ligne">
      {TABS.map(([name, label]) => (
        <button
          key={name}
          type="button"
          role="tab"
          aria-selected={props.view === name}
          onClick={() => props.onChange(name)}
          className={cx(
            "-mb-px whitespace-nowrap border-b-2 px-4 py-2.5 text-[13.5px]",
            props.view === name ? "border-ink font-medium text-ink" : "border-transparent text-muted hover:text-ink",
          )}
        >
          {label} {props.counts?.[name] !== undefined ? <span className="tabular text-muted">{props.counts[name].toLocaleString("fr-FR")}</span> : null}
        </button>
      ))}
    </div>
  );
}

/** The type filter under the tabs: "Tout" or one type, each with how many images of the tab it holds. */
function TypePills(props: { type: SiteImageType | null; counts: SiteImagesPage["type_counts"] | undefined; total: number | undefined; packshots: boolean; onChange: (type: SiteImageType | null) => void }) {
  const pills: [SiteImageType | null, string, number | undefined][] = [[null, "Tout", props.total], ...TYPES.filter(([name]) => name !== "packshots" || props.packshots).map(([name, label]): [SiteImageType, string, number | undefined] => [name, label, props.counts?.[name]])];
  return (
    <div className="mb-4 flex flex-wrap items-center gap-2" role="group" aria-label="Type d'image">
      <span className="text-[13px] text-muted">Type</span>
      {pills.map(([name, label, count]) => (
        <button
          key={name ?? "all"}
          type="button"
          aria-pressed={props.type === name}
          onClick={() => props.onChange(name)}
          className={cx("rounded-full border px-3.5 py-1 text-[13px] font-medium", props.type === name ? "border-ink bg-ink text-white" : "border-line-strong bg-paper text-ink hover:bg-sunk")}
        >
          {label} {count !== undefined ? <span className="tabular opacity-75">{count.toLocaleString("fr-FR")}</span> : null}
        </button>
      ))}
    </div>
  );
}

/** One line under the tabs saying what the tab is and where the rest happens. */
function ViewNote({ view, logosSetAside }: { view: SiteImageView; logosSetAside: boolean | undefined }) {
  const { link } = useOrg();
  const linkClass = "font-medium text-ink underline underline-offset-2";
  const settings = <Link className={linkClass} to={`${link("settings")}?section=general`}>Réglages</Link>;
  const notes: Partial<Record<SiteImageView, ReactNode>> = {
    found: <>Ces images contiennent un visuel de la bibliothèque. Les décisions se prennent dans <Link className={linkClass} to={link("review")}>À traiter</Link>.</>,
    ignored: <>Images mises de côté : à la main, ou parce que Nyra les a reconnues comme logos {logosSetAside ? "(réglage « Ne pas comparer les logos »)" : ""} ou comme packshots. Elles ne sont plus proposées dans « À qualifier ». On les réintègre depuis les {settings}.</>,
  };
  return notes[view] ? <p key={view} className="fade-in mb-4 rounded-xl bg-side px-4 py-3 text-sm text-ink-soft">{notes[view]}</p> : null;
}

/** One tab per site version (US, FR, INT...), when there are several. */
function SiteTabs(props: { page: SiteImagesPage; search: Search }) {
  const { page, search } = props;
  if (page.sites.length < 2) return null;
  const tabs: [string, string, number][] = [["all", "Tous", page.counts[page.view]], ...page.sites.map((site): [string, string, number] => [site.id, site.label, site.count])];
  return (
    <div className="mb-5 flex w-full overflow-x-auto border-b border-line [scrollbar-width:none]" role="tablist" aria-label="Versions du site">
      {tabs.map(([id, label, count]) => (
        <button
          key={id}
          type="button"
          role="tab"
          aria-selected={search.site === id}
          onClick={() => search.changeSite(id)}
          className={cx(
            "-mb-px flex-1 whitespace-nowrap border-b-2 px-4 py-2.5 text-[13.5px]",
            search.site === id ? "border-ink font-medium text-ink" : "border-transparent text-muted hover:text-ink",
          )}
        >
          {label} <span className="tabular text-muted">{count}</span>
        </button>
      ))}
    </div>
  );
}

type Selecting = { selected: Set<string>; onSelect: (ids: Set<string>) => void; items: SiteImage[] };

function FilterBar(props: { page: SiteImagesPage; search: Search; labels: LabelInfo[]; filter: LabelFilter; selecting: Selecting | undefined; stale: boolean }) {
  const { page, search, filter, selecting } = props;
  return (
    <>
      <SiteTabs page={page} search={search} />
      <div className="mb-4 flex flex-wrap items-end gap-3">
        <div className="w-full sm:w-72">
          <FieldLabel htmlFor="img-search">Rechercher</FieldLabel>
          <Input id="img-search" type="search" placeholder="Nom de fichier ou page" value={search.text} onChange={(event) => search.changeText(event.target.value)} />
        </div>
        <div>
          <FieldLabel htmlFor="img-min">Taille minimale</FieldLabel>
          <select id="img-min" value={search.min} onChange={(event) => search.changeMin(Number(event.target.value))} className="h-10 rounded-md border border-line-strong bg-paper px-3 text-sm">
            <option value={0}>Toutes</option>
            <option value={300}>≥ 300 px</option>
            <option value={600}>≥ 600 px</option>
          </select>
        </div>
        {/* While the answer to a search or a site comes, the count is the previous one: it says so instead of passing for the new one. */}
        <p className="pb-2 text-sm text-muted">{props.stale ? "Mise à jour…" : plural(page.total, page.view === "qualify" ? "image à vérifier" : "image", page.view === "qualify" ? "images à vérifier" : "images")}</p>
        {selecting && page.items.length ? (
          <button
            type="button"
            className="ml-auto pb-2 text-sm text-muted underline-offset-2 hover:text-ink hover:underline"
            onClick={() => selecting.onSelect(selecting.selected.size ? new Set() : new Set(selecting.items.map((item) => item.id)))}
          >
            {selecting.selected.size ? "Tout désélectionner" : page.total > page.items.length ? `Sélectionner les ${page.items.length} affichées` : "Tout sélectionner"}
          </button>
        ) : null}
      </div>
      {LABELS_ENABLED ? <TypeFilter types={props.labels.filter((label) => label.kind === "type")} counts={filter.counts} value={filter.type} onChange={filter.changeType} /> : null}
      {LABELS_ENABLED ? <ContentFilter labels={props.labels} counts={filter.counts} value={filter.content} onChange={filter.changeContent} /> : null}
    </>
  );
}

type GridProps = {
  page: SiteImagesPage;
  items: SiteImage[];
  labelsOf: (item: SiteImage) => ImageLabel[];
  selected: Set<string>;
  filtered: boolean;
  onClear: () => void;
  onToggle: ((id: string, on: boolean, range?: boolean) => void) | undefined;
  onZoom: (item: SiteImage) => void;
  onMore: () => void;
  loadingMore: boolean;
  onAdd: ((item: SiteImage) => void) | undefined;
  onIgnore: ((item: SiteImage) => void) | undefined;
  footerFor: ((item: SiteImage) => ReactNode) | undefined;
};

function ImageGrid(props: GridProps) {
  const { page, items } = props;
  if (!page.items.length) return <EmptyTab view={page.view} filtered={props.filtered} nothingRead={Object.values(page.counts).every((count) => count === 0)} onClear={props.onClear} />;
  return (
    <>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
        {items.map((item) => (
          <ImageCard
            key={item.id}
            item={item}
            labels={props.labelsOf(item)}
            selected={props.selected.has(item.id)}
            selecting={props.selected.size > 0}
            onSelect={props.onToggle ? (on, range) => props.onToggle?.(item.id, on, range) : undefined}
            onOpen={() => props.onZoom(item)}
            onAdd={props.onAdd ? () => props.onAdd?.(item) : undefined}
            onIgnore={props.onIgnore ? () => props.onIgnore?.(item) : undefined}
            footer={props.footerFor ? <div className="mt-auto pt-1.5 text-[13px]">{props.footerFor(item)}</div> : undefined}
          />
        ))}
      </div>
      {items.some((item) => item.packshot_measures) ? (
        <p className="mt-3 text-xs text-muted">Équipe Nyra, carte sous le focus : <kbd className="font-mono">P</kbd> packshot · <kbd className="font-mono">N</kbd> pas un packshot · <kbd className="font-mono">⌫</kbd> revenir à la détection.</p>
      ) : null}
      {page.items.length < page.total ? (
        <div className="mt-4 text-center">
          <Button size="sm" loading={props.loadingMore} onClick={props.onMore}>Afficher plus · {page.items.length}/{page.total}</Button>
        </div>
      ) : null}
      {!items.length ? <p className="py-6 text-center text-sm text-muted">Aucune image ne correspond à ces filtres.</p> : null}
    </>
  );
}

/** "Remettre à qualifier" for an image ignored by hand: every file of the photo goes back together. */
function Requalify({ item }: { item: SiteImage }) {
  const requalify = useRequalify();
  const toast = useToast();
  const run = () =>
    requalify.mutate(item.variants.map((variant) => variant.id), {
      onSuccess: (result) => toast.show(result.exclusions ? { tone: "success", message: "Remise dans À qualifier", description: "Elle sera comparée de nouveau à la bibliothèque." } : { tone: "info", message: "Rien à retirer", description: "Cette image n'a pas été ignorée à la main." }),
      onError: (error) => toast.show({ tone: "error", message: "L'image n'a pas été remise", description: errorMessage(error) }),
    });
  return <button type="button" disabled={requalify.isPending} onClick={run} className="text-[13px] font-medium text-ink underline underline-offset-2 disabled:opacity-60">Remettre à qualifier</button>;
}

/** For the logos tab: put a logo back into the comparison, or set it aside again (administrators). Without the setting there is nothing to put back. */
function KeepLogo({ item, setAside }: { item: SiteImage; setAside: boolean }) {
  const { keep } = useLogoMutations();
  const toast = useToast();
  if (!item.logo) return null;
  const kept = item.logo.kept;
  return (
    <div className="flex flex-wrap items-center justify-between gap-2">
      <span className="text-xs text-muted">{item.logo.score === null ? "reconnu comme logo" : `reconnu à ${Math.round(item.logo.score * 100)} %`}</span>
      {setAside ? (
        <button
          type="button"
          disabled={keep.isPending}
          className="font-medium text-ink underline-offset-2 hover:underline disabled:opacity-60"
          // Every file of the photo (a crop is another file) goes together.
          onClick={() => keep.mutate({ ids: item.variants.map((variant) => variant.id), kept: !kept }, { onError: (error) => toast.show({ tone: "error", message: "L'image n'a pas été modifiée", description: errorMessage(error) }) })}
        >
          {kept ? "Mettre à part" : "Remettre dans la comparaison"}
        </button>
      ) : null}
    </div>
  );
}

/** A tab with nothing in it, saying what to do: for "À qualifier" that is good news. */
function EmptyTab({ view, filtered, nothingRead, onClear }: { view: SiteImageView; filtered: boolean; nothingRead: boolean; onClear: () => void }) {
  const { link, platformAdmin } = useOrg();
  if (filtered) {
    return <EmptyState title="Aucun résultat pour ces filtres" body="Aucune image ne correspond à cette recherche, ce site ou ce type." action={<Button size="sm" onClick={onClear}>Tout effacer</Button>} />;
  }
  if (nothingRead) {
    return (
      <EmptyState
        title="Aucune image lue pour cette marque"
        body="Les images apparaissent après la première lecture de ses sites."
        action={platformAdmin ? <Link className="text-sm underline" to={link("scans")}>Lancer une lecture</Link> : undefined}
      />
    );
  }
  if (view === "qualify") {
    return (
      <EmptyState
        title="Tout est qualifié"
        body="Chaque image trouvée sur les sites correspond à un visuel de la bibliothèque, a été ajoutée, ou a été mise de côté. Les nouveautés apparaissent après chaque lecture des sites."
        action={<Link className="text-sm underline" to={link("library")}>Ouvrir la bibliothèque</Link>}
      />
    );
  }
  return <p className="py-10 text-center text-sm text-muted">Aucune image dans cet onglet.</p>;
}

function SelectionBar(props: { count: number; ignoring: boolean; onAdd: () => void; onIgnore: () => void; onClear: () => void; children: ReactNode }) {
  const { leaving, onAnimationEnd } = usePresence(true);
  return (
    <div inert={leaving} onAnimationEnd={onAnimationEnd} className={cx(leaving ? "fade-out" : "rise-in", "fixed inset-x-4 bottom-4 z-30 mx-auto flex max-w-4xl flex-wrap items-center gap-3 rounded-3xl border border-line-strong bg-bar px-5 py-2.5 text-sm text-white shadow-float md:left-[calc(248px+2.5rem)]")} role="region" aria-label="Actions sur la sélection">
      <span className="font-medium">{plural(props.count, "sélectionnée", "sélectionnées")}</span>
      <Button size="sm" onClick={props.onAdd}>Ajouter à la bibliothèque</Button>
      {/* Immediate feedback on the bar itself, not only the toast: the images are set aside one request later. */}
      <button type="button" className="text-white/80 hover:text-white disabled:opacity-60" disabled={props.ignoring} aria-busy={props.ignoring} onClick={props.onIgnore}>{props.ignoring ? "Ignorer…" : "Ignorer"}</button>
      {props.children}
      <button type="button" className="ml-auto text-white/70 hover:text-white" onClick={props.onClear}>Annuler</button>
    </div>
  );
}

/** Set aside as not rights-managed: never proposed again, near copies included. */
function useIgnore() {
  const { addMany } = useExclusionMutations();
  const toast = useToast();
  // `done` receives the ids of the cards that could not be ignored, so a caller can keep them selected for a retry.
  const ignore = (items: SiteImage[], done: (failedIds: string[]) => void) => {
    const pending = toast.loading(items.length > 1 ? `${items.length} images ignorées…` : "Image ignorée…");
    // Every version of a photo is set aside: a crop does not look like its original to the hashes.
    const siteImageIds = items.flatMap((item) => item.variants.map((variant) => variant.id));
    void addMany.mutateAsync({ siteImageIds, reason: "Pas sous droits" }).then(({ failedIds: failedImages, firstError }) => {
      const failedIds = items.filter((item) => item.variants.some((variant) => failedImages.includes(variant.id))).map((item) => item.id);
      done(failedIds);
      toast.update(pending, failedIds.length
        ? { tone: "error", message: `${plural(failedIds.length, "image n'a pas pu être ignorée", "images n'ont pas pu être ignorées")}`, description: firstError || "Réessayez dans un instant." }
        : {
            tone: "success",
            message: items.length > 1 ? `${items.length} images ignorées` : "Image ignorée",
            description: items.length > 1
              ? "Elles ne seront plus proposées, copies proches comprises. Vous pouvez les réintégrer depuis les Réglages."
              : "Elle ne sera plus proposée, copies proches comprises. Vous pouvez la réintégrer depuis les Réglages.",
          });
    });
  };
  return { ignore, ignoring: addMany.isPending };
}
