import { useState } from "react";
import { useToast } from "../../components/feedback";
import { Icon } from "../../components/icons";
import { Button, Segmented, Spinner, Thumb, cx } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import { formatDateTime, hostOf, pathOf, plural } from "../../lib/format";
import { useOrg } from "../../lib/org";
import { useLocate, useOccurrences } from "../../lib/queries";
import type { LibraryItem, Occurrences as OccurrencesData } from "../../types";
import { PAGE_SIZE, useLocateJob } from "./Occurrences";

type Filter = "all" | "review";
type PageRow = OccurrencesData["pages"][number];

/** What unfolds under "Où est-elle utilisée ?": the pages, the filter, the search. */
export default function OccurrencesPanel({ item }: { item: LibraryItem }) {
  const [offset, setOffset] = useState(0);
  const [filter, setFilter] = useState<Filter>("all");
  const found = useOccurrences(item.filename, offset, PAGE_SIZE, filter === "review" ? "review" : undefined);
  const data = found.data;

  return (
    <>
      <Summary data={data} filter={filter} onFilter={(next) => { setFilter(next); setOffset(0); }} />
      <div className="max-h-72 overflow-x-hidden overflow-y-auto px-4 pb-3">
        {found.isLoading ? <Spinner /> : null}
        {found.isError ? <p className="text-sm text-muted">{errorMessage(found.error)}</p> : null}
        {data ? <PageRows data={data} filter={filter} /> : null}
      </div>
      <div className="border-t border-line bg-sunk">
        {data && data.total_pages > PAGE_SIZE ? <Pager total={data.total_pages} offset={data.offset} onOffset={setOffset} /> : null}
        <SearchBar item={item} data={data} onStarted={() => setOffset(0)} />
      </div>
    </>
  );
}

/** The figures in one line, when the search last ran, and the filter once there is something to check. */
function Summary({ data, filter, onFilter }: { data: OccurrencesData | undefined; filter: Filter; onFilter: (filter: Filter) => void }) {
  const stale = data?.located_at && data.last_crawled_at && data.last_crawled_at > data.located_at;
  const showFilter = data && (data.review_images > 0 || filter === "review");
  return (
    <div className="px-4 pt-3 pb-3">
      <p className="text-sm font-medium tabular">
        {data ? `${plural(data.total_pages, "page")} · ${plural(data.images_found, "image")}${filter === "review" ? " à vérifier" : ""}` : "…"}
      </p>
      <p className="mt-0.5 text-[13px] text-muted">
        {data?.located_at ? `Recherchée le ${formatDateTime(data.located_at)} · ${data.pages_crawled} pages lues` : "Pas encore recherchée"}
      </p>
      {stale ? <p className="mt-1 text-[13px] text-urgent">Des pages ont été lues depuis : relancez la recherche.</p> : null}
      {showFilter ? (
        <div className="mt-3">
          <Segmented<Filter>
            label="Filtrer"
            value={filter}
            onChange={onFilter}
            options={[{ value: "all", label: "Toutes" }, { value: "review", label: `À vérifier · ${data.review_images}` }]}
          />
        </div>
      ) : null}
    </div>
  );
}

