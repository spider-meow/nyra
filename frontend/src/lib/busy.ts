import { useEffect, useRef, useState } from "react";

/**
 * When a refresh of data already on screen is SHOWN (AXE-632): not before it has lasted `SHOW_AFTER_MS` (an answer that comes at once changes nothing on
 * screen: no flash), and once shown, for at least `SHOW_AT_LEAST_MS` (no blink either). Pure functions and a small hook: `npm run check:busy` runs the
 * timeline on a fake clock.
 */
export const SHOW_AFTER_MS = 200;
export const SHOW_AT_LEAST_MS = 300;

/** `activeSince`: when the current load began (null: none); `shownSince`: when the indicator was last shown (null: it is not). Times in ms. */
export type BusyClock = { activeSince: number | null; shownSince: number | null };

/** Whether the indicator is shown at `now`, and in how many ms to look again (null: nothing to wait for). */
export function busyAt(clock: BusyClock, now: number, after = SHOW_AFTER_MS, atLeast = SHOW_AT_LEAST_MS): { show: boolean; recheckIn: number | null } {
  if (clock.activeSince !== null) {
    if (clock.shownSince !== null) return { show: true, recheckIn: null }; // loading and already shown: until it ends
    const waited = now - clock.activeSince;
    return waited >= after ? { show: true, recheckIn: null } : { show: false, recheckIn: after - waited };
  }
  if (clock.shownSince === null) return { show: false, recheckIn: null };
  const left = atLeast - (now - clock.shownSince); // the load ended: what is left of the minimum
  return left > 0 ? { show: true, recheckIn: left } : { show: false, recheckIn: null };
}

/** `active` (a refresh with data on screen) as what to show: false for a quick one, true from 200 ms to at least 300 ms after it ends. */
export function useRefreshing(active: boolean): boolean {
  const clock = useRef<BusyClock>({ activeSince: null, shownSince: null });
  const [show, setShow] = useState(false);
  useEffect(() => {
    const state = clock.current;
    if (active && state.activeSince === null) state.activeSince = Date.now();
    if (!active) state.activeSince = null;
    let timer: number | undefined;
    const look = () => {
      const now = Date.now();
      const next = busyAt(state, now);
      state.shownSince = next.show ? (state.shownSince ?? now) : null;
      setShow(next.show);
      if (next.recheckIn !== null) timer = window.setTimeout(look, next.recheckIn);
    };
    look();
    return () => window.clearTimeout(timer);
  }, [active]);
  return show;
}

/** The last value that was not undefined: what stays on screen (the counters of the tabs) while another answer comes. */
export function useLastKnown<T>(value: T | undefined): T | undefined {
  const [known, setKnown] = useState(value);
  if (value !== undefined && value !== known) setKnown(value);
  return value ?? known;
}
