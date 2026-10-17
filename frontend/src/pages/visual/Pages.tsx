import { Button, Segmented, cx } from "../../components/ui";
import { hostOf, pathOf } from "../../lib/format";
import { PAGE_SIZE, type Narrowing, type PageState, type Presence } from "../../lib/presence";

const STATE_TEXT: Record<PageState, { label: string; tone: string }> = {
  still: { label: "Encore en ligne", tone: "bg-expired-soft text-expired" },
  gone: { label: "Retirée", tone: "bg-ok-soft text-ok" },
  unknown: { label: "Pas relue", tone: "bg-sunk text-muted" },
};

const FILTERS: { value: PageState | "all"; label: string }[] = [
  { value: "all", label: "Toutes" },
  { value: "still", label: "Encore en ligne" },
  { value: "gone", label: "Retirées" },
  { value: "unknown", label: "Pas relues" },
];

/** The pages, filtered by state, `PAGE_SIZE` at a time. */
export function Pages(props: { data: Presence; narrowing: Narrowing; onNarrow: (narrowing: Narrowing) => void; onOffset: (offset: number) => void }) {
  const { data, narrowing } = props;
  const last = Math.floor((Math.max(data.total, 1) - 1) / PAGE_SIZE) * PAGE_SIZE;
  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div className="max-w-full overflow-x-auto">
          <Segmented<PageState | "all"> label="État des pages" value={narrowing.state ?? "all"} options={FILTERS} onChange={(value) => props.onNarrow({ ...narrowing, state: value === "all" ? undefined : value })} />
        </div>
        <p className="text-[13px] text-muted tabular">
          {data.total.toLocaleString("fr-FR")} page{data.total > 1 ? "s" : ""}
          {narrowing.section ? ` dans ${narrowing.section}` : ""}
        </p>
      </div>
      {data.pages.length ? (
        <ul className="divide-y divide-line rounded-xl border border-ink bg-paper">
          {data.pages.map((page) => (
            <li key={page.url} className="flex items-center gap-3 px-4 py-2.5">
              <a href={page.url} target="_blank" rel="noreferrer noopener" title={page.url} className="min-w-0 flex-1 hover:underline">
                <span className="block truncate text-xs text-muted">{hostOf(page.url)}</span>
                <span className="block truncate font-mono text-[13px]">{pathOf(page.url)}</span>
              </a>
              <span className={cx("shrink-0 rounded-md px-2 py-px text-xs font-medium", STATE_TEXT[page.state].tone)}>{STATE_TEXT[page.state].label}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="rounded-xl border border-dashed border-line-strong px-4 py-8 text-center text-sm text-muted">Aucune page pour ce filtre.</p>
      )}
      {data.total > PAGE_SIZE ? (
        <div className="mt-3 flex items-center justify-between gap-2 text-[13px] text-muted">
          <Button size="sm" disabled={data.offset <= 0} onClick={() => props.onOffset(data.offset - PAGE_SIZE)}>Précédent</Button>
          <span className="tabular">{data.offset + 1}–{Math.min(data.offset + PAGE_SIZE, data.total)} sur {data.total.toLocaleString("fr-FR")}</span>
          <Button size="sm" disabled={data.offset >= last} onClick={() => props.onOffset(data.offset + PAGE_SIZE)}>Suivant</Button>
        </div>
      ) : null}
    </div>
  );
}
