import { Link } from "react-router";
import { Chip, SearchField, Segmented } from "../../components/ui";
import { useOrg } from "../../lib/org";
import type { LibraryItem, Status } from "../../types";
import { TAGS_SHOWN, type Filter, type LibraryView, type Tab } from "./view";

function countStatus(items: LibraryItem[], status: Status): number {
  return items.filter((item) => item.status === status).length;
}

function statusChips(items: LibraryItem[]): { value: Filter; label: string; dot?: Status; count?: number }[] {
  return [
    { value: "all", label: "Tous", count: items.length },
    { value: "<30j", label: "Sous 30 j", dot: "<30j", count: countStatus(items, "<30j") },
    { value: "<90j", label: "Sous 90 j", dot: "<90j", count: countStatus(items, "<90j") },
    { value: "ok", label: "Dans les délais", dot: "ok" },
    { value: "inconnue", label: "Sans échéance", dot: "inconnue", count: countStatus(items, "inconnue") },
    { value: "unindexed", label: "Pas encore indexés", count: items.filter((item) => !item.indexed).length },
  ];
}

/** Tabs, search, status chips, sort and tag filters, plus the `#library-tags` suggestions the tag fields use. */
export function LibraryToolbar(props: { view: LibraryView; onTab: (tab: Tab) => void }) {
  const { admin, link } = useOrg();
  const view = props.view;
  return (
    <>
      <div className="mb-4">
        <Segmented
          label="Bibliothèque"
          value={view.tab}
          onChange={props.onTab}
          options={[
            { value: "active", label: `Actifs · ${view.activeItems.length.toLocaleString("fr-FR")}` },
            { value: "expired", label: `Expirés · ${view.expiredItems.length.toLocaleString("fr-FR")}` },
          ]}
        />
      </div>

      {view.tab === "expired" ? (
        <p className="mb-4 rounded-xl bg-side px-4 py-3 text-sm text-ink-soft">
          Ces visuels ne sont plus sous droits, mais les sites continuent d'être comparés à eux : ceux qui y sont encore en ligne sont dans « À traiter ».
          {admin ? " Pour en renouveler un, donnez-lui une nouvelle échéance : il revient dans les visuels actifs." : ""}{" "}
          <Link to={`${link("a-traiter")}?statut=expire`} className="font-medium text-ink underline underline-offset-2">Voir ce qui est encore en ligne</Link>
        </p>
      ) : null}

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <SearchField id="lib-search" placeholder="Rechercher un nom ou un tag" className="w-full sm:w-72" value={view.query} onChange={view.setQuery} />
        {view.tab === "active" ? (
          <div className="flex gap-2 overflow-x-auto pb-0.5 [scrollbar-width:none]">
            {statusChips(view.activeItems).map((item) => (
              <Chip key={item.value} active={view.filter === item.value} dot={item.dot} count={item.count} onClick={() => view.setFilter(item.value)}>
                {item.label}
              </Chip>
            ))}
          </div>
        ) : null}
        <div className="ml-auto">
          <Segmented label="Trier par" value={view.sort} onChange={view.setSort} options={[{ value: "expiry", label: "Échéance" }, { value: "name", label: "Nom" }]} />
        </div>
      </div>

      <TagFilters view={view} />

      <datalist id="library-tags">
        {view.libraryTags.map(([tag]) => <option key={tag} value={tag} />)}
      </datalist>
    </>
  );
}

function TagFilters({ view }: { view: LibraryView }) {
  if (!view.poolTags.length) return <div className="mb-5" />;
  return (
    <div className="mb-5 flex flex-wrap items-center gap-2" role="group" aria-label="Filtrer par tag">
      <span className="text-[12.5px] text-muted">Tags</span>
      {(view.allTags ? view.poolTags : view.poolTags.slice(0, TAGS_SHOWN)).map(([tag, count]) => (
        <Chip key={tag} active={view.activeTags.includes(tag)} count={count} onClick={() => view.toggleTag(tag)}>{tag}</Chip>
      ))}
      {view.poolTags.length > TAGS_SHOWN ? (
        <button type="button" className="text-[13px] text-muted underline underline-offset-2 hover:text-ink" onClick={view.toggleAllTags}>
          {view.allTags ? "Moins de tags" : `Voir les ${view.poolTags.length} tags`}
        </button>
      ) : null}
      {view.untaggedCount ? (
        <Chip active={view.untagged} count={view.untaggedCount} onClick={view.toggleUntagged}>Sans tag</Chip>
      ) : null}
    </div>
  );
}
