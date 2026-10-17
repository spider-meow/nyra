import { Button, Card, ConfidenceBadge, DecisionBadge, EmptyState, StatusBadge, Thumb, cx } from "../../components/ui";
import { daysText, plural } from "../../lib/format";
import type { NotFoundItem } from "../../types";
import type { ReviewView, Row } from "./view";

/** The photos to confirm (first `shown`), one line each, with "Afficher plus" and the total. */
export function OccurrenceList(props: { view: ReviewView }) {
  const { rows, shown, selectedKey, select, showMore, listRef } = props.view;
  return (
    <div className="self-start rounded-xl border border-ink bg-paper">
      <div ref={listRef} role="listbox" aria-label="Photos trouvées en ligne" className="flex flex-col gap-1 p-2">
        {rows.slice(0, shown).map((row) => (
          <PhotoRow key={row.key} row={row} selected={row.key === selectedKey} onSelect={select} />
        ))}
      </div>
      {rows.length > shown ? (
        <div className="border-t border-line p-3 text-center">
          <Button size="sm" onClick={showMore}>Afficher plus · {shown}/{rows.length}</Button>
        </div>
      ) : null}
      <p className="border-t border-line px-4 py-2.5 text-xs text-muted">{plural(rows.length, "photo")}</p>
    </div>
  );
}

/** Where the photo was found and how many references it matches, with how sure the match is (and the decision, once there is one). */
function PhotoRow(props: { row: Row; selected: boolean; onSelect: (key: string) => void }) {
  const { row } = props;
  const { versions } = row.hit;
  const references = row.others.length + 1;
  return (
    <button
      data-key={row.key}
      type="button"
      role="option"
      aria-selected={props.selected}
      onClick={() => props.onSelect(row.key)}
      className={cx(
        "flex w-full items-center gap-3.5 rounded-[14px] border px-3 py-2.5 text-left transition-colors",
        props.selected ? "border-focus bg-paper shadow-[0_0_0_3px_var(--color-peach-soft)]" : "border-transparent hover:bg-paper",
        row.hit.decision === "ecarte" && "opacity-55",
      )}
    >
      <Thumb src={row.hit.site_thumb} size={46} />
      <span className="min-w-0 flex-1">
        <span className="block text-sm font-medium sm:truncate">Trouvée sur {versions.length > 4 ? plural(versions.length, "version") : versions.join(", ")}</span>
        <span className="mt-0.5 block text-xs text-muted sm:truncate">{plural(row.hit.file_count, "fichier")} · correspond à {plural(references, "référence")}</span>
      </span>
      <span className="flex shrink-0 flex-col items-end gap-1 sm:flex-row sm:items-center sm:gap-1.5">
        <ConfidenceBadge confidence={row.hit.confidence} />
        {row.hit.decision ? <DecisionBadge decision={row.hit.decision} /> : null}
      </span>
    </button>
  );
}

/** References no occurrence was found for. */
export function MissingList(props: { items: NotFoundItem[] }) {
  if (!props.items.length) return <EmptyState title="Aucun visuel manquant" body="Toutes les références de cette fenêtre ont au moins une occurrence en ligne." />;
  return (
    <Card padded={false} className="overflow-hidden">
      <p className="border-b border-line bg-sunk px-5 py-3.5 text-sm text-muted">
        Ces visuels n'ont été retrouvés nulle part. « Pas encore comparé » signifie qu'ils ont été ajoutés après la dernière comparaison : ce n'est pas une preuve d'absence.
      </p>
      <ul className="divide-y divide-line">
        {props.items.map((item) => (
          <li key={item.reference_id} className="flex items-center gap-3 px-5 py-3">
            <Thumb src={item.ref_thumb} size={40} />
            <span className="min-w-0 flex-1 truncate text-sm">{item.filename}</span>
            <StatusBadge status={item.status} label={daysText(item.days_left)} />
            <span className={cx("w-36 shrink-0 text-right text-xs", item.compared ? "text-muted" : "text-urgent")}>
              {item.compared ? "Comparé, rien trouvé" : "Pas encore comparé"}
            </span>
          </li>
        ))}
      </ul>
    </Card>
  );
}
