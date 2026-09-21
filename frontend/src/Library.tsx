import { useMemo, useState } from "react";
import { api } from "./api";
import { thumb } from "./api";
import type { LibraryItem } from "./types";
import { btn, btnDanger, btnGhost, card, field, label } from "./ui";

type Props = {
  items: LibraryItem[];
  indexed: number;
  running: boolean;
  ingestFast: boolean;
  onIngestFast: (value: boolean) => void;
  onContinue: () => void;
  onBanner: (message: string, note?: boolean) => void;
  onRefresh: () => Promise<unknown>;
};

export function Library(props: Props) {
  const [query, setQuery] = useState("");
  const [shown, setShown] = useState(60);
  const [over, setOver] = useState(false);
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return needle ? props.items.filter((item) => item.filename.toLowerCase().includes(needle)) : props.items;
  }, [props.items, query]);
  const visible = filtered.slice(0, shown);

  async function upload(files: FileList | File[]) {
    const body = new FormData();
    for (const file of files) body.append("files", file);
    props.onBanner("");
    try {
      await api("/api/library/upload", { method: "POST", body });
      await props.onRefresh();
    } catch (error) {
      props.onBanner(error instanceof Error ? error.message : "La requête a échoué.");
    }
  }

  async function importCsv(file: File) {
    const body = new FormData();
    body.append("file", file);
    try {
      const result = await api<{ applied: number }>("/api/library/import-csv", { method: "POST", body });
      await props.onRefresh();
      props.onBanner(`${result.applied} ligne(s) de métadonnées importées.`, true);
    } catch (error) {
      props.onBanner(error instanceof Error ? error.message : "La requête a échoué.");
    }
  }

  async function save(item: LibraryItem, patch: Partial<LibraryItem>) {
    try {
      await api(`/api/library/${encodeURIComponent(item.filename)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          expiry_date: patch.expiry_date ?? item.expiry_date,
          credit: patch.credit ?? item.credit,
          notes: patch.notes ?? item.notes,
        }),
      });
    } catch (error) {
      props.onBanner(error instanceof Error ? error.message : "La requête a échoué.");
    }
  }

  async function remove(filename: string) {
    try {
      await api(`/api/library/${encodeURIComponent(filename)}`, { method: "DELETE" });
      await props.onRefresh();
    } catch (error) {
      props.onBanner(error instanceof Error ? error.message : "La requête a échoué.");
    }
  }

  async function ingest() {
    props.onBanner("");
    try {
      await api("/api/jobs/ingest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ fast: props.ingestFast }),
      });
      await props.onRefresh();
    } catch (error) {
      props.onBanner(error instanceof Error ? error.message : "La requête a échoué.");
    }
  }

  return (
    <section>
      <h2 className="text-4xl font-semibold tracking-tight">Les images que tu défends</h2>
      <p className="mt-2 text-muted">Chaque date est une promesse. Quand elle passe, l'image ne devrait plus être là.</p>
      <div
        className={`${card} mt-6 ${over ? "border-ink" : ""}`}
        onDragOver={(event) => { event.preventDefault(); setOver(true); }}
        onDragLeave={() => setOver(false)}
        onDrop={(event) => {
          event.preventDefault();
          setOver(false);
          if (event.dataTransfer.files.length) void upload(event.dataTransfer.files);
        }}
      >
        <h3 className="text-lg font-semibold">Dépose les visuels</h3>
        <p className="mt-1 text-sm text-muted">JPG, PNG, WEBP. La date d'expiration se renseigne juste en dessous.</p>
        <label className={`${btn} mt-4 cursor-pointer`}>
          Choisir des fichiers
          <input className="hidden" type="file" accept="image/*" multiple onChange={(event) => {
            if (event.target.files?.length) void upload(event.target.files);
          }} />
        </label>
      </div>
      <div className="mt-4 flex flex-wrap items-end gap-3">
        <label className={`${btnGhost} cursor-pointer`}>
          Importer un CSV
          <input className="hidden" type="file" accept=".csv,text/csv" onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) void importCsv(file);
          }} />
        </label>
        <label className="min-w-40">
          <span className={label}>Filtrer</span>
          <input className={field} value={query} placeholder="Nom de fichier" onChange={(event) => { setQuery(event.target.value); setShown(60); }} />
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={props.ingestFast} onChange={(event) => props.onIngestFast(event.target.checked)} />
          Indexation rapide, sans le modèle visuel
        </label>
        <button type="button" className={btn} disabled={props.running || props.items.length === 0} onClick={() => void ingest()}>
          Enregistrer les dates
        </button>
      </div>
      {props.items.length === 0 ? (
        <div className={`${card} mt-4`}>
          <h3 className="text-lg font-semibold">Rien à protéger pour l'instant</h3>
          <p className="mt-1 text-sm text-muted">Dépose les visuels dont les droits ont une date. Sans eux, le site n'a rien à quoi se comparer.</p>
        </div>
      ) : (
        <div className="mt-4 grid gap-2">
          {visible.map((item) => (
            <article key={item.filename} className={card}>
              <div className="grid grid-cols-[40px_minmax(0,1fr)_auto] items-center gap-3">
                <img className="h-10 w-10 rounded-lg object-cover" alt="" loading="lazy" src={thumb(item.url, 80)} />
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium" title={item.filename}>{item.filename}</p>
                  <p className="text-xs text-muted">{item.indexed ? "Indexée" : "Date pas encore enregistrée"}</p>
                </div>
                <button type="button" className={btnDanger} onClick={() => void remove(item.filename)}>Retirer</button>
              </div>
              <div className="mt-3 grid gap-2 md:grid-cols-3">
                <label>
                  <span className={label}>Expiration</span>
                  <input className={field} type="date" defaultValue={item.expiry_date} onChange={(event) => void save(item, { expiry_date: event.target.value })} />
                </label>
                <label>
                  <span className={label}>Crédit</span>
                  <input className={field} defaultValue={item.credit} placeholder="Qui a fait cette image" onBlur={(event) => void save(item, { credit: event.target.value })} />
                </label>
                <label>
                  <span className={label}>Notes</span>
                  <input className={field} defaultValue={item.notes} placeholder="Usage, territoire" onBlur={(event) => void save(item, { notes: event.target.value })} />
                </label>
              </div>
            </article>
          ))}
          {filtered.length > visible.length ? (
            <button type="button" className={btnGhost} onClick={() => setShown((value) => value + 60)}>
              Afficher la suite · {visible.length}/{filtered.length}
            </button>
          ) : null}
        </div>
      )}
      {props.indexed ? (
        <div className={`${card} mt-4`}>
          <h3 className="text-lg font-semibold">La liste est prête</h3>
          <p className="mt-1 text-sm text-muted">Ensuite, indique le site où ces images n'ont peut-être plus le droit d'être.</p>
          <button type="button" className={`${btn} mt-3`} onClick={props.onContinue}>Continuer vers le site</button>
        </div>
      ) : null}
    </section>
  );
}
