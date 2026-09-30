import { useMemo, useRef, useState, type DragEvent } from "react";
import { Link } from "react-router";
import { Modal, useConfirm, useToast } from "../components/feedback";
import { Icon } from "../components/icons";
import { Button, Card, Chip, EmptyState, FieldLabel, Input, PageHeader, SearchField, Segmented, Skeleton, Spinner, StatusBadge, cx } from "../components/ui";
import { downloadFile, errorMessage } from "../lib/api";
import { MAX_TAGS, daysText, formatDate, plural, splitTags } from "../lib/format";
import { useOrg } from "../lib/org";
import { useLibrary, useLibraryMutations, useReferenceUpload } from "../lib/queries";
import type { ImportRow, LibraryItem, Status } from "../types";

type Filter = "all" | Status | "unindexed";

/** Refused files grouped by reason: one explanation, then the names it applies to. */
function byReason(failures: { filename: string; reason: string }[]): [string, string[]][] {
  const groups = new Map<string, string[]>();
  for (const item of failures) groups.set(item.reason, [...(groups.get(item.reason) ?? []), item.filename]);
  return [...groups];
}
type Sort = "expiry" | "name";
/** Expired visuals live in their own tab: still compared to the sites, out of the way of the working library. */
type Tab = "active" | "expired";
const TAGS_SHOWN = 12;

