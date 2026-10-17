import { useEffect, useRef, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router";
import { CompareView } from "../components/CompareView";
import { Modal } from "../components/feedback";
import { Button, Card, EmptyState, Kbd, LinkButton, PageHeader, Skeleton } from "../components/ui";
import { errorMessage } from "../lib/api";
import { useOrg } from "../lib/org";
import { useDecide, useShortcuts } from "./review/decide";
import { ReviewFilters, ReviewTabs } from "./review/Filters";
import { MissingList, OccurrenceList } from "./review/List";
import { RelatedReferences } from "./review/Related";
import { VerifyRemovals } from "./review/Removal";
import { focusOn, useReviewView, type ReviewView } from "./review/view";
import { Visual } from "./visual/Visual";

/** The list of what to deal with, or, with `?visual=<id>`, the page of one visual. */
export function Review() {
  const [params, setParams] = useSearchParams();
  const visual = params.get("visual");
  // Back to the list with the filters it had; a link opened from elsewhere has none, and the default window may not hold this visual.
  const back = () => setParams((current) => {
    current.delete("visual");
    if (!current.has("window")) current.set("window", "3650");
    return current;
  });
  return visual ? <Visual referenceId={visual} onBack={back} /> : <ReviewList />;
}

function ReviewList() {
  const view = useReviewView();
  const decide = useDecide(view);
  useShortcuts(view, decide);
  const { admin } = useOrg();
  const { selected, review, panelOpen, setPanelOpen, unchecked } = view;
  // Which of the photo's references is being compared: kept for the selected photo only.
  const [focus, setFocus] = useState<{ key: string; reference: string } | null>(null);
  const active = selected ? focusOn(selected, focus?.key === selected.key ? focus.reference : null) : null;
  const compare = selected && active ? (
    <div className="flex flex-col gap-4">
      <RelatedReferences pairs={[selected, ...selected.others]} activeId={active.group.reference_id} onPick={(reference) => setFocus({ key: active.key, reference })} />
      <CompareView group={active.group} hit={active.hit} related={active.others.length} busy={review.isPending} onDecide={(decision, everywhere) => decide(active, decision, everywhere)} />
    </div>
  ) : null;

  return (
    <>
      <PageHeader
        title="À traiter"
        actions={<span className="hidden items-center gap-1.5 text-xs text-muted md:flex"><Kbd>J</Kbd><Kbd>K</Kbd> naviguer · <Kbd>R</Kbd> à retirer · <Kbd>F</Kbd> faux positif · <Kbd>T</Kbd> retiré</span>}
      />
      <ReviewFilters view={view} />
      <ReviewTabs view={view} />
      {admin && unchecked ? <VerifyRemovals unchecked={unchecked} /> : null}
      <Results view={view} compare={compare} />
      <Modal open={panelOpen && selected !== null} onClose={() => setPanelOpen(false)} title="Comparer" wide>
        {compare}
      </Modal>
    </>
  );
}

/** Loading, error, the missing list, the empty states, or the occurrences beside the comparison panel. */
function Results(props: { view: ReviewView; compare: ReactNode }) {
  const { link } = useOrg();
  const { matches, tab, missing, rows, counts } = props.view;
  const noMatches = counts.found + counts.verify === 0;
  if (matches.isLoading) return <div className="grid gap-2">{Array.from({ length: 6 }, (_, index) => <Skeleton key={index} className="h-16" />)}</div>;
  if (matches.error) return <EmptyState title="Impossible de charger les correspondances" body={errorMessage(matches.error)} action={<Button onClick={() => void matches.refetch()}>Réessayer</Button>} />;
  if (tab === "missing") return <MissingList items={missing} />;
  if (!rows.length) {
    return (
      <EmptyState
        title={noMatches ? "Aucune correspondance pour l'instant" : "Rien ne correspond à ces filtres"}
        body={noMatches ? "Lancez une lecture du site pour comparer la bibliothèque à ce qui est en ligne." : "Élargissez l'échéance ou choisissez « Toutes » les décisions."}
        action={noMatches ? <LinkButton to={link("scans")} variant="primary">Lire le site</LinkButton> : undefined}
      />
    );
  }
  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,480px)]">
      <OccurrenceList view={props.view} />
      <div className="hidden lg:block">
        <StickyPanel>
          <Card>{props.compare ?? <p className="text-sm text-muted">Sélectionnez une occurrence.</p>}</Card>
        </StickyPanel>
      </div>
    </div>
  );
}

/** Sticks below the top of the screen and scrolls on its own, as tall as the screen left under it: before the page
 * has scrolled it starts lower than the top, and a fixed height would push its end off screen. */
function StickyPanel(props: { children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const fit = () => {
      if (ref.current) ref.current.style.maxHeight = `${Math.max(240, window.innerHeight - Math.max(ref.current.getBoundingClientRect().top, 24) - 24)}px`;
    };
    fit();
    window.addEventListener("scroll", fit, { passive: true });
    window.addEventListener("resize", fit);
    return () => {
      window.removeEventListener("scroll", fit);
      window.removeEventListener("resize", fit);
    };
  }, []);
  return <div ref={ref} className="sticky top-6 overflow-y-auto overscroll-contain [scrollbar-width:none]">{props.children}</div>;
}
