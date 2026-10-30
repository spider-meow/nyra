import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router";
import { useToast } from "../components/feedback";
import { ContentFilter, ContentTagger } from "../components/Labels";
import { LoadError, RefreshFailed, Refreshing, SkeletonGrid } from "../components/busy";
import { Button, EmptyState, FieldLabel, Input, PageHeader, cx } from "../components/ui";
import { errorMessage } from "../lib/api";
import { useLastKnown } from "../lib/busy";
import { plural } from "../lib/format";
import { LABELS_ENABLED, useAssignments, useLabels } from "../lib/labels";
import { useOrg } from "../lib/org";
import { useExclusionMutations } from "../lib/queries";
import { useLogoMutations, useSiteImages } from "../lib/siteImages";
import type { ImageLabel, LabelInfo, SiteImage, SiteImagesPage, SiteImageView } from "../types";
import { AddToLibrary } from "./site-images/AddToLibrary";
import { ImageCard, Zoom } from "./site-images/ImageCard";
import { LabelingMode, TypeButtons, TypeFilter, typeOf } from "./site-images/Typing";

const TABS: [SiteImageView, string][] = [["qualify", "À qualifier"], ["found", "Retrouvées"], ["logos", "Logos"], ["packshots", "Packshots"], ["ignored", "Ignorées"], ["all", "Toutes"]];
const isView = (value: string | null): value is SiteImageView => TABS.some(([name]) => name === value);

/**
 * Everything read on the brand's sites, one tab per kind: what still has to be qualified (it matches nothing in the
 * library: each one either joins the library with its expiry date or is ignored), what was found in the library, the logos,
 * what was ignored, and all of it.
 */