export function Library() {
  const { admin, apiPath, brand, link } = useOrg();
  const library = useLibrary();
  const mutations = useLibraryMutations();
  const uploader = useReferenceUpload();
  const [exporting, setExporting] = useState(false);
  const toast = useToast();
  const confirm = useConfirm();
  const fileInput = useRef<HTMLInputElement>(null);
  const [tab, setTab] = useState<Tab>("active");
  const [filter, setFilter] = useState<Filter>("all");
  const [sort, setSort] = useState<Sort>("expiry");
  const [query, setQuery] = useState("");
  const [activeTags, setActiveTags] = useState<string[]>([]);
  const [untagged, setUntagged] = useState(false);
  const [allTags, setAllTags] = useState(false);
  const [bulkTag, setBulkTag] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [editing, setEditing] = useState<LibraryItem | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [bulkDate, setBulkDate] = useState("");
  const [shown, setShown] = useState(100);
  const [failures, setFailures] = useState<{ filename: string; reason: string }[]>([]);
  // The refused files themselves, to send them again in one click.
  const [retryable, setRetryable] = useState<File[]>([]);
  const [replaced, setReplaced] = useState<string[]>([]);

  const items = library.data?.items ?? [];
  const expiredItems = useMemo(() => items.filter((item) => item.status === "expire"), [items]);
  const activeItems = useMemo(() => items.filter((item) => item.status !== "expire"), [items]);
  const pool = tab === "expired" ? expiredItems : activeItems;
  // Tags of the whole library (suggestions) and of the tab being looked at (filters), most used first.
  const libraryTags = useMemo(() => tagCounts(items), [items]);
  const poolTags = useMemo(() => tagCounts(pool), [pool]);
  const untaggedCount = useMemo(() => pool.filter((item) => !item.tags.length).length, [pool]);
  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const list = pool.filter((item) => {
      if (needle && !item.filename.toLowerCase().includes(needle) && !item.tags.some((tag) => tag.includes(needle))) return false;
      if (untagged && item.tags.length) return false;
      if (activeTags.some((tag) => !item.tags.includes(tag))) return false;
      if (filter === "unindexed") return !item.indexed;
      return filter === "all" || item.status === filter;
    });
    // Working library: closest deadline first. Expired tab: most recently expired first.
    return [...list].sort((a, b) => {
      if (sort === "name") return a.filename.localeCompare(b.filename, "fr");
      const left = a.days_left ?? Number.MAX_SAFE_INTEGER;
      const right = b.days_left ?? Number.MAX_SAFE_INTEGER;
      return (tab === "expired" ? right - left : left - right) || a.filename.localeCompare(b.filename, "fr");
    });
  }, [pool, tab, filter, sort, query, activeTags, untagged]);

  const filters: { value: Filter; label: string; dot?: Status; count?: number }[] = [
    { value: "all", label: "Tous", count: activeItems.length },
    { value: "<30j", label: "Sous 30 j", dot: "<30j", count: countStatus(activeItems, "<30j") },
    { value: "<90j", label: "Sous 90 j", dot: "<90j", count: countStatus(activeItems, "<90j") },
    { value: "ok", label: "Dans les délais", dot: "ok" },
    { value: "inconnue", label: "Sans échéance", dot: "inconnue", count: countStatus(activeItems, "inconnue") },
    { value: "unindexed", label: "Pas encore indexés", count: activeItems.filter((item) => !item.indexed).length },
  ];
  const filtering = filter !== "all" || Boolean(query) || activeTags.length > 0 || untagged;

  function changeTab(next: Tab) {
    setTab(next);
    setFilter("all");
    setActiveTags([]);
    setUntagged(false);
    setAllTags(false);
    setSelected(new Set());
    setShown(100);
  }

  function toggleTag(tag: string) {
    setActiveTags((current) => (current.includes(tag) ? current.filter((item) => item !== tag) : [...current, tag]));
    setShown(100);
  }

  function upload(files: File[]) {
    if (!admin || !files.length) return;
    if (uploader.uploading) {
      toast.show({ message: "Un envoi est déjà en cours", description: "Ajoutez ces fichiers dès qu'il est terminé." });
      return;
    }
    setFailures([]);
    setRetryable([]);
    setReplaced([]);
    void uploader.run(files).then((result) => {
      setFailures(result.failed);
      setReplaced(result.replaced);
      const refused = new Set(result.failed.map((item) => item.filename));
      setRetryable(files.filter((file) => refused.has(file.name)));
    });
  }

  async function exportCsv() {
    setExporting(true);
    try {
      await downloadFile(apiPath("/library/export-csv"), "references.csv");
    } catch (error) {
      toast.show({ tone: "error", message: "L'export n'a pas abouti", description: errorMessage(error) });
    } finally {
      setExporting(false);
    }
  }

  function onDrop(event: DragEvent) {
    event.preventDefault();
    setDragging(false);
    upload(Array.from(event.dataTransfer.files));
  }

  async function removeSelected(names: string[]) {
    const ok = await confirm({
      title: names.length > 1 ? `Supprimer ${names.length} visuels ?` : "Supprimer ce visuel ?",
      body: (
        <>
          <p>{names.length > 1 ? "Ils seront retirés" : `« ${names[0]} » sera retiré`} de la surveillance, avec les correspondances et décisions associées.</p>
          <p className="mt-2 font-medium">Cette action est définitive.</p>
        </>
      ),
      confirm: "Supprimer",
      danger: true,
    });
    if (!ok) return;
    const pending = toast.loading(names.length > 1 ? `Suppression de ${names.length} visuels…` : "Suppression du visuel…");
    mutations.remove.mutate(names, {
      onSuccess: (result) => {
        toast.update(pending, { tone: "success", message: result.deleted > 1 ? `${result.deleted} visuels supprimés` : "Visuel supprimé" });
        setSelected(new Set());
        setEditing(null);
      },
      onError: (error) => toast.update(pending, { tone: "error", message: "La suppression n'a pas abouti", description: errorMessage(error) }),
    });
  }

  function applyBulkDate() {
    const names = [...selected];
    mutations.setExpiry.mutate(
      { filenames: names, expiry_date: bulkDate },
      {
        onSuccess: (result) => {
          toast.show({
            tone: "success",
            message: bulkDate ? `Échéance fixée au ${formatDate(bulkDate)}` : "Échéance retirée",
            description: plural(result.updated, "visuel mis à jour", "visuels mis à jour"),
          });
          setSelected(new Set());
        },
        onError: (error) => toast.show({ tone: "error", message: "L'enregistrement n'a pas abouti", description: errorMessage(error) }),
      },
    );
  }

  function applyTags(mode: "add" | "remove") {
    const tags = splitTags(bulkTag);
    if (!tags.length) return;
    mutations.setTags.mutate(
      { filenames: [...selected], [mode]: tags },
      {
        onSuccess: (result) => {
          toast.show({
            tone: "success",
            message: mode === "add" ? "Tags ajoutés" : "Tags retirés",
            description: plural(result.updated, "visuel mis à jour", "visuels mis à jour"),
          });
          setBulkTag("");
        },
        onError: (error) => toast.show({ tone: "error", message: "L'enregistrement n'a pas abouti", description: errorMessage(error) }),
      },
    );
  }

  const allVisibleSelected = visible.length > 0 && visible.every((item) => selected.has(item.filename));

  return (
    <div
      onDragOver={(event) => {
        if (!admin) return;
        event.preventDefault();
        setDragging(true);
      }}
      onDragLeave={(event) => {
        if (event.currentTarget === event.target) setDragging(false);
      }}
      onDrop={onDrop}
      className={cx("relative", admin && selected.size > 0 && "pb-20", dragging && "after:pointer-events-none after:absolute after:inset-0 after:rounded-xl after:border-2 after:border-dashed after:border-focus after:bg-focus-soft/40")}
    >
      <PageHeader
        title="Bibliothèque"
        description="Les visuels sous droits, leur date d'expiration et leurs tags. C'est à eux que chaque page lue est comparée, même une fois expirés."
        actions={
          <>
            <Button variant="ghost" loading={exporting} onClick={() => void exportCsv()}>
              Exporter en CSV
            </Button>
            {admin ? <Button onClick={() => setImportOpen(true)}>Importer (CSV)</Button> : null}
            {admin ? (
              <>
                <Button variant="primary" loading={uploader.uploading} onClick={() => fileInput.current?.click()}>
                  {uploader.progress
                    ? uploader.progress.total > 1
                      ? `Envoi · ${uploader.progress.done}/${uploader.progress.total}`
                      : "Envoi en cours…"
                    : "Ajouter des visuels"}
                </Button>
                <input
                  ref={fileInput}
                  type="file"
                  accept="image/jpeg,image/png,image/webp,image/gif,image/avif,image/tiff,image/bmp"
                  multiple
                  hidden
                  onChange={(event) => {
                    upload(Array.from(event.target.files ?? []));
                    event.target.value = "";
                  }}
                />
              </>
            ) : null}
          </>
        }
      />

      {dragging ? (
        <p className="pointer-events-none fixed inset-x-0 top-6 z-40 mx-auto w-fit rounded-full bg-ink px-4 py-2 text-sm text-white shadow-lg">
          Déposez pour ajouter à la bibliothèque de {brand.name}
        </p>
      ) : null}

      {library.data?.indexing ? (
        <p className="mb-5 flex items-center gap-2.5 rounded-xl bg-peach-soft px-4 py-3 text-sm text-bark-800">
          <span aria-hidden className="h-3.5 w-3.5 shrink-0 animate-spin rounded-full border-2 border-bark-800/25 border-t-bark-800" />
          Indexation en cours : les nouveaux visuels seront comparés aux sites dès qu'elle sera terminée.
        </p>
      ) : null}

      {replaced.length ? (
        <Card className="mb-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium">
                {replaced.length > 1 ? `${replaced.length} visuels remplacés` : "1 visuel remplacé"}
              </p>
              <p className="mt-0.5 text-[13px] text-muted">
                Un visuel du même nom était déjà dans la bibliothèque : l'image a été remplacée, son échéance, son crédit et ses notes sont conservés.
              </p>
              <p className="mt-1 break-words text-[13px] text-ink-soft">{replaced.join(", ")}</p>
            </div>
            <Button size="sm" variant="ghost" onClick={() => setReplaced([])}>Fermer</Button>
          </div>
        </Card>
      ) : null}

      {failures.length ? (
        <Card className="mb-4 border-expired/30">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium text-expired">{plural(failures.length, "fichier refusé", "fichiers refusés")}</p>
              <div className="mt-2 grid gap-3">
                {byReason(failures).map(([reason, items]) => (
                  <div key={reason}>
                    <p className="text-[13px] font-medium text-ink">{reason}</p>
                    <p className="mt-0.5 break-words text-[13px] text-ink-soft">{items.join(", ")}</p>
                  </div>
                ))}
              </div>
            </div>
            <div className="flex gap-1">
              {retryable.length ? (
                <Button size="sm" loading={uploader.uploading} onClick={() => upload(retryable)}>
                  Réessayer {retryable.length > 1 ? `les ${retryable.length}` : ""}
                </Button>
              ) : null}
              <Button size="sm" variant="ghost" onClick={() => { setFailures([]); setRetryable([]); }}>Fermer</Button>
            </div>
          </div>
        </Card>
      ) : null}

      {library.isLoading ? (
        <div className="grid gap-2">{Array.from({ length: 6 }, (_, index) => <Skeleton key={index} className="h-14" />)}</div>
      ) : library.error ? (
        <EmptyState title="Impossible de charger la bibliothèque" body={errorMessage(library.error)} />
      ) : !items.length ? (
        <EmptyState
          title="Aucun visuel pour l'instant"
          body={admin ? "Glissez vos images ici, ou utilisez « Ajouter des visuels ». Vous renseignerez ensuite leur date d'expiration, une par une ou par import CSV." : "Un administrateur doit d'abord déposer les visuels à surveiller."}
          action={admin ? <Button variant="primary" loading={uploader.uploading} onClick={() => fileInput.current?.click()}>Ajouter des visuels</Button> : undefined}
        />
      ) : (
        <>
          <div className="mb-4">
            <Segmented
              label="Bibliothèque"
              value={tab}
              onChange={changeTab}
              options={[
                { value: "active", label: `Actifs · ${activeItems.length.toLocaleString("fr-FR")}` },
                { value: "expired", label: `Expirés · ${expiredItems.length.toLocaleString("fr-FR")}` },
              ]}
            />
          </div>

          {tab === "expired" ? (
            <p className="mb-4 rounded-xl bg-side px-4 py-3 text-sm text-ink-soft">
              Ces visuels ne sont plus sous droits, mais les sites continuent d'être comparés à eux : ceux qui y sont encore en ligne sont dans « À traiter ».
              {admin ? " Pour en renouveler un, donnez-lui une nouvelle échéance : il revient dans les visuels actifs." : ""}{" "}
              <Link to={`${link("a-traiter")}?statut=expire`} className="font-medium text-ink underline underline-offset-2">Voir ce qui est encore en ligne</Link>
            </p>
          ) : null}

          <div className="mb-3 flex flex-wrap items-center gap-2">
            <SearchField id="lib-search" placeholder="Rechercher un nom ou un tag" className="w-full sm:w-72" value={query} onChange={(value) => { setQuery(value); setShown(100); }} />
            {tab === "active" ? (
              <div className="flex gap-2 overflow-x-auto pb-0.5 [scrollbar-width:none]">
                {filters.map((item) => (
                  <Chip key={item.value} active={filter === item.value} dot={item.dot} count={item.count} onClick={() => { setFilter(item.value); setShown(100); }}>
                    {item.label}
                  </Chip>
                ))}
              </div>
            ) : null}
            <div className="ml-auto">
              <Segmented label="Trier par" value={sort} onChange={setSort} options={[{ value: "expiry", label: "Échéance" }, { value: "name", label: "Nom" }]} />
            </div>
          </div>

          {poolTags.length ? (
            <div className="mb-5 flex flex-wrap items-center gap-2" role="group" aria-label="Filtrer par tag">
              <span className="text-[12.5px] text-muted">Tags</span>
              {(allTags ? poolTags : poolTags.slice(0, TAGS_SHOWN)).map(([tag, count]) => (
                <Chip key={tag} active={activeTags.includes(tag)} count={count} onClick={() => toggleTag(tag)}>{tag}</Chip>
              ))}
              {poolTags.length > TAGS_SHOWN ? (
                <button type="button" className="text-[13px] text-muted underline underline-offset-2 hover:text-ink" onClick={() => setAllTags((value) => !value)}>
                  {allTags ? "Moins de tags" : `Voir les ${poolTags.length} tags`}
                </button>
              ) : null}
              {untaggedCount ? (
                <Chip active={untagged} count={untaggedCount} onClick={() => { setUntagged((value) => !value); setShown(100); }}>Sans tag</Chip>
              ) : null}
            </div>
          ) : (
            <div className="mb-5" />
          )}

          <datalist id="library-tags">
            {libraryTags.map(([tag]) => <option key={tag} value={tag} />)}
          </datalist>

          {admin && selected.size ? (
            <div className="fixed inset-x-4 bottom-4 z-30 mx-auto flex max-w-3xl flex-wrap items-center gap-3 rounded-2xl bg-ink px-4 py-2.5 text-sm text-paper shadow-float md:left-[calc(256px+3.5rem)]" role="region" aria-label="Actions sur la sélection">
              <span className="font-medium">{plural(selected.size, "sélectionné")}</span>
              <span className="flex items-center gap-2">
                <label htmlFor="bulk-date" className="text-white/70">Échéance</label>
                <input id="bulk-date" type="date" value={bulkDate} onChange={(event) => setBulkDate(event.target.value)} className="h-9 rounded-[10px] border border-white/20 bg-white/10 px-2.5 text-paper [color-scheme:dark]" />
                <button type="button" className="h-9 rounded-[10px] bg-peach px-3.5 text-[13.5px] font-medium text-bark-800 hover:bg-[#f9bd98] disabled:cursor-progress disabled:opacity-60" onClick={applyBulkDate} disabled={mutations.setExpiry.isPending}>
                  {mutations.setExpiry.isPending ? "Application…" : "Appliquer"}
                </button>
              </span>
              <span className="flex items-center gap-2">
                <label htmlFor="bulk-tag" className="text-white/70">Tags</label>
                <input
                  id="bulk-tag"
                  list="library-tags"
                  value={bulkTag}
                  maxLength={120}
                  placeholder="magnum, classic"
                  onChange={(event) => setBulkTag(event.target.value)}
                  onKeyDown={(event) => { if (event.key === "Enter") applyTags("add"); }}
                  className="h-9 w-40 rounded-[10px] border border-white/20 bg-white/10 px-2.5 text-paper placeholder:text-white/40"
                />
                <button type="button" className="h-9 rounded-[10px] bg-peach px-3.5 text-[13.5px] font-medium text-bark-800 hover:bg-[#f9bd98] disabled:cursor-not-allowed disabled:opacity-50" onClick={() => applyTags("add")} disabled={mutations.setTags.isPending || !splitTags(bulkTag).length}>
                  Ajouter
                </button>
                <button type="button" className="h-9 rounded-[10px] px-3 text-[13.5px] text-paper hover:bg-white/10 disabled:cursor-not-allowed disabled:opacity-50" onClick={() => applyTags("remove")} disabled={mutations.setTags.isPending || !splitTags(bulkTag).length}>
                  Retirer
                </button>
              </span>
              <button type="button" className="h-9 rounded-[10px] px-3 text-[13.5px] text-[#f4b3a8] hover:bg-white/10 disabled:cursor-progress disabled:opacity-60" disabled={mutations.remove.isPending} onClick={() => void removeSelected([...selected])}>
                {mutations.remove.isPending ? "Suppression…" : "Supprimer"}
              </button>
              <button type="button" className="ml-auto text-white/70 hover:text-white" onClick={() => setSelected(new Set())}>Tout désélectionner</button>
            </div>
          ) : null}

          {admin && visible.length ? (
            <label className="mb-3 inline-flex items-center gap-2 text-[13px] text-muted">
              <input
                type="checkbox"
                className="h-4 w-4 accent-ink"
                checked={allVisibleSelected}
                onChange={(event) => setSelected(event.target.checked ? new Set(visible.map((item) => item.filename)) : new Set())}
              />
              Tout sélectionner
            </label>
          ) : null}
          <ul className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
            {admin && tab === "active" && !filtering ? (
              <li>
                <button
                  type="button"
                  onClick={() => fileInput.current?.click()}
                  className="flex h-full min-h-56 w-full flex-col items-center justify-center gap-2.5 rounded-2xl border-[1.5px] border-dashed border-line-strong bg-sunk p-4 text-center transition-colors hover:border-bark hover:bg-peach-soft/40"
                >
                  <span className="grid h-11 w-11 place-items-center rounded-xl bg-peach-soft text-bark-700"><Icon name="upload" size={20} /></span>
                  <span className="text-sm font-medium">Déposez des images</span>
                  <span className="text-[12.5px] leading-snug text-muted">ou cliquez pour choisir. JPG, PNG, WebP…</span>
                </button>
              </li>
            ) : null}
            {visible.slice(0, shown).map((item) => {
              const checked = selected.has(item.filename);
              return (
                <li
                  key={item.id}
                  className={cx(
                    "group relative flex flex-col overflow-hidden rounded-2xl border bg-paper transition-shadow hover:shadow-float",
                    checked ? "border-[#e6d3c2] shadow-[0_0_0_3px_var(--color-peach-soft)]" : "border-line",
                  )}
                >
                  <button type="button" onClick={() => setEditing(item)} className="block text-left" aria-label={`${admin ? "Modifier" : "Voir"} ${item.filename}`}>
                    <span className="block aspect-[4/3] overflow-hidden bg-side">
                      {item.thumb_url ? <img src={item.thumb_url} alt="" loading="lazy" className="h-full w-full object-cover" /> : null}
                    </span>
                    <span className="flex flex-col gap-2 px-3.5 pt-3 pb-3.5">
                      <span className="truncate text-[13.5px] font-medium" title={item.filename}>{item.filename}</span>
                      <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
                        <StatusBadge status={item.status} label={item.status === "inconnue" ? undefined : daysText(item.days_left)} />
                        {item.expiry_date ? <span className="text-[11.5px] text-muted tabular">{formatDate(item.expiry_date)}</span> : null}
                      </span>
                      {item.tags.length ? (
                        <span className="flex flex-wrap gap-1">
                          {item.tags.slice(0, 3).map((tag) => (
                            <span key={tag} className="rounded-full bg-side px-2 py-0.5 text-[11px] text-ink-soft">{tag}</span>
                          ))}
                          {item.tags.length > 3 ? <span className="px-1 py-0.5 text-[11px] text-muted">+{item.tags.length - 3}</span> : null}
                        </span>
                      ) : null}
                      {!item.indexed ? <span className="text-[11.5px] text-urgent">Indexation en attente</span> : null}
                    </span>
                  </button>
                  {admin ? (
                    <input
                      type="checkbox"
                      className={cx("absolute top-2.5 left-2.5 h-5 w-5 accent-ink transition-opacity", checked || selected.size ? "opacity-100" : "opacity-0 group-hover:opacity-100 focus-visible:opacity-100")}
                      aria-label={`Sélectionner ${item.filename}`}
                      checked={checked}
                      onChange={(event) => {
                        const next = new Set(selected);
                        if (event.target.checked) next.add(item.filename);
                        else next.delete(item.filename);
                        setSelected(next);
                      }}
                    />
                  ) : null}
                </li>
              );
            })}
          </ul>
          {visible.length > shown ? (
            <div className="mt-6 text-center">
              <Button onClick={() => setShown((value) => value + 100)}>Afficher plus · {shown}/{visible.length}</Button>
            </div>
          ) : null}
          {!visible.length ? (
            <p className="py-10 text-center text-sm text-muted">
              {tab === "expired" && !filtering ? "Aucun visuel expiré : tout ce que vous surveillez est encore sous droits." : "Aucun visuel ne correspond à ces filtres."}
            </p>
          ) : null}
        </>
      )}

      <EditReference item={editing} onClose={() => setEditing(null)} onDelete={(item) => void removeSelected([item.filename])} />
      <ImportCsv open={importOpen} onClose={() => setImportOpen(false)} />
    </div>
  );
}

