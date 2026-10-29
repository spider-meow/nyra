/**
 * When the « page is loading » bar shows (AXE-631). A navigation that ends within `SHOW_AFTER_MS` shows nothing (no flash on a fast page);
 * once shown, the bar stays at least `MIN_SHOWN_MS` (it does not vanish in a blink). Apart from React, and importing nothing, so that
 * `npm run check:navprogress` can run it with a fake clock.
 */
export const SHOW_AFTER_MS = 150;
export const MIN_SHOWN_MS = 300;

export type Timers = { now: () => number; set: (run: () => void, ms: number) => unknown; clear: (handle: unknown) => void };

/** `update(busy)` is told at each change of the router's state; `onChange(visible)` is told when the bar must appear or go. */
export function createNavProgress(onChange: (visible: boolean) => void, timers: Timers) {
  let shownAt: number | null = null; // null: hidden
  let timer: unknown = null;
  let kind: "show" | "hide" | null = null;

  const schedule = (next: "show" | "hide", ms: number, run: () => void) => {
    kind = next;
    timer = timers.set(() => {
      timer = null;
      kind = null;
      run();
    }, ms);
  };
  const cancel = () => {
    timers.clear(timer);
    timer = null;
    kind = null;
  };

  return {
    update(busy: boolean): void {
      if (busy) {
        if (kind === "hide") cancel(); // another navigation began while the bar was about to go: it stays
        if (shownAt === null && kind === null) {
          schedule("show", SHOW_AFTER_MS, () => {
            shownAt = timers.now();
            onChange(true);
          });
        }
      } else if (kind === "show") {
        cancel(); // over before the delay: nothing was shown
      } else if (shownAt !== null && kind === null) {
        schedule("hide", Math.max(0, shownAt + MIN_SHOWN_MS - timers.now()), () => {
          shownAt = null;
          onChange(false);
        });
      }
    },
    dispose: cancel,
  };
}
