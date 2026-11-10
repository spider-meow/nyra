import { useIsFetching, useIsMutating, type Query } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useNavigation } from "react-router";
import { createNavProgress } from "../../lib/navProgress";
import { usePresence } from "../feedback";
import { cx } from "../ui";

/** Work nobody waits for: the jobs (polled every 2 or 20 s) and the thumbnails loaded as the cards scroll in. Neither lights the bar. */
const BACKGROUND_KEYS = new Set(["jobs", "library-thumbs"]);
const POLLING_UNDER_MS = 60_000;

/** A request someone waits for: not a background key, and not a query that polls often by itself (a function, or under a minute: the back-office gauges). A slow refresh that only keeps signed addresses fresh (30 min) still counts when it runs. */
function isForeground(query: Query): boolean {
  if (BACKGROUND_KEYS.has(String(query.queryKey[0]))) return false;
  return query.observers.every((observer) => {
    const every = observer.options.refetchInterval;
    return !(typeof every === "function" || (typeof every === "number" && every < POLLING_UNDER_MS));
  });
}

/**
 * THE bar of the whole interface (AXE-631, AXE-869): a thin line at the top of the screen while anything is being waited for, after a short
 * delay so that a fast answer shows nothing (`lib/navProgress.ts`). It follows the router loading the next page, every request whose answer
 * is shown (a search, a filter, a tab, « Voir plus », another brand) and every action in progress. The content on screen is never dimmed: it
 * stays as it is until the new answer replaces it. The status line is for screen readers; the bar moves only for people who have not asked
 * for less motion (`.progress-indeterminate`, index.css).
 */
export function NavProgress() {
  const fetching = useIsFetching({ predicate: isForeground }) > 0;
  const acting = useIsMutating() > 0;
  const busy = useNavigation().state !== "idle" || fetching || acting;
  const [visible, setVisible] = useState(false);
  const bar = usePresence(visible);
  const progress = useRef<ReturnType<typeof createNavProgress>>(null);
  useEffect(() => {
    const created = createNavProgress(setVisible, { now: () => performance.now(), set: (run, ms) => setTimeout(run, ms), clear: (handle) => clearTimeout(handle as number) });
    progress.current = created;
    return created.dispose;
  }, []);
  useEffect(() => progress.current?.update(busy), [busy]);
  return (
    <>
      <p role="status" className="sr-only">{visible ? "Chargement…" : ""}</p>
      {bar.mounted ? (
        <div
          role="progressbar"
          aria-label="Chargement"
          onAnimationEnd={bar.onAnimationEnd}
          className={cx("fixed inset-x-0 top-0 z-50 h-[3px] overflow-hidden bg-yellow", bar.leaving ? "fade-out" : "fade-in")}
        >
          <div className="progress-indeterminate h-full w-1/4 bg-ink" />
        </div>
      ) : null}
    </>
  );
}
