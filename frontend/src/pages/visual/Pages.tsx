import { Select } from "../../components/Dropdown";
import { Button, Segmented, cx } from "../../components/ui";
import { hostOf, pathOf } from "../../lib/format";
import { PAGE_SIZE, type Counts, type Narrowing, type PageState, type Presence } from "../../lib/presence";

const STATE_TEXT: Record<PageState, { label: string; tone: string }> = {
  still: { label: "Encore en ligne", tone: "bg-expired-soft text-expired" },
  gone: { label: "Retirée", tone: "bg-ok-soft text-ok" },
  unknown: { label: "Pas relue", tone: "bg-sunk text-muted" },
};

const ALL = "";

/** How many pages are in each state for the version and rubric chosen (the whole visual when none is). */
function countsFor(data: Presence, narrowing: Narrowing): Counts {
  const version = data.versions.find((item) => item.site_id === narrowing.site);
  if (!version) return data.totals;
  return version.sections.find((item) => item.section === narrowing.section) ?? version;
}

/** Why a filter shows nothing: "still online" with nothing read is not the same as nothing online. */
function emptyText(counts: Counts, state: PageState | undefined): string {
  if (state === "still" && counts.unknown) return `Aucune page confirmée en ligne. ${counts.unknown.toLocaleString("fr-FR")} pas relues : une lecture complète les confirmera ou non.`;
  if (state === "gone" && counts.unknown) return `Aucune page confirmée retirée. ${counts.unknown.toLocaleString("fr-FR")} pas relues.`;
  return "Aucune page pour ce filtre.";
}

/** The pages, filtered by state and site version, `PAGE_SIZE` at a time. */
export function Pages(props: { data: Presence; narrowing: Narrowing; onNarrow: (narrowing: Narrowing) => void; onOffset: (offset: number) => void }) {
  const { data, narrowing } = props;
  const counts = countsFor(data, narrowing);
  const last = Math.floor((Math.max(data.total, 1) - 1) / PAGE_SIZE) * PAGE_SIZE;
  const states: { value: PageState | "all"; label: string }[] = [
    { value: "all", label: "Toutes" },
    { value: "still", label: `Encore en ligne · ${counts.still.toLocaleString("fr-FR")}` },
    { value: "gone", label: `Retirées · ${counts.gone.toLocaleString("fr-FR")}` },
    { value: "unknown", label: `Pas relues · ${counts.unknown.toLocaleString("fr-FR")}` },
  ];
  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <div className="max-w-full overflow-x-auto">
          <Segmented<PageState | "all"> label="État des pages" value={narrowing.state ?? "all"} options={states} onChange={(value) => props.onNarrow({ ...narrowing, state: value === "all" ? undefined : value })} />
        </div>
        {data.versions.length > 1 ? (
          <Select aria-label="Version du site" className="w-52" value={narrowing.site ?? ALL} onChange={(event) => props.onNarrow({ state: narrowing.state, site: event.target.value || undefined })}>
            <option value={ALL}>Toutes les versions</option>
            {data.versions.map((version) => (
              <option key={version.site_id} value={version.site_id}>{version.label || hostOf(version.site_url)}</option>
            ))}
          </Select>
        ) : null}
        <p className="ml-auto text-[13px] text-muted tabular">
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
        <p className="rounded-xl border border-dashed border-line-strong px-4 py-8 text-center text-sm text-muted">{emptyText(counts, narrowing.state)}</p>
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