/** The search: its button (admins), or its progress while it runs. */
function SearchBar({ item, data, onStarted }: { item: LibraryItem; data: OccurrencesData | undefined; onStarted: () => void }) {
  const { admin } = useOrg();
  const toast = useToast();
  const locate = useLocate();
  const { running, mine } = useLocateJob(item);
  const done = running?.progress.done ?? 0;
  const total = running?.progress.total ?? 0;

  function start() {
    locate.mutate(item.filename, {
      onSuccess: onStarted,
      onError: (error) => toast.show({ tone: "error", message: "La recherche n'a pas démarré", description: errorMessage(error) }),
    });
  }

  if (mine) {
    return (
      <div className="px-4 py-3" role="status">
        <div className="h-1.5 overflow-hidden rounded-full bg-line">
          <div className={cx("h-full rounded-full bg-bark transition-[width] duration-500", !total && "progress-indeterminate w-1/4")} style={total ? { width: `${Math.min(100, Math.round((done / total) * 100))}%` } : undefined} />
        </div>
        <p className="mt-1.5 text-[13px] text-muted">{running?.message}</p>
      </div>
    );
  }
  if (!admin) return null;
  return (
    <div className="px-4 py-3">
      {!data?.located_at ? <p className="mb-2 text-xs text-muted">Vérifie chaque image déjà lue, recadrages compris. Peut prendre plusieurs minutes.</p> : null}
      <Button className="w-full" onClick={start} loading={locate.isPending} disabled={Boolean(running)} title={running ? "Une recherche est déjà en cours pour un autre visuel." : undefined}>
        {data?.located_at ? "Relancer la recherche" : "Lancer la recherche"}
      </Button>
    </div>
  );
}

function PageRows({ data, filter }: { data: OccurrencesData; filter: Filter }) {
  if (!data.pages.length) {
    return (
      <p className="rounded-xl border border-dashed border-line-strong px-4 py-8 text-center text-sm text-muted">
        {filter === "review" ? "Aucune image à vérifier." : data.located_at ? `Aucune page ne montre ce visuel parmi les ${data.pages_crawled} pages lues.` : "Rien à afficher pour l'instant."}
      </p>
    );
  }
  return (
    <ul className="grid grid-cols-1 gap-2">
      {data.pages.map((page) => (
        <li key={page.url} className="min-w-0">
          <a href={page.url} target="_blank" rel="noreferrer noopener" title={page.url} className="group flex items-center gap-3 rounded-xl border border-ink bg-paper p-2.5 transition-colors hover:border-line-strong hover:bg-sunk">
            <Thumbs page={page} />
            <span className="min-w-0 flex-1">
              <span className="block truncate text-xs text-muted">{hostOf(page.url)}</span>
              <span className="block truncate text-sm font-medium">{pathOf(page.url)}</span>
              {page.images.some((image) => image.tier === "review") ? (
                <span title="Recadrage ou ressemblance partielle : à confirmer" className="mt-1 inline-block rounded-full bg-soon-soft px-2 py-0.5 text-[11.5px] font-medium text-soon">À vérifier</span>
              ) : null}
            </span>
            <Icon name="arrow" size={14} className="shrink-0 -rotate-45 text-faint group-hover:text-ink" />
          </a>
        </li>
      ))}
    </ul>
  );
}

function Thumbs({ page }: { page: PageRow }) {
  return (
    <span className="flex shrink-0 -space-x-3">
      {page.images.slice(0, 3).map((image) => (
        <span key={image.site_image_id} className="rounded-md ring-2 ring-paper group-hover:ring-sunk"><Thumb src={image.thumb} size={48} /></span>
      ))}
      {page.image_count > 3 ? <span className="grid h-12 w-12 place-items-center rounded-md bg-side text-xs font-medium text-muted ring-2 ring-paper">+{page.image_count - 3}</span> : null}
    </span>
  );
}

function Pager(props: { total: number; offset: number; onOffset: (offset: number) => void }) {
  const last = Math.floor((props.total - 1) / PAGE_SIZE) * PAGE_SIZE;
  return (
    <div className="flex items-center justify-between gap-2 border-b border-line px-4 py-2.5 text-[13px] text-muted">
      <Button size="sm" disabled={props.offset <= 0} onClick={() => props.onOffset(props.offset - PAGE_SIZE)}>Précédent</Button>
      <span className="tabular">{props.offset + 1}–{Math.min(props.offset + PAGE_SIZE, props.total)} sur {props.total}</span>
      <Button size="sm" disabled={props.offset >= last} onClick={() => props.onOffset(props.offset + PAGE_SIZE)}>Suivant</Button>
    </div>
  );
}
