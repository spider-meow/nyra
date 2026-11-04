import { useEffect, useRef, useState } from "react";
import { useNavigation } from "react-router";
import { createNavProgress } from "../../lib/navProgress";
import { usePresence } from "../feedback";
import { cx } from "../ui";

/**
 * A thin bar at the top of the screen while the router loads the next page (the old page stays on screen until then), after a short
 * delay so that a fast page shows nothing (`lib/navProgress.ts`). The status line is for screen readers; the bar moves only for
 * people who have not asked for less motion (`.progress-indeterminate`, index.css).
 */
export function NavProgress() {
  const busy = useNavigation().state !== "idle";
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
      <p role="status" className="sr-only">{visible ? "Chargement de la page…" : ""}</p>
      {bar.mounted ? (
        <div
          role="progressbar"
          aria-label="Chargement de la page"
          onAnimationEnd={bar.onAnimationEnd}
          className={cx("fixed inset-x-0 top-0 z-50 h-0.5 overflow-hidden bg-yellow", bar.leaving ? "fade-out" : "fade-in")}
        >
          <div className="progress-indeterminate h-full w-1/4 bg-ink" />
        </div>
      ) : null}
    </>
  );
}
