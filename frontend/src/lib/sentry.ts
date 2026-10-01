import { breadcrumbsIntegration, captureException, init, reactRouterBrowserTracingIntegration, withStaticSpan, wrapCreateBrowserRouter } from "@sentry/react";
import { matchRoutes, type createBrowserRouter } from "react-router";
import { ApiError } from "./api";
import type { SentryConfig } from "./config";
import { scrubBreadcrumb, scrubEvent, scrubSpan, scrubTransaction, type Scope } from "./scrub";

type Router = ReturnType<typeof createBrowserRouter>;

/** `/o/remy-martin/m/louis-xiii/bibliotheque` -> `/o/:slug/m/:brand/bibliotheque`, from the router's own routes. */
function routePattern(router: Router, pathname: string): string {
  const parts = matchRoutes(router.routes, pathname)?.map((match) => match.route.path ?? "") ?? ["*"];
  return `/${parts.join("/").split("/").filter(Boolean).join("/")}`;
}

// The integration asks for four React Router functions. A data router (`createBrowserRouter`) only needs
// `matchRoutes`; the other three serve the declarative <Routes> API, which this app does not use. Importing
// the real ones would add ~1.3 KB to the first-load `react` chunk (checked on the build), so they are inert.
const routerHooks = { matchRoutes, useLocation: () => window.location, useNavigationType: () => "POP" as const, createRoutesFromChildren: () => [] };

/**
 * Errors, Web Vitals and navigation/API timings, to the DSN the server gave. Everything
 * leaving the browser goes through `scrub.ts`. No replay, no profiling, no user.
 */
export function startSentry(config: SentryConfig, router: Router): void {
  const scope: Scope = { origin: window.location.origin, routeOf: (pathname) => routePattern(router, pathname) };
  init({
    dsn: config.dsn,
    environment: config.environment,
    release: config.release || undefined,
    // v11 replaced `sendDefaultPii` by `dataCollection`: nothing about the user, no headers, cookies, bodies or query strings.
    dataCollection: { userInfo: false, cookies: false, httpHeaders: false, httpBodies: [], urlQueryParams: false, stackFrameVariables: false },
    tracesSampleRate: config.tracesSampleRate,
    // Classic transactions (named by route, with Web Vitals as measurements): v11 streams spans by default.
    traceLifecycle: "static",
    // Trace headers go to our own API and nowhere else (Supabase, signed storage links).
    tracePropagationTargets: [/^\/api\//],
    integrations: [reactRouterBrowserTracingIntegration(routerHooks), breadcrumbsIntegration({ dom: false })],
    beforeSend: (event, hint) =>
      scrubEvent(event, scope, hint.originalException instanceof ApiError ? hint.originalException.status : undefined),
    beforeSendTransaction: (event) => scrubTransaction(event, scope),
    beforeSendSpan: withStaticSpan((span) => scrubSpan(span, scope)),
    beforeBreadcrumb: (crumb) => scrubBreadcrumb(crumb, scope),
  });
  // The router already exists (Sentry loads after the first render): let the integration follow it.
  wrapCreateBrowserRouter(() => router)(router.routes);
}

export function captureError(error: unknown): void {
  captureException(error);
}