function EditReference(props: { item: LibraryItem | null; onClose: () => void; onDelete: (item: LibraryItem) => void }) {
  const { admin } = useOrg();
  const { updateMeta } = useLibraryMutations();
  const toast = useToast();
  const item = props.item;
  const [form, setForm] = useState<{ expiry_date: string; credit: string; notes: string; tags: string[] }>({ expiry_date: "", credit: "", notes: "", tags: [] });
  const [loadedFor, setLoadedFor] = useState<string | null>(null);
  if (item && loadedFor !== item.id) {
    setLoadedFor(item.id);
    setForm({ expiry_date: item.expiry_date, credit: item.credit, notes: item.notes, tags: item.tags });
  }
  if (!item) return null;

  function save() {
    if (!item) return;
    updateMeta.mutate(
      { filename: item.filename, ...form },
      {
        onSuccess: () => {
          toast.show({ tone: "success", message: "Modifications enregistrées", description: item.filename, duration: 3000 });
          props.onClose();
        },
        onError: (error) => toast.show({ tone: "error", message: "L'enregistrement n'a pas abouti", description: errorMessage(error) }),
      },
    );
  }

  return (
    <Modal
      open
      onClose={props.onClose}
      title={item.filename}
      wide
      footer={
        admin ? (
          <>
            <Button variant="danger" className="mr-auto" onClick={() => props.onDelete(item)}>Supprimer</Button>
            <Button onClick={props.onClose}>Annuler</Button>
            <Button variant="primary" onClick={save} loading={updateMeta.isPending}>Enregistrer</Button>
          </>
        ) : (
          <Button onClick={props.onClose}>Fermer</Button>
        )
      }
    >
      <div className="grid gap-5 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <a href={item.url} target="_blank" rel="noreferrer noopener" className="block overflow-hidden rounded-2xl bg-side">
          <img src={item.url || item.thumb_url} alt="" className="aspect-square w-full object-contain" />
        </a>
        <div className="grid content-start gap-4">
          <div>
            <FieldLabel htmlFor="edit-expiry">Date d'expiration des droits</FieldLabel>
            <Input id="edit-expiry" type="date" disabled={!admin} value={form.expiry_date} onChange={(event) => setForm({ ...form, expiry_date: event.target.value })} />
          </div>
          <div>
            <FieldLabel htmlFor="edit-credit">Crédit</FieldLabel>
            <Input id="edit-credit" disabled={!admin} placeholder="Photographe, agence" value={form.credit} onChange={(event) => setForm({ ...form, credit: event.target.value })} />
          </div>
          <div>
            <FieldLabel htmlFor="edit-tags" hint="produit, campagne, shooting…">Tags</FieldLabel>
            <TagEditor id="edit-tags" tags={form.tags} disabled={!admin} onChange={(tags) => setForm({ ...form, tags })} />
          </div>
          <div>
            <FieldLabel htmlFor="edit-notes">Notes</FieldLabel>
            <textarea
              id="edit-notes"
              disabled={!admin}
              rows={4}
              placeholder="Usages autorisés, territoires, numéro de contrat…"
              value={form.notes}
              onChange={(event) => setForm({ ...form, notes: event.target.value })}
              className="w-full rounded-[10px] border border-line-strong bg-paper px-3.5 py-2.5 text-sm outline-none focus:border-focus focus:ring-4 focus:ring-focus-soft disabled:bg-canvas"
            />
          </div>
          <p className="text-xs text-muted">
            {item.width && item.height ? `${item.width} × ${item.height} px · ` : ""}
            {item.indexed ? (item.compared ? "Indexé et comparé au site" : "Indexé, pas encore comparé") : "Indexation en attente"}
          </p>
        </div>
      </div>
    </Modal>
  );
}