export function SiteImages() {
  const { admin } = useOrg();
  const [params, setParams] = useSearchParams();
  const asked = params.get("view");
  const view: SiteImageView = isView(asked) ? asked : "qualify";
  const search = useSearch();
  const images = useSiteImages({ view, site: search.site, text: search.applied });
  const items = useMemo(() => images.data?.pages.flatMap((page) => page.items) ?? [], [images.data]);
  const last = images.data?.pages.at(-1);
  const page: SiteImagesPage | undefined = last && { ...last, items };
  // A search, a site or a refresh with cards on screen: they stay (never passed off as the new answer: see `stale`), and so do the counters of the tabs.
  const refreshing = images.isFetching && !images.isFetchingNextPage && !images.isLoading;
  const counts = useLastKnown(page?.counts);
  const packshotsOn = useLastKnown(page?.packshots_enabled) ?? false; // the tab exists only for a brand that turned the category on (it stays while another tab loads)
  const pick = useSelection();
  const [adding, setAdding] = useState<SiteImage[] | null>(null);
  const [zoom, setZoom] = useState<SiteImage | null>(null);
  const [typing, setTyping] = useState(false);
  const { ignore, ignoring } = useIgnore();
  const { labels, labelsOf } = useSiteLabels();
  const filter = useLabelFilter(items, labelsOf);
  const chosen = items.filter((item) => pick.selected.has(item.id));
  const qualifying = view === "qualify" && admin;
  function changeView(next: SiteImageView) {
    setParams(next === "qualify" ? {} : { view: next }, { replace: true });
    search.reset();
    pick.clear();
  }
  return (
    <div className={cx(qualifying && chosen.length > 0 && "pb-20")}>
      <PageHeader title="Images en ligne" actions={admin && LABELS_ENABLED ? <Button onClick={() => setTyping(true)}>Étiqueter à la main</Button> : undefined} />
      <ViewTabs view={view} counts={counts} packshots={packshotsOn || view === "packshots"} onChange={changeView} />
      <ViewNote view={view} logosSetAside={page?.logos_set_aside} />
      {images.isLoading ? (
        <SkeletonGrid className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4" cell="aspect-[4/5]" />
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
            footerFor={admin && view === "logos" ? (item) => <KeepLogo item={item} setAside={page.logos_set_aside} /> : undefined}
            filtered={search.applied !== "" || search.site !== "all"}
          />
          </Refreshing>
        </>
      )}
      {qualifying && chosen.length ? (
        <BulkBar chosen={chosen} labels={labels} ignoring={ignoring} onAdd={() => setAdding(chosen)} onIgnore={() => ignore(chosen, (failedIds) => pick.setSelected(new Set(failedIds)))} onClear={pick.clear} />
      ) : null}
      <AddToLibrary items={adding} onClose={() => setAdding(null)} onDone={(ids) => pick.setSelected(new Set([...pick.selected].filter((id) => !ids.includes(id))))} />
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

/** The cards ticked (qualify tab only). */
function useSelection() {
  const [selected, setSelected] = useState<Set<string>>(new Set());
  return {
    selected,
    setSelected,
    clear: () => setSelected(new Set()),
    toggle: (id: string, on: boolean) => {
      const next = new Set(selected);
      if (on) next.add(id);
      else next.delete(id);
      setSelected(next);
    },
  };
}

/** The site version and the text asked for (the text is sent after a short pause). */
function useSearch() {
  const [site, setSite] = useState("all");
  const [text, setText] = useState("");
  const [applied, setApplied] = useState("");
  useEffect(() => {
    const timer = setTimeout(() => setApplied(text.trim()), 300);
    return () => clearTimeout(timer);
  }, [text]);
  return {
    site, text, applied,
    changeSite: setSite,
    changeText: setText,
    reset: () => { setSite("all"); setText(""); setApplied(""); },
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
function ViewTabs(props: { view: SiteImageView; counts: SiteImagesPage["counts"] | undefined; packshots: boolean; onChange: (view: SiteImageView) => void }) {
  return (
    <div className="mb-4 flex w-full overflow-x-auto border-b border-line [scrollbar-width:none]" role="tablist" aria-label="Images en ligne">
      {TABS.filter(([name]) => name !== "packshots" || props.packshots).map(([name, label]) => (
        <button
          key={name}
          type="button"
          role="tab"
          aria-selected={props.view === name}
          onClick={() => props.onChange(name)}
          className={cx(
            "-mb-px whitespace-nowrap border-b-2 px-4 py-2.5 text-[13.5px] transition-colors",
            props.view === name ? "border-ink font-medium text-ink" : "border-transparent text-muted hover:text-ink",
          )}
        >
          {label} {props.counts ? <span className="tabular text-muted">{props.counts[name].toLocaleString("fr-FR")}</span> : null}
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
    logos: logosSetAside
      ? <>Images reconnues comme logos : elles ne sont ni comparées ni proposées dans « À qualifier ». Le réglage se change dans les {settings}.</>
      : <>Images reconnues comme logos. Le réglage « Ne pas comparer les logos » est désactivé : elles sont encore comparées et proposées dans « À qualifier ». Pour les mettre à part, voir les {settings}.</>,
    packshots: <>Photos de produit seul sur un fond uni, repérées par Nyra. Elles ne sont plus dans « À qualifier » mais restent comparées à la bibliothèque : rien n'est masqué. Une erreur de détection se corrige en désactivant le réglage dans les {settings}.</>,
    ignored: <>Images mises de côté à la main : elles ne sont plus proposées. On les réintègre depuis les <Link className={linkClass} to={`${link("settings")}?section=general`}>Réglages</Link>.</>,
  };
  return notes[view] ? <p className="mb-4 rounded-xl bg-side px-4 py-3 text-sm text-ink-soft">{notes[view]}</p> : null;
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
            "-mb-px flex-1 whitespace-nowrap border-b-2 px-4 py-2.5 text-[13.5px] transition-colors",
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
        {/* While the answer to a search or a site comes, the count is the previous one: it says so instead of passing for the new one. */}
        <p className="pb-2 text-sm text-muted">{props.stale ? "Mise à jour…" : plural(page.total, page.view === "qualify" ? "image à vérifier" : "image", page.view === "qualify" ? "images à vérifier" : "images")}</p>
        {selecting && page.items.length ? (
          <button
            type="button"
            className="ml-auto pb-2 text-sm text-muted underline-offset-2 hover:text-ink hover:underline"
            onClick={() => selecting.onSelect(selecting.selected.size ? new Set() : new Set(selecting.items.map((item) => item.id)))}
          >
            {selecting.selected.size ? "Tout désélectionner" : "Tout sélectionner"}
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
  onToggle: ((id: string, on: boolean) => void) | undefined;
  onZoom: (item: SiteImage) => void;
  onMore: () => void;
  loadingMore: boolean;
  onAdd: ((item: SiteImage) => void) | undefined;
  onIgnore: ((item: SiteImage) => void) | undefined;
  footerFor: ((item: SiteImage) => ReactNode) | undefined;
};

function ImageGrid(props: GridProps) {
  const { page, items } = props;
  if (!page.items.length) return <EmptyTab view={page.view} filtered={props.filtered} />;
  return (
    <>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
        {items.map((item) => (
          <ImageCard
            key={item.id}
            item={item}
            labels={props.labelsOf(item)}
            selected={props.selected.has(item.id)}
            onSelect={props.onToggle ? (on) => props.onToggle?.(item.id, on) : undefined}
            onOpen={() => props.onZoom(item)}
            onAdd={props.onAdd ? () => props.onAdd?.(item) : undefined}
            onIgnore={props.onIgnore ? () => props.onIgnore?.(item) : undefined}
            footer={props.footerFor ? <div className="mt-auto pt-1.5 text-[13px]">{props.footerFor(item)}</div> : undefined}
          />
        ))}
      </div>
      {page.items.length < page.total ? (
        <div className="mt-4 text-center">
          <Button size="sm" loading={props.loadingMore} onClick={props.onMore}>Afficher plus · {page.items.length}/{page.total}</Button>
        </div>
      ) : null}
      {!items.length ? <p className="py-6 text-center text-sm text-muted">Aucune image ne correspond à ces filtres.</p> : null}
    </>
  );
}

/** For the logos tab: put a logo back into the comparison, or set it aside again (administrators). Without the setting there is nothing to put back. */
function KeepLogo({ item, setAside }: { item: SiteImage; setAside: boolean }) {
  const { keep } = useLogoMutations();
  const toast = useToast();
  if (!item.logo) return null;
  const kept = item.logo.kept;
  return (
    <div className="flex flex-wrap items-center justify-between gap-2">
      <span className="text-xs text-muted">reconnu à {Math.round(item.logo.score * 100)} %</span>
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

/** A tab with nothing in it: for "À qualifier" that is good news. */
function EmptyTab({ view, filtered }: { view: SiteImageView; filtered: boolean }) {
  const { link } = useOrg();
  if (view === "qualify" && !filtered) {
    return (
      <EmptyState
        title="Toutes les images en ligne ont des droits vérifiés"
        body="Chaque image trouvée sur les sites correspond à un visuel de la bibliothèque, a été ignorée ou est un logo. Relisez les sites pour vérifier les nouveautés."
        action={<Link className="text-sm underline" to={link("scans")}>Sites et lectures</Link>}
      />
    );
  }
  return <p className="py-10 text-center text-sm text-muted">{filtered ? "Aucune image ne correspond à cette recherche." : "Aucune image dans cet onglet."}</p>;
}

function SelectionBar(props: { count: number; ignoring: boolean; onAdd: () => void; onIgnore: () => void; onClear: () => void; children: ReactNode }) {
  return (
    <div className="fixed inset-x-4 bottom-4 z-30 mx-auto flex max-w-4xl flex-wrap items-center gap-3 rounded-3xl border border-line-strong bg-bar px-5 py-2.5 text-sm text-white shadow-float md:left-[calc(248px+2.5rem)]" role="region" aria-label="Actions sur la sélection">
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
