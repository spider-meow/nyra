import { Link } from "react-router";
import { Icon } from "../../components/icons";
import { Button, cx, statusDot } from "../../components/ui";
import { statusLabel } from "../../lib/format";
import { useOrg } from "../../lib/org";
import type { Collection, Status } from "../../types";
import { MAX_DEPTH } from "./collections";
import { NameField, useFolderActions } from "./folderEdit";
import type { LibraryView } from "./view";

/** The library's side menu, brandcenter style: "Tous", "Non classés", then the three levels as an accordion. Only for browsing; collections are managed in the settings. */
export function CollectionTree({ view }: { view: LibraryView }) {
  const { admin, link } = useOrg();
  const { folders, folderCounts, scope } = view;
  const top = folders.children.get(null) ?? [];

  // A function, not a component: it calls itself for the levels below.
  // Accordion: a folder is open while the folder being looked at is itself or below it, so only one family is open.
  function node(collection: Collection, depth: number) {
    const kids = folders.children.get(collection.id) ?? [];
    const expanded = folders.subtree(collection.id).includes(scope);
    return (
      <li key={collection.id} className={depth === 1 ? "rounded-xl border border-line bg-paper p-1" : undefined}>
        <Row depth={depth} active={scope === collection.id} label={collection.name} count={folderCounts[collection.id] ?? 0} onSelect={() => view.setScope(collection.id)} chevron={kids.length ? { expanded } : undefined} />
        {expanded && kids.length ? <ul>{kids.map((child) => node(child, depth + 1))}</ul> : null}
      </li>
    );
  }

  return (
    <nav aria-label="Collections" className="mb-5 lg:mb-0">
      <ul className="flex flex-col gap-0.5">
        <li><Row depth={1} active={scope === "all"} label="Tous les visuels" count={folderCounts.all} onSelect={() => view.setScope("all")} /></li>
        {folderCounts.none || scope === "none" ? <li><Row depth={1} active={scope === "none"} label="Non classés" count={folderCounts.none} onSelect={() => view.setScope("none")} /></li> : null}
        {top.map((collection) => node(collection, 1))}
      </ul>
      {!top.length ? (
        <p className="mt-2 px-2.5 text-[12.5px] text-muted">
          {admin ? <>Créez vos collections dans les <Link to={`${link("settings")}?section=collections`} className="underline underline-offset-2">réglages</Link>.</> : "Pas encore de collection."}
        </p>
      ) : null}
    </nav>
  );
}

