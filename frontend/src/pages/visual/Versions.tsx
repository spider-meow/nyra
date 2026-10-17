import { cx } from "../../components/ui";
import { hostOf } from "../../lib/format";
import type { Narrowing, Version } from "../../lib/presence";

const total = (counts: { still: number; gone: number; unknown: number }) => (counts.still + counts.gone + counts.unknown).toLocaleString("fr-FR");

/** The site versions the visual is on, each with its sections and how many pages still show it. Clicking narrows the pages. */
export function Versions(props: { versions: Version[]; narrowing: Narrowing; onNarrow: (narrowing: Narrowing) => void }) {
  const { narrowing } = props;
  return (
    <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
      {props.versions.map((version) => {
        const chosen = narrowing.site === version.site_id;
        return (
          <section key={version.site_id} className={cx("rounded-xl border bg-paper p-4", chosen ? "border-ink" : "border-line")}>
            <button type="button" aria-pressed={chosen} onClick={() => props.onNarrow(chosen && !narrowing.section ? {} : { site: version.site_id })} className="flex w-full items-baseline justify-between gap-3 text-left">
              <span className="min-w-0">
                <span className="block truncate text-sm font-semibold">{hostOf(version.site_url)}</span>
                {version.label ? <span className="text-xs text-muted">{version.label}</span> : null}
              </span>
              <span className="shrink-0 text-sm tabular"><b>{version.still.toLocaleString("fr-FR")}</b> <span className="text-muted">sur {total(version)}</span></span>
            </button>
            <ul className="mt-2 flex flex-wrap gap-1.5">
              {version.sections.slice(0, 8).map((section) => {
                const on = chosen && narrowing.section === section.section;
                return (
                  <li key={section.section}>
                    <button
                      type="button"
                      aria-pressed={on}
                      onClick={() => props.onNarrow(on ? { site: version.site_id } : { site: version.site_id, section: section.section })}
                      className={cx("rounded-md border px-2 py-1 font-mono text-xs", on ? "border-ink bg-yellow" : "border-line hover:border-line-strong")}
                    >
                      {section.section} <span className="font-sans text-muted">{section.still.toLocaleString("fr-FR")}/{total(section)}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </section>
        );
      })}
    </div>
  );
}