/** Tags with the number of visuals carrying each, most used first. */
function tagCounts(items: LibraryItem[]): [string, number][] {
  const counts = new Map<string, number>();
  for (const item of items) for (const tag of item.tags) counts.set(tag, (counts.get(tag) ?? 0) + 1);
  return [...counts].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], "fr"));
}

/** Chips plus a text field: Enter or a comma adds a tag, Backspace on an empty field removes the last one. Suggestions come from the library's `#library-tags` list. */
function TagEditor(props: { id: string; tags: string[]; onChange: (tags: string[]) => void; disabled?: boolean }) {
  const [text, setText] = useState("");

  function commit(raw: string) {
    setText("");
    const added = splitTags(raw).filter((tag) => !props.tags.includes(tag));
    if (added.length) props.onChange([...props.tags, ...added].slice(0, MAX_TAGS));
  }

  return (
    <div className="flex flex-wrap gap-1.5 rounded-[10px] border border-line-strong bg-paper p-2 focus-within:border-focus focus-within:ring-4 focus-within:ring-focus-soft has-[input:disabled]:bg-canvas">
      {props.tags.map((tag) => (
        <span key={tag} className="inline-flex items-center gap-1 rounded-full bg-side py-1 pr-1.5 pl-2.5 text-[12.5px]">
          {tag}
          {!props.disabled ? (
            <button type="button" aria-label={`Retirer le tag ${tag}`} className="grid h-4 w-4 place-items-center rounded-full text-muted hover:bg-line hover:text-ink" onClick={() => props.onChange(props.tags.filter((item) => item !== tag))}>
              ×
            </button>
          ) : null}
        </span>
      ))}
      {!props.disabled && props.tags.length < MAX_TAGS ? (
        <input
          id={props.id}
          list="library-tags"
          value={text}
          maxLength={120}
          placeholder={props.tags.length ? "" : "miniature, classic…"}
          onChange={(event) => (/[,;|]/.test(event.target.value) ? commit(event.target.value) : setText(event.target.value))}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              commit(text);
            } else if (event.key === "Backspace" && !text && props.tags.length) {
              props.onChange(props.tags.slice(0, -1));
            }
          }}
          onBlur={() => commit(text)}
          // The container already shows the focus ring; the global :focus-visible outline would double it.
          style={{ outline: "none" }}
          className="min-w-[8rem] flex-1 bg-transparent px-1 py-1 text-sm placeholder:text-faint"
        />
      ) : props.disabled && !props.tags.length ? (
        <span className="px-1 py-1 text-sm text-muted">Aucun tag</span>
      ) : null}
    </div>
  );
}

