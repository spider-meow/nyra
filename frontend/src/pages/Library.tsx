import { useMemo, useRef, useState, type DragEvent } from "react";
import { Modal, useConfirm, useToast } from "../components/feedback";
import { Icon } from "../components/icons";
import { Button, Card, Chip, EmptyState, FieldLabel, Input, PageHeader, SearchField, Segmented, Skeleton, StatusBadge, cx } from "../components/ui";
import { downloadFile, errorMessage } from "../lib/api";
import { daysText, formatDate, plural } from "../lib/format";
import { useOrg } from "../lib/org";
import { useLibrary, useLibraryMutations } from "../lib/queries";
import type { ImportRow, LibraryItem, Status } from "../types";

type Filter = "all" | Status | "unindexed";
type Sort = "expiry" | "name";

export function Library() {
  const { admin, apiPath } = useOrg();
  const library = useLibrary();
  const mutations = useLibraryMutations();
  const toast = useToast();
  const confirm = useConfirm();
  const fileInput = useRef<HTMLInputElement>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [sort, setSort] = useState<Sort>("expiry");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [editing, setEditing] = useState<LibraryItem | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [bulkDate, setBulkDate] = useState("");
  const [shown, setShown] = useState(100);
  const [failures, setFailures] = useState<{ filename: string; reason: string }[]>([]);

  const items = library.data?.items ?? [];
  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const list = items.filter((item) => {
      if (needle && !item.filename.toLowerCase().includes(needle)) return false;
      if (filter === "unindexed") return !item.indexed;
      return filter === "all" || item.status === filter;
    });
    return [...list].sort((a, b) => {
      if (sort === "name") return a.filename.localeCompare(b.filename, "fr");
      const left = a.days_left ?? Number.MAX_SAFE_INTEGER;
      const right = b.days_left ?? Number.MAX_SAFE_INTEGER;
      return left - right || a.filename.localeCompare(b.filename, "fr");
    });
  }, [items, filter, sort, query]);

  const filters: { value: Filter; label: string; dot?: Status; count?: number }[] = [
    { value: "all", label: "Tous", count: items.length },
    { value: "expire", label: "Expirés", dot: "expire", count: countStatus(items, "expire") },
    { value: "<30j", label: "Sous 30 j", dot: "<30j", count: countStatus(items, "<30j") },
    { value: "<90j", label: "Sous 90 j", dot: "<90j", count: countStatus(items, "<90j") },
    { value: "ok", label: "Dans les délais", dot: "ok" },
    { value: "inconnue", label: "Sans échéance", dot: "inconnue", count: countStatus(items, "inconnue") },
    { value: "unindexed", label: "Pas encore indexés", count: items.filter((item) => !item.indexed).length },
  ];


  function upload(files: File[]) {
    if (!admin || !files.length) return;
    setFailures([]);
    mutations.upload.mutate(files, {
      onSuccess: (result) => {
        if (result.saved.length) toast(`${plural(result.saved.length, "visuel ajouté", "visuels ajoutés")}. L'indexation démarre.`, "success");
        setFailures(result.failed);
      },
      onError: (error) => toast(errorMessage(error), "error"),
    });
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
    mutations.remove.mutate(names, {
      onSuccess: (result) => {
        toast(`${plural(result.deleted, "visuel supprimé", "visuels supprimés")}.`);
        setSelected(new Set());
        setEditing(null);
      },
      onError: (error) => toast(errorMessage(error), "error"),
    });
  }

  function applyBulkDate() {
    const names = [...selected];
    mutations.setExpiry.mutate(
      { filenames: names, expiry_date: bulkDate },
      {
        onSuccess: (result) => {
          toast(bulkDate ? `Échéance fixée au ${formatDate(bulkDate)} pour ${plural(result.updated, "visuel")}.` : `Échéance retirée pour ${plural(result.updated, "visuel")}.`);
          setSelected(new Set());
        },
        onError: (error) => toast(errorMessage(error), "error"),
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
        description="Les visuels sous droits et leur date d'expiration. C'est à eux que chaque page lue est comparée."
        actions={
          <>
            <Button variant="ghost" onClick={() => void downloadFile(apiPath("/library/export-csv"), "references.csv").catch((error) => toast(errorMessage(error), "error"))}>
              Exporter en CSV
            </Button>
            {admin ? <Button onClick={() => setImportOpen(true)}>Importer des dates (CSV)</Button> : null}
            {admin ? (
              <>
                <Button variant="primary" disabled={mutations.upload.isPending} onClick={() => fileInput.current?.click()}>
                  {mutations.upload.isPending ? "Envoi en cours…" : "Ajouter des visuels"}
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

      {library.data?.indexing ? (
        <p className="mb-5 rounded-xl bg-peach-soft px-4 py-3 text-sm text-bark-800">Indexation en cours : les nouveaux visuels seront comparés au site dès qu'elle sera terminée.</p>
      ) : null}

      {failures.length ? (
        <Card className="mb-4 border-expired/30">
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="text-sm font-medium text-expired">{plural(failures.length, "fichier refusé", "fichiers refusés")}</p>
              <ul className="mt-1 text-[13px] text-ink-soft">
                {failures.map((item) => <li key={item.filename}>{item.filename} : {item.reason}</li>)}
              </ul>
            </div>
            <Button size="sm" variant="ghost" onClick={() => setFailures([])}>Fermer</Button>
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
          action={admin ? <Button variant="primary" onClick={() => fileInput.current?.click()}>Ajouter des visuels</Button> : undefined}
        />
      ) : (
        <>
          <div className="mb-5 flex flex-wrap items-center gap-2">
            <SearchField id="lib-search" placeholder="Rechercher un visuel" className="w-full sm:w-72" value={query} onChange={(value) => { setQuery(value); setShown(100); }} />
            <div className="flex gap-2 overflow-x-auto pb-0.5 [scrollbar-width:none]">
              {filters.map((item) => (
                <Chip key={item.value} active={filter === item.value} dot={item.dot} count={item.count} onClick={() => { setFilter(item.value); setShown(100); }}>
                  {item.label}
                </Chip>
              ))}
            </div>
            <div className="ml-auto">
              <Segmented label="Trier par" value={sort} onChange={setSort} options={[{ value: "expiry", label: "Échéance" }, { value: "name", label: "Nom" }]} />
            </div>
          </div>

          {admin && selected.size ? (
            <div className="fixed inset-x-4 bottom-4 z-30 mx-auto flex max-w-3xl flex-wrap items-center gap-3 rounded-2xl bg-ink px-4 py-2.5 text-sm text-paper shadow-float md:left-[calc(256px+3.5rem)]" role="region" aria-label="Actions sur la sélection">
              <span className="font-medium">{plural(selected.size, "sélectionné")}</span>
              <span className="flex items-center gap-2">
                <label htmlFor="bulk-date" className="text-white/70">Échéance</label>
                <input id="bulk-date" type="date" value={bulkDate} onChange={(event) => setBulkDate(event.target.value)} className="h-9 rounded-[10px] border border-white/20 bg-white/10 px-2.5 text-paper [color-scheme:dark]" />
                <button type="button" className="h-9 rounded-[10px] bg-peach px-3.5 text-[13.5px] font-medium text-bark-800 hover:bg-[#f9bd98] disabled:opacity-40" onClick={applyBulkDate} disabled={mutations.setExpiry.isPending}>Appliquer</button>
              </span>
              <button type="button" className="h-9 rounded-[10px] px-3 text-[13.5px] text-[#f4b3a8] hover:bg-white/10" onClick={() => void removeSelected([...selected])}>Supprimer</button>
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
            {admin && filter === "all" && !query ? (
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
          {!visible.length ? <p className="py-10 text-center text-sm text-muted">Aucun visuel ne correspond à ces filtres.</p> : null}
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
  const [form, setForm] = useState({ expiry_date: "", credit: "", notes: "" });
  const [loadedFor, setLoadedFor] = useState<string | null>(null);
  if (item && loadedFor !== item.id) {
    setLoadedFor(item.id);
    setForm({ expiry_date: item.expiry_date, credit: item.credit, notes: item.notes });
  }
  if (!item) return null;

  function save() {
    if (!item) return;
    updateMeta.mutate(
      { filename: item.filename, ...form },
      {
        onSuccess: () => {
          toast("Modifications enregistrées.", "success");
          props.onClose();
        },
        onError: (error) => toast(errorMessage(error), "error"),
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
            <Button variant="primary" onClick={save} disabled={updateMeta.isPending}>Enregistrer</Button>
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

const importStatus: Record<ImportRow["status"], string> = {
  ok: "Prêt",
  unknown_file: "Fichier inconnu",
  bad_date: "Date invalide",
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
    importCsv.mutate({ file: next, apply: false }, { onSuccess: (data) => setRows(data.rows), onError: (error) => toast(errorMessage(error), "error") });
  }

  function apply() {
    if (!file) return;
    importCsv.mutate(
      { file, apply: true },
      {
        onSuccess: (data) => {
          toast(`${plural(data.applied, "ligne appliquée", "lignes appliquées")}.`, "success");
          close();
        },
        onError: (error) => toast(errorMessage(error), "error"),
      },
    );
  }

  const ready = rows?.filter((row) => row.status === "ok").length ?? 0;
  return (
    <Modal
      open={props.open}
      onClose={close}
      title="Importer des dates depuis un CSV"
      wide
      footer={
        <>
          <Button onClick={close}>Annuler</Button>
          <Button variant="primary" disabled={!ready || importCsv.isPending} onClick={apply}>
            {ready ? `Appliquer ${plural(ready, "ligne")}` : "Appliquer"}
          </Button>
        </>
      }
    >
      <p className="text-sm text-ink-soft">
        Colonnes attendues : <code className="rounded bg-canvas px-1">filename</code>, <code className="rounded bg-canvas px-1">expiry_date</code>, et en option <code className="rounded bg-canvas px-1">credit</code>, <code className="rounded bg-canvas px-1">notes</code>. Dates au format AAAA-MM-JJ ou JJ/MM/AAAA. Rien n'est modifié avant de cliquer sur « Appliquer ».
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
      {rows ? (
        <div className="mt-4 max-h-80 overflow-y-auto rounded-lg border border-line">
          <table className="w-full text-[13px]">
            <thead className="sticky top-0 bg-paper">
              <tr className="border-b border-line text-left text-muted">
                <th className="px-3 py-2 font-medium">Ligne</th>
                <th className="px-3 py-2 font-medium">Fichier</th>
                <th className="px-3 py-2 font-medium">Échéance</th>
                <th className="px-3 py-2 font-medium">État</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {rows.map((row) => (
                <tr key={`${row.line}-${row.filename}`}>
                  <td className="px-3 py-1.5 text-muted tabular">{row.line}</td>
                  <td className="max-w-[240px] truncate px-3 py-1.5">{row.filename}</td>
                  <td className="px-3 py-1.5 tabular">{row.expiry_date ? formatDate(row.expiry_date) : "·"}</td>
                  <td className={cx("px-3 py-1.5", row.status === "ok" ? "text-ok" : "text-expired")} title={row.message}>
                    {importStatus[row.status]}
                    {row.message && row.status === "bad_date" ? <span className="block text-xs text-muted">{row.message}</span> : null}
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
