import { useMemo, useRef, useState, type DragEvent } from "react";
import { Modal, useConfirm, useToast } from "../components/feedback";
import { Button, Card, EmptyState, FieldLabel, Input, PageHeader, Select, Skeleton, StatusBadge, Thumb, cx } from "../components/ui";
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

  const counts = useMemo(() => {
    const out: Record<string, number> = { expire: 0, "<30j": 0, inconnue: 0, unindexed: 0 };
    for (const item of items) {
      out[item.status] = (out[item.status] ?? 0) + 1;
      if (!item.indexed) out.unindexed += 1;
    }
    return out;
  }, [items]);

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

  function saveExpiry(item: LibraryItem, value: string) {
    mutations.updateMeta.mutate(
      { filename: item.filename, expiry_date: value, credit: item.credit, notes: item.notes },
      { onError: (error) => toast(errorMessage(error), "error") },
    );
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
        <p className="mb-4 rounded-lg bg-focus-soft px-4 py-2.5 text-sm">Indexation en cours : les nouveaux visuels seront comparés au site dès qu'elle sera terminée.</p>
      ) : null}

      {failures.length ? (
        <Card className="mb-4 border-expired/30">
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="text-sm font-medium text-expired">{plural(failures.length, "fichier refusé", "fichiers refusés")}</p>
              <ul className="mt-1 text-[13px] text-ink-soft">
                {failures.map((item) => <li key={item.filename}>{item.filename} — {item.reason}</li>)}
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
          <div className="mb-3 flex flex-wrap items-end gap-3">
            <div className="w-full sm:w-64">
              <FieldLabel htmlFor="lib-search">Rechercher</FieldLabel>
              <Input id="lib-search" type="search" placeholder="Nom de fichier" value={query} onChange={(event) => { setQuery(event.target.value); setShown(100); }} />
            </div>
            <div className="w-full sm:w-56">
              <FieldLabel htmlFor="lib-filter">Afficher</FieldLabel>
              <Select id="lib-filter" value={filter} onChange={(event) => { setFilter(event.target.value as Filter); setShown(100); }}>
                <option value="all">Tous ({items.length})</option>
                <option value="expire">Expirés ({counts.expire})</option>
                <option value="<30j">Moins de 30 jours ({counts["<30j"]})</option>
                <option value="<90j">Moins de 90 jours</option>
                <option value="ok">Dans les délais</option>
                <option value="inconnue">Sans échéance ({counts.inconnue})</option>
                <option value="unindexed">Pas encore indexés ({counts.unindexed})</option>
              </Select>
            </div>
            <div className="w-full sm:w-44">
              <FieldLabel htmlFor="lib-sort">Trier par</FieldLabel>
              <Select id="lib-sort" value={sort} onChange={(event) => setSort(event.target.value as Sort)}>
                <option value="expiry">Échéance</option>
                <option value="name">Nom</option>
              </Select>
            </div>
          </div>

          {admin && selected.size ? (
            <div className="fixed inset-x-4 bottom-4 z-30 mx-auto flex max-w-3xl flex-wrap items-center gap-3 rounded-xl bg-ink px-4 py-2.5 text-sm text-white shadow-xl md:left-[calc(248px+2.5rem)]" role="region" aria-label="Actions sur la sélection">
              <span className="font-medium">{plural(selected.size, "sélectionné")}</span>
              <span className="flex items-center gap-2">
                <label htmlFor="bulk-date" className="text-white/70">Échéance</label>
                <input id="bulk-date" type="date" value={bulkDate} onChange={(event) => setBulkDate(event.target.value)} className="h-8 rounded-md border border-white/20 bg-white/10 px-2 text-white [color-scheme:dark]" />
                <Button size="sm" onClick={applyBulkDate} disabled={mutations.setExpiry.isPending}>Appliquer</Button>
              </span>
              <Button size="sm" variant="danger" onClick={() => void removeSelected([...selected])}>Supprimer</Button>
              <button type="button" className="ml-auto text-white/70 hover:text-white" onClick={() => setSelected(new Set())}>Tout désélectionner</button>
            </div>
          ) : null}

          <Card padded={false} className="overflow-x-auto">
            <table className="w-full min-w-[720px] text-sm">
              <thead>
                <tr className="border-b border-line text-left text-xs text-muted">
                  {admin ? (
                    <th className="w-10 px-4 py-2.5">
                      <input
                        type="checkbox"
                        className="h-4 w-4 accent-ink"
                        aria-label="Tout sélectionner"
                        checked={allVisibleSelected}
                        onChange={(event) => setSelected(event.target.checked ? new Set(visible.map((item) => item.filename)) : new Set())}
                      />
                    </th>
                  ) : null}
                  <th className="px-2 py-2.5 font-medium">Visuel</th>
                  <th className="w-44 px-2 py-2.5 font-medium">Échéance</th>
                  <th className="w-44 px-2 py-2.5 font-medium">Statut</th>
                  <th className="px-2 py-2.5 font-medium">Crédit</th>
                  <th className="w-24 px-4 py-2.5" />
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {visible.slice(0, shown).map((item) => (
                  <tr key={item.id} className={cx(selected.has(item.filename) && "bg-focus-soft")}>
                    {admin ? (
                      <td className="px-4 py-2">
                        <input
                          type="checkbox"
                          className="h-4 w-4 accent-ink"
                          aria-label={`Sélectionner ${item.filename}`}
                          checked={selected.has(item.filename)}
                          onChange={(event) => {
                            const next = new Set(selected);
                            if (event.target.checked) next.add(item.filename);
                            else next.delete(item.filename);
                            setSelected(next);
                          }}
                        />
                      </td>
                    ) : null}
                    <td className="px-2 py-2">
                      <div className="flex items-center gap-3">
                        <Thumb src={item.thumb_url} size={40} />
                        <div className="min-w-0">
                          <p className="max-w-[280px] truncate font-medium" title={item.filename}>{item.filename}</p>
                          <p className="text-xs text-muted">
                            {item.width && item.height ? `${item.width} × ${item.height}` : ""}
                            {!item.indexed ? <span className="ml-1 text-urgent">· indexation en attente</span> : null}
                          </p>
                        </div>
                      </div>
                    </td>
                    <td className="px-2 py-2">
                      {admin ? (
                        <Input
                          type="date"
                          aria-label={`Échéance de ${item.filename}`}
                          defaultValue={item.expiry_date}
                          key={`${item.id}-${item.expiry_date}`}
                          className="h-8"
                          onBlur={(event) => {
                            if (event.target.value !== item.expiry_date) saveExpiry(item, event.target.value);
                          }}
                          onKeyDown={(event) => {
                            if (event.key === "Enter") (event.target as HTMLInputElement).blur();
                          }}
                        />
                      ) : (
                        <span className="tabular">{formatDate(item.expiry_date)}</span>
                      )}
                    </td>
                    <td className="px-2 py-2"><StatusBadge status={item.status} label={item.status === "inconnue" ? undefined : daysText(item.days_left)} /></td>
                    <td className="max-w-[200px] truncate px-2 py-2 text-ink-soft" title={item.credit}>{item.credit || <span className="text-faint">—</span>}</td>
                    <td className="px-4 py-2 text-right">
                      <Button size="sm" variant="ghost" onClick={() => setEditing(item)}>{admin ? "Modifier" : "Voir"}</Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {visible.length > shown ? (
              <div className="border-t border-line p-3 text-center">
                <Button size="sm" onClick={() => setShown((value) => value + 100)}>Afficher plus · {shown}/{visible.length}</Button>
              </div>
            ) : null}
            {!visible.length ? <p className="px-4 py-6 text-center text-sm text-muted">Aucun visuel ne correspond à ces filtres.</p> : null}
          </Card>
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
        <a href={item.url} target="_blank" rel="noreferrer noopener" className="block overflow-hidden rounded-lg border border-line bg-canvas">
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
              className="w-full rounded-lg border border-line-strong bg-paper px-3 py-2 text-sm outline-none focus:border-focus focus:ring-2 focus:ring-focus-soft disabled:bg-canvas"
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
                  <td className="px-3 py-1.5 tabular">{row.expiry_date ? formatDate(row.expiry_date) : "—"}</td>
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
