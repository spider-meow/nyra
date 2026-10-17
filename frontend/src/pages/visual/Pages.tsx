import { Select } from "../../components/Dropdown";
import { useToast } from "../../components/feedback";
import { Button, Segmented, cx } from "../../components/ui";
import { errorMessage } from "../../lib/api";
import { hostOf, pathOf } from "../../lib/format";
import { useOrg } from "../../lib/org";
import { MAX_CHECKED_PAGES, PAGE_SIZE, useCheckPages, useMarkReplaced, type Counts, type Narrowing, type PageState, type Presence } from "../../lib/presence";

const STATE_TEXT: Record<PageState, { label: string; tone: string }> = {
  still: { label: "Encore en ligne", tone: "bg-expired-soft text-expired" },
  gone: { label: "Retirée", tone: "bg-ok-soft text-ok" },
  unknown: { label: "Pas relue", tone: "bg-sunk text-muted" },
};

const ALL = "";

type Page = Presence["pages"][number];

/** What a page says once the person's mark and the last read are put together. */
function statusOf(page: Page): { label: string; tone: string } {
  if (!page.marked) return STATE_TEXT[page.state];
  if (page.state === "gone") return { label: "Remplacée · confirmée", tone: "bg-ok-soft text-ok" };
  if (page.state === "still") return { label: "Marquée remplacée · encore en ligne", tone: "bg-expired-soft text-expired" };
  return { label: "Remplacée · à vérifier", tone: "bg-soon-soft text-soon" };
}

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
export function Pages(props: { referenceId: string; data: Presence; narrowing: Narrowing; onNarrow: (narrowing: Narrowing) => void; onOffset: (offset: number) => void }) {
  const { data, narrowing } = props;
  const counts = countsFor(data, narrowing);
  const shown = data.pages.slice(0, MAX_CHECKED_PAGES);
  const { admin } = useOrg();
  const toast = useToast();
  const { check, busy } = useCheckPages();
  const mark = useMarkReplaced(props.referenceId);
  // The person's own count: it says where they are, whatever the last read saw; a read or a check then confirms it.
  const setMarked = (urls: string[], replaced: boolean) => mark.mutate({ urls, replaced }, {
    onError: (error) => toast.show({ tone: "error", message: "La marque n'a pas été enregistrée", description: errorMessage(error) }),
  });
  const allMarked = shown.length > 0 && shown.every((page) => page.marked);
  // Reads these pages again, and only these: after changing a visual on a page, this says whether it is gone.
  const verify = (urls: string[]) => check.mutate(urls, {
    onSuccess: () => toast.show({ tone: "success", message: urls.length > 1 ? `Vérification de ${urls.length} pages lancée` : "Vérification de la page lancée", description: "La progression s'affiche en bas de l'écran.", duration: 4000 }),
    onError: (error) => toast.show({ tone: "error", message: "La vérification n'a pas démarré", description: errorMessage(error) }),
  });
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
        <Select aria-label="Pages marquées remplacées" className="w-52" value={narrowing.marked === undefined ? ALL : narrowing.marked ? "yes" : "no"}
          onChange={(event) => props.onNarrow({ ...narrowing, marked: event.target.value === ALL ? undefined : event.target.value === "yes" })}>
          <option value={ALL}>Marquées ou non</option>
          <option value="yes">Marquées remplacées · {counts.marked.toLocaleString("fr-FR")}</option>
          <option value="no">Pas encore marquées</option>
        </Select>
        <p className="ml-auto text-[13px] text-muted tabular">
          {data.total.toLocaleString("fr-FR")} page{data.total > 1 ? "s" : ""}
          {narrowing.section ? ` dans ${narrowing.section}` : ""}
        </p>
        {data.pages.length ? <Button size="sm" disabled={mark.isPending} onClick={() => setMarked(shown.map((page) => page.url), !allMarked)}>{allMarked ? "Tout démarquer" : "Tout marquer remplacé"}</Button> : null}
        {admin && data.pages.length ? <Button size="sm" loading={busy} onClick={() => verify(shown.map((page) => page.url))}>Vérifier ces {shown.length} pages</Button> : null}
      </div>
      {data.pages.length ? (
        <ul className="divide-y divide-line rounded-xl border border-ink bg-paper">
          {data.pages.map((page) => (
            <li key={page.url} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2.5">
              <label className="flex shrink-0 items-center gap-2 text-xs text-muted">
                <input type="checkbox" className="h-[18px] w-[18px] accent-ink" checked={page.marked} disabled={mark.isPending} aria-label={`Page remplacée : ${pathOf(page.url)}`} onChange={(event) => setMarked([page.url], event.target.checked)} />
                Remplacée
              </label>
              <a href={page.url} target="_blank" rel="noreferrer noopener" title={page.url} className="min-w-0 basis-full order-first hover:underline sm:order-none sm:flex-1 sm:basis-0">
                <span className="block truncate text-xs text-muted">{hostOf(page.url)}</span>
                <span className="block truncate font-mono text-[13px]">{pathOf(page.url)}</span>
              </a>
              <span className={cx("shrink-0 rounded-md px-2 py-px text-xs font-medium", statusOf(page).tone)}>{statusOf(page).label}</span>
              {admin ? <Button size="sm" variant="ghost" disabled={busy} aria-label={`Vérifier la page ${pathOf(page.url)}`} onClick={() => verify([page.url])}>Vérifier</Button> : null}
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
