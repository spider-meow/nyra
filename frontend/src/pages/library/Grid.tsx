import { Icon } from "../../components/icons";
import { Button, StatusBadge, cx } from "../../components/ui";
import { daysText, formatDate } from "../../lib/format";
import { useOrg } from "../../lib/org";
import type { LibraryItem } from "../../types";
import type { LibraryView } from "./view";

type Props = {
  view: LibraryView;
  selected: Set<string>;
  setSelected: (selected: Set<string>) => void;
  onEdit: (item: LibraryItem) => void;
  onPick: () => void;
};

/** "Select all" box, the visuels (first page of them), "show more" and the empty-result message. */
export function LibraryGrid({ view, selected, setSelected, onEdit, onPick }: Props) {
  const { admin } = useOrg();
  const { visible, shown, tab, filtering } = view;
  const allVisibleSelected = visible.length > 0 && visible.every((item) => selected.has(item.filename));

  function toggle(filename: string, on: boolean) {
    const next = new Set(selected);
    if (on) next.add(filename);
    else next.delete(filename);
    setSelected(next);
  }

  return (
    <>
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
        {admin && tab === "active" && !filtering ? <DropTile onPick={onPick} /> : null}
        {visible.slice(0, shown).map((item) => (
          <ReferenceCard key={item.id} item={item} checked={selected.has(item.filename)} anySelected={selected.size > 0} onEdit={onEdit} onToggle={toggle} />
        ))}
      </ul>
      {visible.length > shown ? (
        <div className="mt-6 text-center">
          <Button onClick={view.showMore}>Afficher plus · {shown}/{visible.length}</Button>
        </div>
      ) : null}
      {!visible.length ? (
        <p className="py-10 text-center text-sm text-muted">
          {tab === "expired" && !filtering ? "Aucun visuel expiré : tout ce que vous surveillez est encore sous droits." : "Aucun visuel ne correspond à ces filtres."}
        </p>
      ) : null}
    </>
  );
}

function DropTile({ onPick }: { onPick: () => void }) {
  return (
    <li>
      <button
        type="button"
        onClick={onPick}
        className="flex h-full min-h-56 w-full flex-col items-center justify-center gap-2.5 rounded-2xl border-[1.5px] border-dashed border-line-strong bg-sunk p-4 text-center transition-colors hover:border-bark hover:bg-peach-soft/40"
      >
        <span className="grid h-11 w-11 place-items-center rounded-xl bg-peach-soft text-bark-700"><Icon name="upload" size={20} /></span>
        <span className="text-sm font-medium">Déposez des images</span>
        <span className="text-[12.5px] leading-snug text-muted">ou cliquez pour choisir. JPG, PNG, WebP…</span>
      </button>
    </li>
  );
}

type CardProps = {
  item: LibraryItem;
  checked: boolean;
  anySelected: boolean;
  onEdit: (item: LibraryItem) => void;
  onToggle: (filename: string, checked: boolean) => void;
};

function ReferenceCard({ item, checked, anySelected, onEdit, onToggle }: CardProps) {
  const { admin } = useOrg();
  return (
    <li
      className={cx(
        "group relative flex flex-col overflow-hidden rounded-2xl border bg-paper transition-shadow hover:shadow-float",
        checked ? "border-[#e6d3c2] shadow-[0_0_0_3px_var(--color-peach-soft)]" : "border-line",
      )}
    >
      <button type="button" onClick={() => onEdit(item)} className="block text-left" aria-label={`${admin ? "Modifier" : "Voir"} ${item.filename}`}>
        <span className="block aspect-[4/3] overflow-hidden bg-side">
          {item.thumb_url ? <img src={item.thumb_url} alt="" loading="lazy" className="h-full w-full object-cover" /> : null}
        </span>
        <span className="flex flex-col gap-2 px-3.5 pt-3 pb-3.5">
          <span className="truncate text-[13.5px] font-medium" title={item.filename}>{item.filename}</span>
          <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <StatusBadge status={item.status} label={item.status === "inconnue" ? undefined : daysText(item.days_left)} />
            {item.expiry_date ? <span className="text-[11.5px] text-muted tabular">{formatDate(item.expiry_date)}</span> : null}
          </span>
          {item.tags.length ? (
            <span className="flex flex-wrap gap-1">
              {item.tags.slice(0, 3).map((tag) => (
                <span key={tag} className="rounded-full bg-side px-2 py-0.5 text-[11px] text-ink-soft">{tag}</span>
              ))}
              {item.tags.length > 3 ? <span className="px-1 py-0.5 text-[11px] text-muted">+{item.tags.length - 3}</span> : null}
            </span>
          ) : null}
          {!item.indexed ? <span className="text-[11.5px] text-urgent">Indexation en attente</span> : null}
        </span>
      </button>
      {admin ? (
        <input
          type="checkbox"
          className={cx("absolute top-2.5 left-2.5 h-5 w-5 accent-ink transition-opacity", checked || anySelected ? "opacity-100" : "opacity-0 group-hover:opacity-100 focus-visible:opacity-100")}
          aria-label={`Sélectionner ${item.filename}`}
          checked={checked}
          onChange={(event) => onToggle(item.filename, event.target.checked)}
        />
      ) : null}
    </li>
  );
}
