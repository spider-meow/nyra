import { useMemo, useState, type FormEvent } from "react";
import { Link } from "react-router";
import { Modal, useToast } from "../components/feedback";
import { Button, Card, EmptyState, FieldLabel, Input, PageHeader, Select, Skeleton, cx } from "../components/ui";
import { errorMessage } from "../lib/api";
import { formatDate, pathOf, plural } from "../lib/format";
import { useOrg } from "../lib/org";
import { useExclusionMutations, useSiteImageMutations, useSiteImages } from "../lib/queries";
import type { SiteImage } from "../types";

/**
 * Everything read on the brand's sites that matches nothing in its library.
 * Their rights are unknown: each one either joins the library (with its
 * expiry date) or is set aside as not rights-managed (logo, pictogram).
 */
export function SiteImages() {
  const { admin, brand, link } = useOrg();
  const images = useSiteImages();
  const [site, setSite] = useState("all");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [adding, setAdding] = useState<SiteImage[] | null>(null);
  const [zoom, setZoom] = useState<SiteImage | null>(null);
  const [shown, setShown] = useState(60);
  const ignore = useIgnore();

  const items = images.data?.items ?? [];
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
  const chosen = items.filter((item) => selected.has(item.id));

  function toggle(id: string, on: boolean) {
    const next = new Set(selected);
    if (on) next.add(id);
    else next.delete(id);
    setSelected(next);
  }

  return (
    <div className={cx(admin && selected.size > 0 && "pb-20")}>
      <PageHeader
        title="Droits non vérifiés"
        description={`Les images en ligne sur les sites de ${brand.name} qui ne correspondent à aucun visuel de la bibliothèque : personne n'a encore vérifié leurs droits, et certaines sont peut-être expirées. Ajoutez celles qui sont sous droits, avec leur échéance, et ignorez le reste (logos, pictogrammes, visuels maison).`}
      />

      {images.isLoading ? (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
          {Array.from({ length: 8 }, (_, index) => <Skeleton key={index} className="aspect-[4/5]" />)}
        </div>
      ) : images.error ? (
        <EmptyState title="Impossible de charger les images" body={errorMessage(images.error)} />
      ) : !items.length ? (
        <EmptyState
          title="Toutes les images en ligne ont des droits vérifiés"
          body="Chaque image trouvée sur les sites correspond à un visuel de la bibliothèque, ou a été ignorée. Relisez les sites pour vérifier les nouveautés."
          action={<Link className="text-sm underline" to={link("lectures")}>Sites et lectures</Link>}
        />
      ) : (
        <>
          <div className="mb-4 flex flex-wrap items-end gap-3">
            <div className="w-full sm:w-72">
              <FieldLabel htmlFor="img-search">Rechercher</FieldLabel>
              <Input id="img-search" type="search" placeholder="Nom de fichier ou page" value={query} onChange={(event) => { setQuery(event.target.value); setShown(60); }} />
            </div>
            {sites.length > 1 ? (
              <div className="w-full sm:w-56">
                <FieldLabel htmlFor="img-site">Site</FieldLabel>
                <Select id="img-site" value={site} onChange={(event) => { setSite(event.target.value); setShown(60); }}>
                  <option value="all">Tous ({items.length})</option>
                  {sites.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
                </Select>
              </div>
            ) : null}
            <p className="pb-2 text-sm text-muted">{plural(visible.length, "image à vérifier", "images à vérifier")}</p>
            {admin && visible.length ? (
              <button
                type="button"
                className="ml-auto pb-2 text-sm text-muted underline-offset-2 hover:text-ink hover:underline"
                onClick={() => setSelected(selected.size ? new Set() : new Set(visible.slice(0, shown).map((item) => item.id)))}
              >
                {selected.size ? "Tout désélectionner" : "Tout sélectionner"}
              </button>
            ) : null}
          </div>

          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
            {visible.slice(0, shown).map((item) => (
              <ImageCard
                key={item.id}
                item={item}
                selected={selected.has(item.id)}
                onSelect={admin ? (on) => toggle(item.id, on) : undefined}
                onOpen={() => setZoom(item)}
                onAdd={admin ? () => setAdding([item]) : undefined}
                onIgnore={admin ? () => ignore([item], () => toggle(item.id, false)) : undefined}
              />
            ))}
          </div>
          {visible.length > shown ? (
            <div className="mt-4 text-center">
              <Button size="sm" onClick={() => setShown((value) => value + 60)}>Afficher plus · {shown}/{visible.length}</Button>
            </div>
          ) : null}
          {!visible.length ? <p className="py-6 text-center text-sm text-muted">Aucune image ne correspond à ces filtres.</p> : null}
        </>
      )}

      {admin && selected.size ? (
        <div className="fixed inset-x-4 bottom-4 z-30 mx-auto flex max-w-2xl flex-wrap items-center gap-3 rounded-xl bg-ink px-4 py-2.5 text-sm text-white shadow-xl md:left-[calc(248px+2.5rem)]" role="region" aria-label="Actions sur la sélection">
          <span className="font-medium">{plural(selected.size, "sélectionnée", "sélectionnées")}</span>
          <Button size="sm" onClick={() => setAdding(chosen)}>Ajouter à la bibliothèque</Button>
          <button type="button" className="text-white/80 hover:text-white" onClick={() => ignore(chosen, () => setSelected(new Set()))}>Ignorer</button>
          <button type="button" className="ml-auto text-white/70 hover:text-white" onClick={() => setSelected(new Set())}>Annuler</button>
        </div>
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

function ImageCard(props: {
  item: SiteImage;
  selected: boolean;
  onSelect?: (on: boolean) => void;
  onOpen: () => void;
  onAdd?: () => void;
  onIgnore?: () => void;
}) {
  const { item } = props;
  return (
    <Card padded={false} className={cx("flex flex-col overflow-hidden", props.selected && "ring-2 ring-focus")}>
      <div className="relative">
        <button type="button" className="block w-full bg-canvas" onClick={props.onOpen} aria-label={`Agrandir ${item.filename}`}>
          {item.thumb ? (
            <img src={item.thumb} alt="" loading="lazy" className="aspect-square w-full object-contain" />
          ) : (
            <span className="block aspect-square w-full" />
          )}
        </button>
        {props.onSelect ? (
          <input
            type="checkbox"
            aria-label={`Sélectionner ${item.filename}`}
            className="absolute top-2 left-2 h-4 w-4 accent-ink"
            checked={props.selected}
            onChange={(event) => props.onSelect?.(event.target.checked)}
          />
        ) : null}
        {!item.compared ? (
          <span className="absolute top-2 right-2 rounded-full bg-paper/90 px-2 py-0.5 text-[11px] text-muted">pas encore comparée</span>
        ) : null}
      </div>
      <div className="flex flex-1 flex-col gap-1 p-3">
        <span className="w-fit rounded-full bg-urgent-soft px-2 py-0.5 text-[11px] font-medium text-urgent">Droits non vérifiés</span>
        <p className="truncate text-sm font-medium" title={item.filename}>{item.filename}</p>
        <p className="text-xs text-muted">
          {item.sites.join(", ")} · {plural(item.page_count, "page")}
          {item.width && item.height ? ` · ${item.width} × ${item.height}` : ""}
        </p>
        {item.pages[0] ? (
          <a href={item.pages[0]} target="_blank" rel="noreferrer noopener" className="truncate text-xs text-muted hover:text-ink hover:underline" title={item.pages[0]}>
            {pathOf(item.pages[0])}
          </a>
        ) : null}
        {props.onAdd ? (
          <div className="mt-auto flex gap-1 pt-2">
            <Button size="sm" variant="primary" className="flex-1" onClick={props.onAdd}>Ajouter</Button>
            <Button size="sm" variant="ghost" onClick={props.onIgnore}>Ignorer</Button>
          </div>
        ) : null}
      </div>
    </Card>
  );
}

/** Set aside as not rights-managed: never proposed again, near copies included. */
function useIgnore() {
  const { add } = useExclusionMutations();
  const toast = useToast();
  return (items: SiteImage[], done: () => void) => {
    const pending = toast.loading(items.length > 1 ? `${items.length} images ignorées…` : "Image ignorée…");
    void Promise.allSettled(items.map((item) => add.mutateAsync({ siteImageId: item.id, reason: "Pas sous droits" }))).then((results) => {
      const failed = results.filter((result) => result.status === "rejected").length;
      done();
      toast.update(pending, failed
        ? { tone: "error", message: `${plural(failed, "image n'a pas pu être ignorée", "images n'ont pas pu être ignorées")}`, description: "Réessayez dans un instant." }
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

function AddToLibrary(props: { items: SiteImage[] | null; onClose: () => void; onDone: (ids: string[]) => void }) {
  const { adopt } = useSiteImageMutations();
  const toast = useToast();
  const [expiry, setExpiry] = useState("");
  const [credit, setCredit] = useState("");
  const [name, setName] = useState("");
  const [openedFor, setOpenedFor] = useState<string | null>(null);
  const items = props.items;
  const key = items?.map((item) => item.id).join(",") ?? null;
  if (key !== openedFor) {
    setOpenedFor(key);
    setExpiry("");
    setCredit("");
    setName(items?.length === 1 ? items[0].filename : "");
  }
  if (!items) return null;
  const single = items.length === 1;

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!items) return;
    adopt.mutate(
      {
        site_image_ids: items.map((item) => item.id),
        expiry_date: expiry,
        credit,
        filenames: single && name.trim() ? { [items[0].id]: name.trim() } : {},
      },
      {
        onSuccess: (result) => {
          props.onDone(result.added.map((item) => item.site_image_id));
          props.onClose();
          if (result.added.length && !result.failed.length) {
            toast.show({
              tone: "success",
              message: result.added.length > 1 ? `${result.added.length} visuels ajoutés à la bibliothèque` : `${result.added[0].filename} ajouté à la bibliothèque`,
              description: expiry
                ? "Ils sont déjà reliés à leurs pages : retrouvez-les dans « À traiter »."
                : "Ils sont reliés à leurs pages. Renseignez leur échéance dans la bibliothèque pour qu'ils comptent dans les alertes.",
            });
          } else {
            toast.show({
              tone: result.added.length ? "info" : "error",
              message: `${plural(result.added.length, "ajouté", "ajoutés")}, ${plural(result.failed.length, "refusé", "refusés")}`,
              description: result.failed.map((item) => item.reason).filter((reason, index, all) => all.indexOf(reason) === index).join(" "),
            });
          }
        },
        onError: (error) => toast.show({ tone: "error", message: "L'ajout n'a pas abouti", description: errorMessage(error) }),
      },
    );
  }

  return (
    <Modal
      open
      onClose={props.onClose}
      title={single ? "Ajouter à la bibliothèque" : `Ajouter ${items.length} images à la bibliothèque`}
      footer={
        <>
          <Button onClick={props.onClose}>Annuler</Button>
          <Button variant="primary" type="submit" form="adopt-form" loading={adopt.isPending}>
            {single ? "Ajouter" : `Ajouter les ${items.length}`}
          </Button>
        </>
      }
    >
      <form id="adopt-form" className="grid gap-4" onSubmit={submit}>
        <div className="flex gap-2 overflow-x-auto">
          {items.slice(0, 8).map((item) => (
            <img key={item.id} src={item.thumb} alt="" className="h-16 w-16 shrink-0 rounded-md border border-line bg-canvas object-cover" />
          ))}
          {items.length > 8 ? <span className="self-center text-sm text-muted">+{items.length - 8}</span> : null}
        </div>
        {single ? (
          <div>
            <FieldLabel htmlFor="adopt-name">Nom dans la bibliothèque</FieldLabel>
            <Input id="adopt-name" maxLength={200} value={name} onChange={(event) => setName(event.target.value)} />
          </div>
        ) : (
          <p className="text-sm text-muted">Chaque image garde le nom de son fichier sur le site.</p>
        )}
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <FieldLabel htmlFor="adopt-expiry" hint="facultatif">Date d'expiration des droits</FieldLabel>
            <Input id="adopt-expiry" type="date" value={expiry} onChange={(event) => setExpiry(event.target.value)} />
          </div>
          <div>
            <FieldLabel htmlFor="adopt-credit" hint="facultatif">Crédit</FieldLabel>
            <Input id="adopt-credit" maxLength={500} placeholder="Photographe, agence" value={credit} onChange={(event) => setCredit(event.target.value)} />
          </div>
        </div>
        <p className="text-xs text-muted">
          {expiry
            ? `Échéance ${new Date(expiry) < new Date() ? "déjà passée : ces visuels apparaîtront comme expirés et en ligne" : `au ${formatDate(expiry)}`}.`
            : "Sans échéance, le visuel est surveillé mais ne déclenche pas d'alerte d'expiration."}
        </p>
      </form>
    </Modal>
  );
}

function Zoom(props: { item: SiteImage | null; onClose: () => void }) {
  const item = props.item;
  return (
    <Modal open={item !== null} onClose={props.onClose} title={item?.filename ?? ""} wide footer={<Button onClick={props.onClose}>Fermer</Button>}>
      {item ? (
        <div className="grid gap-5 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
          <a href={item.image || item.thumb} target="_blank" rel="noreferrer noopener" className="block overflow-hidden rounded-lg border border-line bg-canvas">
            <img src={item.image || item.thumb} alt="" className="max-h-[60vh] w-full object-contain" />
          </a>
          <div className="grid content-start gap-3 text-sm">
            <p className="text-muted">
              Vue pour la première fois le {formatDate(item.first_seen)}
              {item.width && item.height ? ` · ${item.width} × ${item.height} px` : ""}
            </p>
            <div>
              <p className="font-medium">{plural(item.page_count, "page", "pages")}</p>
              <ul className="mt-1 grid gap-1">
                {item.pages.map((page) => (
                  <li key={page} className="truncate">
                    <a href={page} target="_blank" rel="noreferrer noopener" className="text-ink-soft hover:underline">{page}</a>
                  </li>
                ))}
                {item.page_count > item.pages.length ? <li className="text-muted">et {item.page_count - item.pages.length} autre(s)</li> : null}
              </ul>
            </div>
            {item.url_count > 1 ? (
              <p className="text-muted">Le même fichier est servi à {item.url_count} adresses (tailles ou CDN différents).</p>
            ) : null}
          </div>
        </div>
      ) : null}
    </Modal>
  );
}
