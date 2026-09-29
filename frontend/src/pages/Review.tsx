import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router";
import { CompareView } from "../components/CompareView";
import { Modal, useToast } from "../components/feedback";
import { Button, Card, ConfidenceBadge, DecisionBadge, EmptyState, FieldLabel, Kbd, LinkButton, PageHeader, SearchField, Select, Skeleton, StatusBadge, Thumb, cx } from "../components/ui";
import { errorMessage } from "../lib/api";
import { daysText, decisionLabel, hostOf, pathOf, plural } from "../lib/format";
import { useOrg } from "../lib/org";
import { useMatches, useOverview, useReview } from "../lib/queries";
import type { Decision, Hit, MatchGroup, Status } from "../types";

type Tab = "found" | "verify" | "missing" | "later";
type DecisionFilter = "open" | "undecided" | Decision | "all";

const decisionFilters: { value: DecisionFilter; label: string }[] = [
  { value: "open", label: "À traiter" },
  { value: "undecided", label: "Sans décision" },
  { value: "retenu", label: "À retirer" },
  { value: "traite", label: "Retirés" },
  { value: "ecarte", label: "Faux positifs" },
  { value: "all", label: "Toutes" },
];

type Row = { key: string; group: MatchGroup; hit: Hit };

function keep(hit: Hit, filter: DecisionFilter): boolean {
  if (filter === "all") return true;
  if (filter === "open") return hit.decision === null || hit.decision === "retenu";
  if (filter === "undecided") return hit.decision === null;
  return hit.decision === filter;
}

