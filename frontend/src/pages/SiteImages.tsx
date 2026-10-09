import { useMemo, useState } from "react";
import { Link } from "react-router";
import { useToast } from "../components/feedback";
import { Button, EmptyState, FieldLabel, Input, PageHeader, Skeleton, cx } from "../components/ui";
import { errorMessage } from "../lib/api";
import { plural } from "../lib/format";
import { useOrg } from "../lib/org";
import { useExclusionMutations, useSiteImages } from "../lib/queries";
import type { SiteImage } from "../types";
import { AddToLibrary } from "./site-images/AddToLibrary";
import { ImageCard, Zoom } from "./site-images/ImageCard";

/**
 * Everything read on the brand's sites that matches nothing in its library.
 * Their rights are unknown: each one either joins the library (with its
 * expiry date) or is set aside as not rights-managed (logo, pictogram).
 */
export function SiteImages() {
  const { admin, brand } = useOrg();
  const images = useSiteImages();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [adding, setAdding] = useState<SiteImage[] | null>(null);
  const [zoom, setZoom] = useState<SiteImage | null>(null);
  const ignore = useIgnore();
  const items = images.data?.items ?? [];
  const filter = useImageFilter(items);
  const chosen = items.filter((item) => selected.has(item.id));

  function toggle(id: string, on: boolean) {
    const next = new Set(selected);
    if (on) next.add(id);
    else next.delete(id);
    setSelected(next);
  }

  return (
    <div className={cx(admin && chosen.length > 0 && "pb-20")}>
      <PageHeader
        title="Droits non vérifiés"
        description={`Les images en ligne sur les sites de ${brand.name} qui ne correspondent à aucun visuel de la bibliothèque : personne n'a encore vérifié leurs droits, et certaines sont peut-être expirées. Ajoutez celles qui sont sous droits, avec leur échéance, et ignorez le reste (logos, pictogrammes, visuels maison).`}
      />
      {images.isLoading ? (
        <ImageSkeletons />
      ) : images.error ? (
        <EmptyState title="Impossible de charger les images" body={errorMessage(images.error)} />
      ) : !items.length ? (
        <AllVerified />
      ) : (
        <>
          <FilterBar filter={filter} items={items} selected={selected} onSelect={setSelected} />
          <ImageGrid
            filter={filter}
            selected={selected}
            onToggle={toggle}
            onZoom={setZoom}
            onAdd={(item) => setAdding([item])}
            onIgnore={(item) => ignore([item], () => toggle(item.id, false))}
          />
        </>
      )}
      {admin && chosen.length ? (
        <SelectionBar count={chosen.length}onAdd={() => setAdding(chosen)} onIgnore={() => ignore(chosen, (failedIds) => setSelected(new Set(failedIds)))} onClear={() => setSelected(new Set())} />
      ) : null}
      <AddToLibrary
        items={adding}
        onClose={() => setAdding(null)}
        onDone={(ids) => {
          const next = new Set(selected);
          ids.forEach((id) => next.delete(id));
          setSelected(next);
        }}
      />
      <Zoom item={zoom} onClose={() => setZoom(null)} />
    </div>
  );
}

/** The site, the search text and how many cards are shown; any filter change goes back to the first 60. */
function useImageFilter(items: SiteImage[]) {
  const [site, setSite] = useState("all");
  const [query, setQuery] = useState("");
  const [shown, setShown] = useState(60);
  const sites = useMemo(() => {
    const out = new Map<string, string>();
    for (const item of items) item.site_ids.forEach((id, index) => out.set(id, item.sites[index]));
    return [...out];
  }, [items]);
  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return items.filter(
      (item) =>
        (site === "all" || item.site_ids.includes(site)) &&
        (!needle || item.filename.toLowerCase().includes(needle) || item.pages.some((page) => page.toLowerCase().includes(needle))),
    );
  }, [items, site, query]);
  return {
    site, query, shown, sites, visible, setShown,
    changeSite: (value: string) => { setSite(value); setShown(60); },
    changeQuery: (value: string) => { setQuery(value); setShown(60); },
  };
}

/** One tab per site version (US, FR, INT...): each one is its own library of unverified images. */
function SiteTabs(props: { filter: ReturnType<typeof useImageFilter>; items: SiteImage[] }) {
  const { filter, items } = props;
  if (filter.sites.length < 2) return null;
  const tabs: [string, string, number][] = [
    ["all", "Tous", items.length],
    ...filter.sites.map(([id, label]): [string, string, number] => [id, label, items.filter((item) => item.site_ids.includes(id)).length]),
  ];
  return (
    <div className="mb-5 flex w-full overflow-x-auto border-b border-line [scrollbar-width:none]" role="tablist" aria-label="Versions du site">
      {tabs.map(([id, label, count]) => (
        <button
          key={id}
          type="button"
          role="tab"
          aria-selected={filter.site === id}
          onClick={() => filter.changeSite(id)}
          className={cx(
            "-mb-px flex-1 whitespace-nowrap border-b-2 px-4 py-2.5 text-[13.5px] transition-colors",
            filter.site === id ? "border-ink font-medium text-ink" : "border-transparent text-muted hover:text-ink",
          )}
        >
          {label} <span className="tabular text-muted">{count}</span>
        </button>
      ))}
    </div>
  );
}

