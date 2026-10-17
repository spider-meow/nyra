import { Fragment } from "react";
import { Link } from "react-router";
import { Button, Card, ConfidenceBadge, DecisionBadge, EmptyState, StatusBadge, Thumb, cx } from "../../components/ui";
import { daysText, hostOf, pathOf, plural } from "../../lib/format";
import type { NotFoundItem } from "../../types";
import { RemovalBadge } from "./Removal";
import type { Progress, ReviewView, Row } from "./view";

/** The photos found online (first `shown`), under the visual they match, with "Afficher plus" and the total. */
export function OccurrenceList(props: { view: ReviewView }) {
  const { rows, shown, selectedKey, select, showMore, listRef, progress } = props.view;
  return (
    <div className="self-start rounded-xl border border-ink bg-paper">
      <div ref={listRef} role="listbox" aria-label="Photos en ligne" className="flex flex-col gap-1 p-2">
        {rows.slice(0, shown).map((row, index) => (
          <Fragment key={row.key}>
            {index === 0 || rows[index - 1].group.reference_id !== row.group.reference_id ? <VisualHeader row={row} progress={progress.get(row.group.reference_id)} /> : null}
            <PhotoRow row={row} selected={row.key === selectedKey} onSelect={select} />
          </Fragment>
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

/** A visual of the library, its expiry and how many of its photos found online are removed. */
function VisualHeader(props: { row: Row; progress?: Progress }) {
  const { group } = props.row;
  const { done = 0, total = 0, unchecked = 0 } = props.progress ?? {};
  return (
    <div role="presentation" className="mt-2 flex items-center gap-3 px-3 pt-2 pb-1 first:mt-0">
      <Thumb src={group.ref_thumb} size={40} />
      <span className="min-w-0 flex-1">
        <Link to={`?visual=${group.reference_id}`} title="Voir où il est en ligne" className="block truncate text-sm font-semibold hover:underline">{group.filename}</Link>
        <span className="flex flex-wrap items-center gap-2">
          <StatusBadge status={group.status} label={daysText(group.days_left)} />
          <Link to={`?visual=${group.reference_id}`} className="text-xs font-medium underline underline-offset-2">Voir où il est en ligne</Link>
        </span>
      </span>
      <span className="flex shrink-0 flex-col items-end gap-1 text-xs text-muted">
        <span>{total ? `${done} retirée${done > 1 ? "s" : ""} sur ${total}${unchecked ? ` · ${unchecked} à vérifier` : ""}` : "Rien à retirer"}</span>
        <span aria-hidden className="h-1.5 w-24 overflow-hidden rounded-full bg-sunk">
          <span className="block h-full bg-ink" style={{ width: `${total ? (done / total) * 100 : 0}%` }} />
        </span>
      </span>
    </div>
  );
}

function PhotoRow(props: { row: Row; selected: boolean; onSelect: (key: string) => void }) {
  const { row } = props;
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
        <span className="block truncate font-mono text-[12px]">
          {hostOf(row.hit.site_url)} · {row.hit.pages[0] ? pathOf(row.hit.pages[0]) : pathOf(row.hit.site_url)}
          {row.hit.page_count > 1 ? ` et ${row.hit.page_count - 1} autre(s) page(s)` : ""}
        </span>
        <span className="mt-0.5 block truncate text-xs text-muted">
          {plural(row.hit.file_count, "fichier")}
          {row.others.length ? ` · + ${plural(row.others.length, "autre référence", "autres références")}` : ""}
        </span>
      </span>
      <span className="hidden shrink-0 items-center gap-1.5 sm:flex">
        <ConfidenceBadge confidence={row.hit.confidence} />
        {row.hit.removal ? <RemovalBadge removal={row.hit.removal} /> : <DecisionBadge decision={row.hit.decision} />}
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
