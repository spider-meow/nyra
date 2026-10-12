import { useEffect, useRef, type ReactNode } from "react";
import { CompareView } from "../components/CompareView";
import { Modal } from "../components/feedback";
import { Button, Card, EmptyState, Kbd, LinkButton, PageHeader, Skeleton } from "../components/ui";
import { errorMessage } from "../lib/api";
import { useOrg } from "../lib/org";
import { useDecide, useShortcuts } from "./review/decide";
import { ReviewFilters, ReviewTabs } from "./review/Filters";
import { MissingList, OccurrenceList } from "./review/List";
import { useReviewView, type ReviewView } from "./review/view";

export function Review() {
  const view = useReviewView();
  const decide = useDecide(view);
  useShortcuts(view, decide);
  const { selected, review, panelOpen, setPanelOpen } = view;
  const compare = selected ? <CompareView group={selected.group} hit={selected.hit} busy={review.isPending} onDecide={(decision, everywhere) => decide(selected, decision, everywhere)} /> : null;

  return (
    <>
      <PageHeader
        title="À traiter"
        description="Les visuels de la bibliothèque retrouvés sur le site, du plus urgent au moins urgent. Décidez occurrence par occurrence."
        actions={<span className="hidden items-center gap-1.5 text-xs text-muted md:flex"><Kbd>J</Kbd><Kbd>K</Kbd> naviguer · <Kbd>R</Kbd> à retirer · <Kbd>F</Kbd> faux positif · <Kbd>T</Kbd> retiré</span>}
      />
      <ReviewFilters view={view} />
      <ReviewTabs view={view} />
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
        action={noMatches ? <LinkButton to={link("lectures")} variant="primary">Lire le site</LinkButton> : undefined}
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