const importStatus: Record<ImportRow["status"], string> = {
  ok: "Prêt",
  unknown_file: "Fichier inconnu",
  bad_date: "Date invalide",
  bad_tags: "Tag invalide",
};

function ImportCsv(props: { open: boolean; onClose: () => void }) {
  const { importCsv } = useLibraryMutations();
  const toast = useToast();
  const [file, setFile] = useState<File | null>(null);
  const [rows, setRows] = useState<ImportRow[] | null>(null);

  function close() {
    setFile(null);
    setRows(null);
    props.onClose();
  }

  function preview(next: File) {
    setFile(next);
    importCsv.mutate({ file: next, apply: false }, { onSuccess: (data) => setRows(data.rows), onError: (error) => toast.show({ tone: "error", message: "Le fichier n'a pas pu être lu", description: errorMessage(error) }) });
  }

  function apply() {
    if (!file) return;
    importCsv.mutate(
      { file, apply: true },
      {
        onSuccess: (data) => {
          toast.show({
            tone: "success",
            message: `${plural(data.applied, "visuel mis à jour", "visuels mis à jour")}`,
            description: "Le tableau de bord en tient compte dès maintenant.",
          });
          close();
        },
        onError: (error) => toast.show({ tone: "error", message: "L'enregistrement n'a pas abouti", description: errorMessage(error) }),
      },
    );
  }

  const ready = rows?.filter((row) => row.status === "ok").length ?? 0;
  return (
    <Modal
      open={props.open}
      onClose={close}
      title="Importer depuis un CSV"
      wide
      footer={
        <>
          <Button onClick={close}>Annuler</Button>
          <Button variant="primary" disabled={!ready} loading={importCsv.isPending && rows !== null} onClick={apply}>
            {ready ? `Appliquer ${plural(ready, "ligne")}` : "Appliquer"}
          </Button>
        </>
      }
    >
      <p className="text-sm text-ink-soft">
        Colonnes attendues : <code className="rounded bg-canvas px-1">filename</code>, <code className="rounded bg-canvas px-1">expiry_date</code>, et en option <code className="rounded bg-canvas px-1">credit</code>, <code className="rounded bg-canvas px-1">notes</code>, <code className="rounded bg-canvas px-1">tags</code> (séparés par des virgules). Dates au format AAAA-MM-JJ ou JJ/MM/AAAA. Sans colonne <code className="rounded bg-canvas px-1">tags</code>, les tags existants ne sont pas touchés ; avec, ils sont remplacés. Rien n'est modifié avant de cliquer sur « Appliquer ».
      </p>
      <input
        type="file"
        accept=".csv,text/csv"
        className="mt-4 block text-sm"
        onChange={(event) => {
          const next = event.target.files?.[0];
          if (next) preview(next);
        }}
      />
      {importCsv.isPending && !rows ? (
        <div className="mt-4"><Spinner label="Lecture du fichier…" /></div>
      ) : null}
      {rows ? (
        <div className="mt-4 max-h-80 overflow-y-auto rounded-lg border border-line">
          <table className="w-full text-[13px]">
            <thead className="sticky top-0 bg-paper">
              <tr className="border-b border-line text-left text-muted">
                <th className="px-3 py-2 font-medium">Ligne</th>
                <th className="px-3 py-2 font-medium">Fichier</th>
                <th className="px-3 py-2 font-medium">Échéance</th>
                <th className="px-3 py-2 font-medium">Tags</th>
                <th className="px-3 py-2 font-medium">État</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {rows.map((row) => (
                <tr key={`${row.line}-${row.filename}`}>
                  <td className="px-3 py-1.5 text-muted tabular">{row.line}</td>
                  <td className="max-w-[240px] truncate px-3 py-1.5">{row.filename}</td>
                  <td className="px-3 py-1.5 tabular">{row.expiry_date ? formatDate(row.expiry_date) : "·"}</td>
                  <td className="max-w-[200px] truncate px-3 py-1.5" title={row.tags?.join(", ")}>{row.tags === null ? <span className="text-muted">inchangés</span> : row.tags.length ? row.tags.join(", ") : "·"}</td>
                  <td className={cx("px-3 py-1.5", row.status === "ok" ? "text-ok" : "text-expired")} title={row.message}>
                    {importStatus[row.status]}
                    {row.message && row.status !== "ok" && row.status !== "unknown_file" ? <span className="block text-xs text-muted">{row.message}</span> : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </Modal>
  );
}

function countStatus(items: LibraryItem[], status: Status): number {
  return items.filter((item) => item.status === status).length;
}
