import type { createBrowserRouter } from "react-router";
import { loadConfig } from "./config";

type Router = ReturnType<typeof createBrowserRouter>;

// Set once the Sentry chunk has started. Until then, and for good when `SENTRY_BROWSER_DSN`
// is empty, `reportError` does nothing and no Sentry code is ever downloaded.
let report: ((error: unknown) => void) | null = null;

/**
 * Loads Sentry (its own chunk, off the first load) once the page is loaded and the server
 * has handed over a DSN. The only door to the SDK: nothing else imports it.
 */
export async function startMonitoring(router: Router): Promise<void> {
  const config = await loadConfig().catch(() => null); // AuthProvider already shows an unreadable configuration
  if (!config?.sentry) return;
  if (document.readyState !== "complete") await new Promise((done) => window.addEventListener("load", done, { once: true }));
  try {
    const sentry = await import("./sentry");
    sentry.startSentry(config.sentry, router);
    report = sentry.captureError;
  } catch (error) {
    console.warn("Le suivi des erreurs n'a pas pu démarrer.", error); // the interface works without it
  }
}

/** Sends an error caught by an error boundary; a no-op when Sentry is off or not loaded yet. */
export function reportError(error: unknown): void {
  report?.(error);
}
