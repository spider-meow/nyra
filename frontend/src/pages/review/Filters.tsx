import { FieldLabel, SearchField, Select, cx } from "../../components/ui";
import { WINDOWS, decisionFilters, type ReviewView } from "./view";

/** Window, status, decision and search. */
export function ReviewFilters(props: { view: ReviewView }) {
  const { withinDays, statusFilter, decisionFilter, query, setQuery, setParam } = props.view;
  return (
    <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <div>
        <FieldLabel htmlFor="window">Échéance</FieldLabel>
        <Select id="window" value={String(withinDays)} onChange={(event) => setParam("fenetre", event.target.value)}>
          {!WINDOWS.includes(withinDays) ? <option value={withinDays}>Expirés ou sous {withinDays} jours</option> : null}
          {WINDOWS.slice(0, -1).map((days) => <option key={days} value={days}>Expirés ou sous {days} jours</option>)}
          <option value="3650">Toutes les échéances</option>
        </Select>
      </div>
      <div>
        <FieldLabel htmlFor="status">Statut</FieldLabel>
        <Select id="status" value={statusFilter} onChange={(event) => setParam("statut", event.target.value === "all" ? null : event.target.value)}>
          <option value="all">Tous</option>
          <option value="expire">Expirés</option>
          <option value="<30j">Moins de 30 jours</option>
          <option value="<90j">Moins de 90 jours</option>
          <option value="inconnue">Sans échéance</option>
        </Select>
      </div>
      <div>
        <FieldLabel htmlFor="decision" hint={decisionFilter === "open" ? "sans décision ou à retirer" : undefined}>Décision</FieldLabel>
        <Select id="decision" value={decisionFilter} onChange={(event) => setParam("decision", event.target.value)}>
          {decisionFilters.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
        </Select>
      </div>
      <div>
        <FieldLabel htmlFor="search">Rechercher</FieldLabel>
        <SearchField id="search" placeholder="Nom de fichier" value={query} onChange={setQuery} />
      </div>
    </div>
  );
}

/** The four categories, with their counts. */
export function ReviewTabs(props: { view: ReviewView }) {
  const { tab, counts, setParam } = props.view;
  return (
    <div className="mb-5 flex gap-2 overflow-x-auto pb-1 [scrollbar-width:none]" role="tablist" aria-label="Catégories">
      {([
        ["found", "Trouvés", counts.found],
        ["verify", "À vérifier", counts.verify],
        ["missing", "Non trouvés", counts.missing],
        ["later", "Échéance plus lointaine", counts.later],
      ] as const).map(([value, label, count]) => (
        <button
          key={value}
          type="button"
          role="tab"
          aria-selected={tab === value}
          onClick={() => setParam("onglet", value === "found" ? null : value)}
          className={cx(
            "inline-flex h-9 shrink-0 items-center gap-2 rounded-full px-4 text-[13.5px] whitespace-nowrap transition-colors",
            tab === value ? "bg-ink font-medium text-paper" : "border border-line-strong bg-paper text-ink hover:border-faint",
          )}
        >
          {label} <span className={cx("tabular", tab === value ? "text-paper/70" : "text-muted")}>{count}</span>
        </button>
      ))}
    </div>
  );
}