/** Above the grid: where we are ("Bibliothèque › A › B", each step clickable), the folders directly below as tiles, and for an admin inside a collection of the second level the button that adds a specific collection. Hidden while searching, which covers the whole library. */
export function FolderBar({ view }: { view: LibraryView }) {
  const { admin } = useOrg();
  const { folders, folderCounts, scope } = view;
  const { editing, setEditing, submit } = useFolderActions(folders);
  if (view.query || scope === "none") return null;
  const trail: Collection[] = [];
  for (let id = scope === "all" ? undefined : scope; id && trail.length < MAX_DEPTH; id = folders.byId.get(id)?.parent_id ?? undefined) {
    const collection = folders.byId.get(id);
    if (collection) trail.unshift(collection);
  }
  const kids = scope === "all" ? [] : folders.children.get(scope) ?? []; // at the top the side menu already lists them
  const canAdd = admin && scope !== "all" && folders.depth(scope) === MAX_DEPTH - 1;
  const adding = editing !== null && "add" in editing && editing.add === scope;
  return (
    <div className="mb-5">
      {trail.length ? (
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <Breadcrumb trail={trail} onPick={view.setScope} />
          {canAdd && !adding ? <Button size="sm" onClick={() => setEditing({ add: scope })}>+ Sous-collection</Button> : null}
        </div>
      ) : null}
      {canAdd && adding ? <div className="mb-3"><NameField initial="" label="Nom de la nouvelle sous-collection" onDone={submit} /></div> : null}
      {kids.length ? (
        <ul className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
          {kids.map((child) => (
            <li key={child.id}>
              <FolderTile folder={child} count={folderCounts[child.id] ?? 0} subNames={(folders.children.get(child.id) ?? []).map((sub) => sub.name).join(" · ")} statuses={view.folderStatuses[child.id] ?? {}} onOpen={() => view.setScope(child.id)} />
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

// Worst first, so the bar reads left to right from what needs attention to what does not.
const SEGMENTS: Status[] = ["expire", "<30j", "<90j", "inconnue", "ok", "illimite"];

/** A folder as a tile: its name, how many visuals, its sub-folders, and a bar of how healthy the rights of what it holds are. */
function FolderTile(props: { folder: Collection; count: number; subNames: string; statuses: Partial<Record<Status, number>>; onOpen: () => void }) {
  const { statuses } = props;
  const present = SEGMENTS.filter((status) => statuses[status]);
  const summary = present.map((status) => `${statuses[status]} · ${statusLabel[status].toLowerCase()}`).join(", ");
  return (
    <button type="button" onClick={props.onOpen} className="flex h-full w-full flex-col gap-2 rounded-xl border border-line bg-paper p-4 text-left transition-shadow hover:shadow-float">
      <span className="flex w-full items-start justify-between gap-3">
        <span className="font-display min-w-0 text-lg leading-tight [overflow-wrap:anywhere]">{props.folder.name}</span>
        <span className="text-sm text-ink-soft tabular">{props.count.toLocaleString("fr-FR")}</span>
      </span>
      {props.subNames ? <span className="line-clamp-2 text-[12px] text-ink-soft [overflow-wrap:anywhere]">{props.subNames}</span> : null}
      {present.length ? (
        <span role="img" aria-label={summary} title={summary} className="mt-auto flex h-1.5 w-full gap-px overflow-hidden rounded-full bg-sunk">
          {present.map((status) => <span key={status} className={cx("h-full", statusDot[status], status === "illimite" && "opacity-45")} style={{ flexGrow: statuses[status] }} />)}
        </span>
      ) : null}
    </button>
  );
}

function Breadcrumb({ trail, onPick }: { trail: Collection[]; onPick: (id: string) => void }) {
  return (
    <nav aria-label="Fil d'Ariane" className="flex flex-wrap items-center gap-1.5 text-[13px] text-muted">
      <button type="button" onClick={() => onPick("all")} className="hover:text-ink">Bibliothèque</button>
      {trail.map((collection, index) => (
        <span key={collection.id} className="flex items-center gap-1.5">
          <span aria-hidden>›</span>
          {index === trail.length - 1
            ? <span aria-current="page" className="font-medium text-ink">{collection.name}</span>
            : <button type="button" onClick={() => onPick(collection.id)} className="hover:text-ink">{collection.name}</button>}
        </span>
      ))}
    </nav>
  );
}

function Row(props: { depth: number; active: boolean; label: string; count: number; onSelect: () => void; chevron?: { expanded: boolean } }) {
  return (
    <div className={cx("flex items-center rounded-lg text-[13.5px]", props.active ? "bg-side font-medium text-ink" : "text-ink-soft hover:bg-side/60")} style={{ paddingLeft: (props.depth - 1) * 14 }}>
      {props.chevron ? (
        <span aria-hidden className="grid h-7 w-6 shrink-0 place-items-center text-muted">
          <Icon name="arrow" size={12} className={props.chevron.expanded ? "rotate-90" : ""} />
        </span>
      ) : <span className="w-6 shrink-0" />}
      <button type="button" onClick={props.onSelect} aria-current={props.active ? "true" : undefined} aria-expanded={props.chevron?.expanded} className="flex min-w-0 flex-1 items-center gap-2 py-1.5 pr-2 text-left">
        <span className="min-w-0 [overflow-wrap:anywhere]">{props.label}</span>
        <span className="ml-auto text-[11.5px] text-muted tabular">{props.count.toLocaleString("fr-FR")}</span>
      </button>
    </div>
  );
}