function FilterBar(props: { filter: ReturnType<typeof useImageFilter>; items: SiteImage[]; selected: Set<string>; onSelect: (ids: Set<string>) => void }) {
  const { admin } = useOrg();
  const { filter, selected } = props;
  return (
    <>
    <SiteTabs filter={filter} items={props.items} />
    <div className="mb-4 flex flex-wrap items-end gap-3">
      <div className="w-full sm:w-72">
        <FieldLabel htmlFor="img-search">Rechercher</FieldLabel>
        <Input id="img-search" type="search" placeholder="Nom de fichier ou page" value={filter.query} onChange={(event) => filter.changeQuery(event.target.value)} />
      </div>
      <p className="pb-2 text-sm text-muted">{plural(filter.visible.length, "image à vérifier", "images à vérifier")}</p>
      {admin && filter.visible.length ? (
        <button
          type="button"
          className="ml-auto pb-2 text-sm text-muted underline-offset-2 hover:text-ink hover:underline"
          onClick={() => props.onSelect(selected.size ? new Set() : new Set(filter.visible.slice(0, filter.shown).map((item) => item.id)))}
        >
          {selected.size ? "Tout désélectionner" : "Tout sélectionner"}
        </button>
      ) : null}
    </div>
    </>
  );
}

type GridProps = {
  filter: ReturnType<typeof useImageFilter>;
  selected: Set<string>;
  onToggle: (id: string, on: boolean) => void;
  onZoom: (item: SiteImage) => void;
  onAdd: (item: SiteImage) => void;
  onIgnore: (item: SiteImage) => void;
};

function ImageGrid(props: GridProps) {
  const { admin } = useOrg();
  const { visible, shown } = props.filter;
  return (
    <>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
        {visible.slice(0, shown).map((item) => (
          <ImageCard
            key={item.id}
            item={item}
            selected={props.selected.has(item.id)}
            onSelect={admin ? (on) => props.onToggle(item.id, on) : undefined}
            onOpen={() => props.onZoom(item)}
            onAdd={admin ? () => props.onAdd(item) : undefined}
            onIgnore={admin ? () => props.onIgnore(item) : undefined}
          />
        ))}
      </div>
      {visible.length > shown ? (
        <div className="mt-4 text-center">
          <Button size="sm" onClick={() => props.filter.setShown((value) => value + 60)}>Afficher plus · {shown}/{visible.length}</Button>
        </div>
      ) : null}
      {!visible.length ? <p className="py-6 text-center text-sm text-muted">Aucune image ne correspond à ces filtres.</p> : null}
    </>
  );
}

function ImageSkeletons() {
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
      {Array.from({ length: 8 }, (_, index) => <Skeleton key={index} className="aspect-[4/5]" />)}
    </div>
  );
}

function AllVerified() {
  const { link } = useOrg();
  return (
    <EmptyState
      title="Toutes les images en ligne ont des droits vérifiés"
      body="Chaque image trouvée sur les sites correspond à un visuel de la bibliothèque, ou a été ignorée. Relisez les sites pour vérifier les nouveautés."
      action={<Link className="text-sm underline" to={link("lectures")}>Sites et lectures</Link>}
    />
  );
}

function SelectionBar(props: { count: number; onAdd: () => void; onIgnore: () => void; onClear: () => void }) {
  return (
    <div className="fixed inset-x-4 bottom-4 z-30 mx-auto flex max-w-2xl flex-wrap items-center gap-3 rounded-full border border-line-strong bg-bar px-5 py-2.5 text-sm text-white shadow-float md:left-[calc(248px+2.5rem)]" role="region" aria-label="Actions sur la sélection">
      <span className="font-medium">{plural(props.count, "sélectionnée", "sélectionnées")}</span>
      <Button size="sm" onClick={props.onAdd}>Ajouter à la bibliothèque</Button>
      <button type="button" className="text-white/80 hover:text-white" onClick={props.onIgnore}>Ignorer</button>
      <button type="button" className="ml-auto text-white/70 hover:text-white" onClick={props.onClear}>Annuler</button>
    </div>
  );
}

/** Set aside as not rights-managed: never proposed again, near copies included. */
function useIgnore() {
  const { addMany } = useExclusionMutations();
  const toast = useToast();
  // `done` receives the ids of the cards that could not be ignored, so a caller can keep them selected for a retry.
  return (items: SiteImage[], done: (failedIds: string[]) => void) => {
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
}