export function Review() {
  const { link } = useOrg();
  const toast = useToast();
  const overview = useOverview();
  const [params, setParams] = useSearchParams();
  const defaultWindow = overview.data?.defaults.within_days ?? 90;
  const withinDays = Number(params.get("fenetre") ?? defaultWindow);
  const statusFilter = (params.get("statut") ?? "all") as Status | "all";
  const decisionFilter = (params.get("decision") ?? "open") as DecisionFilter;
  const tab = (params.get("onglet") ?? "found") as Tab;
  const [query, setQuery] = useState("");
  const [shown, setShown] = useState(100);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);
  const matches = useMatches(withinDays);
  const review = useReview(withinDays);
  const listRef = useRef<HTMLDivElement>(null);

  function setParam(name: string, value: string | null) {
    const next = new URLSearchParams(params);
    if (value === null) next.delete(name);
    else next.set(name, value);
    setParams(next, { replace: true });
    setShown(100);
  }

  const data = matches.data;
  const needle = query.trim().toLowerCase();
  const byFilters = (groups: MatchGroup[]) =>
    groups.filter((group) => (statusFilter === "all" || group.status === statusFilter) && (!needle || group.filename.toLowerCase().includes(needle)));

  const rows: Row[] = useMemo(() => {
    const source = tab === "found" ? data?.confirmed : tab === "verify" ? data?.to_verify : tab === "later" ? data?.later : [];
    const out: Row[] = [];
    for (const group of byFilters(source ?? [])) {
      for (const hit of group.hits) {
        if (keep(hit, decisionFilter)) out.push({ key: `${group.reference_id}:${hit.site_image_id}`, group, hit });
      }
    }
    return out;
    // byFilters depends on the values listed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, tab, statusFilter, decisionFilter, needle]);

  const missing = useMemo(
    () => (data?.not_found ?? []).filter((item) => (statusFilter === "all" || item.status === statusFilter) && (!needle || item.filename.toLowerCase().includes(needle))),
    [data, statusFilter, needle],
  );

  const selectedIndex = rows.findIndex((row) => row.key === selectedKey);
  const selected = selectedIndex >= 0 ? rows[selectedIndex] : null;

  // Keep a selection on wide screens so the comparison panel is never empty.
  useEffect(() => {
    if (!selected && rows.length && window.matchMedia("(min-width: 1024px)").matches) setSelectedKey(rows[0].key);
  }, [rows, selected]);

  function decide(row: Row, decision: Decision | "", everywhere = false) {
    const hits = everywhere ? row.group.hits : [row.hit];
    const ids = hits.flatMap((hit) => hit.site_image_ids);
    const nextKey = rows[selectedIndex + 1]?.key ?? rows[selectedIndex - 1]?.key ?? null;
    // What each occurrence was before, so "Annuler" puts every one back.
    const before = new Map<Decision | "", string[]>();
    for (const hit of hits) before.set(hit.decision ?? "", [...(before.get(hit.decision ?? "") ?? []), ...hit.site_image_ids]);
    const undo = () => {
      for (const [previous, siteImageIds] of before) {
        review.mutate(
          { referenceId: row.group.reference_id, siteImageIds, decision: previous },
          { onError: (error) => toast.show({ tone: "error", message: "L'annulation n'a pas abouti", description: errorMessage(error) }) },
        );
      }
    };
    review.mutate(
      { referenceId: row.group.reference_id, siteImageIds: ids, decision },
      {
        onSuccess: () =>
          toast.show({
            tone: "success",
            message: decision
              ? `${decisionLabel[decision]}${hits.length > 1 ? ` · ${plural(hits.length, "occurrence")}` : ""}`
              : "Décision retirée",
            description: row.group.filename,
            action: { label: "Annuler", onClick: undo },
            duration: 5000,
          }),
        onError: (error) =>
          toast.show({ tone: "error", message: "La décision n'a pas été enregistrée", description: `${errorMessage(error)} Elle a été annulée à l'écran.` }),
      },
    );
    // Move on when the decided row will leave the current filter.
    if (decision && !keep({ ...row.hit, decision }, decisionFilter)) setSelectedKey(nextKey);
  }

  function move(delta: number) {
    if (!rows.length) return;
    const index = selectedIndex < 0 ? 0 : Math.min(rows.length - 1, Math.max(0, selectedIndex + delta));
    setSelectedKey(rows[index].key);
    if (index >= shown) setShown(index + 20);
    listRef.current?.querySelector(`[data-key="${CSS.escape(rows[index].key)}"]`)?.scrollIntoView({ block: "nearest" });
  }

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement;
      if (target.closest("input, select, textarea, [role=dialog]") || event.metaKey || event.ctrlKey || event.altKey) return;
      const key = event.key.toLowerCase();
      if (key === "j" || key === "arrowdown") move(1);
      else if (key === "k" || key === "arrowup") move(-1);
      else if (selected && key === "r") decide(selected, selected.hit.decision === "retenu" ? "" : "retenu");
      else if (selected && key === "f") decide(selected, selected.hit.decision === "ecarte" ? "" : "ecarte");
      else if (selected && key === "t") decide(selected, selected.hit.decision === "traite" ? "" : "traite");
      else if (selected && key === "u") decide(selected, "");
      else return;
      event.preventDefault();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const counts = {
    found: data?.confirmed.length ?? 0,
    verify: data?.to_verify.length ?? 0,
    missing: data?.not_found.length ?? 0,
    later: data?.later.length ?? 0,
  };

  return (
    <>
      <PageHeader
        title="À traiter"
        description="Les visuels de la bibliothèque retrouvés sur le site, du plus urgent au moins urgent. Décidez occurrence par occurrence."
        actions={<span className="hidden items-center gap-1.5 text-xs text-muted md:flex"><Kbd>J</Kbd><Kbd>K</Kbd> naviguer · <Kbd>R</Kbd> à retirer · <Kbd>F</Kbd> faux positif · <Kbd>T</Kbd> retiré</span>}
      />

      <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <div>
          <FieldLabel htmlFor="window">Échéance</FieldLabel>
          <Select id="window" value={String(withinDays)} onChange={(event) => setParam("fenetre", event.target.value)}>
            {[30, 90, 180, 365].map((days) => <option key={days} value={days}>Expirés ou sous {days} jours</option>)}
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

      {matches.isLoading ? (
        <div className="grid gap-2">{Array.from({ length: 6 }, (_, index) => <Skeleton key={index} className="h-16" />)}</div>
      ) : matches.error ? (
        <EmptyState title="Impossible de charger les correspondances" body={errorMessage(matches.error)} action={<Button onClick={() => void matches.refetch()}>Réessayer</Button>} />
      ) : tab === "missing" ? (
        <MissingList items={missing} />
      ) : !rows.length ? (
        <EmptyState
          title={counts.found + counts.verify === 0 ? "Aucune correspondance pour l'instant" : "Rien ne correspond à ces filtres"}
          body={counts.found + counts.verify === 0 ? "Lancez une lecture du site pour comparer la bibliothèque à ce qui est en ligne." : "Élargissez l'échéance ou choisissez « Toutes » les décisions."}
          action={counts.found + counts.verify === 0 ? <LinkButton to={link("lectures")} variant="primary">Lire le site</LinkButton> : undefined}
        />
      ) : (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,480px)]">
          <div className="self-start rounded-2xl border border-line bg-sunk">
            <div ref={listRef} role="listbox" aria-label="Occurrences" className="flex flex-col gap-1 p-2">
              {rows.slice(0, shown).map((row) => (
                <button
                  key={row.key}
                  data-key={row.key}
                  type="button"
                  role="option"
                  aria-selected={row.key === selectedKey}
                  onClick={() => {
                    setSelectedKey(row.key);
                    if (!window.matchMedia("(min-width: 1024px)").matches) setPanelOpen(true);
                  }}
                  className={cx(
                    "flex w-full items-center gap-3.5 rounded-[14px] border px-3 py-2.5 text-left transition-colors",
                    row.key === selectedKey ? "border-[#e6d3c2] bg-paper shadow-[0_1px_2px_rgb(74_48_20/0.08),0_0_0_3px_var(--color-peach-soft)]" : "border-transparent hover:bg-paper",
                    row.hit.decision === "ecarte" && "opacity-55",
                  )}
                >
                  <span className="flex shrink-0 gap-1">
                    <Thumb src={row.group.ref_thumb} size={46} />
                    <Thumb src={row.hit.site_thumb} size={46} />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-medium">{row.group.filename}</span>
                    <span className="mt-0.5 block truncate font-mono text-[11.5px] text-muted">
                      {hostOf(row.hit.site_url)} · {row.hit.pages[0] ? pathOf(row.hit.pages[0]) : pathOf(row.hit.site_url)}
                      {row.hit.page_count > 1 ? ` et ${row.hit.page_count - 1} autre(s) page(s)` : ""}
                    </span>
                  </span>
                  <span className="hidden shrink-0 flex-col items-end gap-1 sm:flex">
                    <StatusBadge status={row.group.status} label={daysText(row.group.days_left)} />
                    <span className="flex items-center gap-1.5">
                      <ConfidenceBadge confidence={row.hit.confidence} />
                      <DecisionBadge decision={row.hit.decision} />
                    </span>
                  </span>
                </button>
              ))}
            </div>
            {rows.length > shown ? (
              <div className="border-t border-line p-3 text-center">
                <Button size="sm" onClick={() => setShown((value) => value + 100)}>Afficher plus · {shown}/{rows.length}</Button>
              </div>
            ) : null}
            <p className="border-t border-line px-4 py-2.5 text-xs text-muted">{plural(rows.length, "occurrence")}</p>
          </div>

          <div className="hidden lg:block">
            <div className="sticky top-6">
              <Card className="shadow-[0_12px_32px_rgb(74_48_20/0.06)]">{selected ? <CompareView group={selected.group} hit={selected.hit} busy={review.isPending} onDecide={(decision, everywhere) => decide(selected, decision, everywhere)} /> : <p className="text-sm text-muted">Sélectionnez une occurrence.</p>}</Card>
            </div>
          </div>
        </div>
      )}

      <Modal open={panelOpen && selected !== null} onClose={() => setPanelOpen(false)} title="Comparer" wide>
        {selected ? <CompareView group={selected.group} hit={selected.hit} busy={review.isPending} onDecide={(decision, everywhere) => decide(selected, decision, everywhere)} /> : null}
      </Modal>
    </>
  );
}

function MissingList(props: { items: NonNullable<ReturnType<typeof useMatches>["data"]>["not_found"] }) {
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
