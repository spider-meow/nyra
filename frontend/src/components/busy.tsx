import type { ReactNode } from "react";
import { errorMessage } from "../lib/api";
import { useRefreshing } from "../lib/busy";
import { Button, EmptyState, cx } from "./ui";

/**
 * The three states of a request that is shown (AXE-632), written once; `docs/ETATS_DE_CHARGEMENT.md` says what each page does:
 * - first load, nothing to show yet: a skeleton built from the markup of what arrives, with `Bar` for text and `Skeleton` for images and blocks (`SkeletonGrid` for a grid of cards);
 * - refresh with the content already there: `Refreshing` (the content stays, a little paler, with a thin line of progress after 200 ms, `aria-busy`
 *   and a polite announcement);
 * - failure: `LoadError` when there is nothing to show, `RefreshFailed` above the content that is.
 */

/** Wraps content that stays on screen while a refresh comes (`active`): paler after 200 ms, a thin line on top, `aria-busy`, a polite announcement. The focus is never moved. */
export function Refreshing(props: { active: boolean; children: ReactNode; label?: string; className?: string }) {
  const show = useRefreshing(props.active);
  return (
    <div aria-busy={props.active} className={cx("relative", props.className)}>
      {show ? <span aria-hidden className="fade-in absolute inset-x-0 -top-1.5 h-0.5 skeleton rounded-full bg-bark" /> : null}
      <span role="status" className="sr-only">{show ? props.label ?? "Mise à jour des résultats…" : ""}</span>
      <div className={cx("fade-in transition-opacity", show && "opacity-60")}>{props.children}</div>
    </div>
  );
}

/** The first load of a grid of cards: the same columns as the grid it stands for, each cell the skeleton of one real card (`children`), so nothing jumps when they arrive. */
export function SkeletonGrid(props: { count?: number; className: string; children: ReactNode }) {
  return (
    <div className={props.className} aria-busy="true">
      {Array.from({ length: props.count ?? 8 }, (_, index) => <div key={index}>{props.children}</div>)}
    </div>
  );
}

/** Nothing to show and the request failed: why, and a way to try again. */
export function LoadError(props: { title: string; error: unknown; onRetry: () => void; retrying?: boolean }) {
  return <EmptyState title={props.title} body={errorMessage(props.error)} action={<Button loading={props.retrying} onClick={props.onRetry}>Réessayer</Button>} />;
}

/** The content is there but its refresh failed: it may be out of date, and it says so (announced at once) next to a way to try again. */
export function RefreshFailed(props: { error: unknown; onRetry: () => void; retrying?: boolean }) {
  return (
    <p role="alert" className="fade-in mb-3 flex flex-wrap items-center gap-3 rounded-xl bg-soon-soft px-4 py-2.5 text-sm text-soon">
      La mise à jour a échoué, ce qui est affiché peut être périmé : {errorMessage(props.error)}
      <Button size="sm" loading={props.retrying} onClick={props.onRetry}>Réessayer</Button>
    </p>
  );
}
