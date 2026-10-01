import type { QueryClient } from "@tanstack/react-query";
import type { createBrowserRouter } from "react-router";
import { loadConfig } from "./config";

export type Router = ReturnType<typeof createBrowserRouter>;

/** Errors reported before the SDK is up (the Sentry chunk is a download): kept, and sent once it starts. */
const MAX_PENDING = 10;

// Set once the Sentry chunk has started. Until then `reportError` keeps the first `MAX_PENDING` errors;
// when `SENTRY_BROWSER_DSN` is empty (or the SDK cannot start) both stay unset for good, the buffer is
// dropped, and no Sentry code is ever downloaded.
let report: ((error: unknown) => void) | null = null;
let pending: unknown[] | null = [];

/**
 * Loads Sentry (its own chunk, off the first load) once the page is loaded and the server
 * has handed over a DSN. The only door to the SDK: nothing else imports it. Called before
 * the first render, but it waits: the SDK arrives later than the first screen.
 */
export async function startMonitoring(router: Router, queryClient: QueryClient): Promise<void> {
  const config = await loadConfig().catch(() => null); // AuthProvider already shows an unreadable configuration
  if (!config?.sentry) {
    pending = null;
    return;
  }
  if (document.readyState !== "complete") await new Promise((done) => window.addEventListener("load", done, { once: true }));
  try {
    const sentry = await import("./sentry");
    sentry.startSentry(config.sentry, config.supabaseUrl, router, queryClient);
    report = sentry.captureException;
    for (const error of pending ?? []) report(error);
  } catch (error) {
    console.warn("Le suivi des erreurs n'a pas pu démarrer.", error); // the interface works without it
  }
  pending = null;
}

/** Sends an error caught by an error boundary: at once when Sentry is up, kept for it while it loads, dropped when it is off. */
export function reportError(error: unknown): void {
  if (report) report(error);
  else if (pending && pending.length < MAX_PENDING) pending.push(error);
}
