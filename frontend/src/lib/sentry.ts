import { breadcrumbsIntegration, init, reactRouterBrowserTracingIntegration, wrapCreateBrowserRouter } from "@sentry/react";
import type { QueryClient } from "@tanstack/react-query";
import { matchRoutes } from "react-router";
import { ApiError } from "./api";
import type { SentryConfig } from "./config";
import type { Router } from "./monitoring";
import { scrubBreadcrumb, scrubEvent, scrubSpan, type Scope } from "./scrub";
import type { Organization } from "../types";

export { captureException } from "@sentry/react";

/** Hosts of the page's own fonts (index.html): they keep their name in what is sent. */
const FONT_HOSTS = ["fonts.googleapis.com", "fonts.gstatic.com"];

/** `/o/remy-martin/m/louis-xiii/bibliotheque` -> `/o/:slug/m/:brand/bibliotheque`, from the router's own routes. */
function routePattern(router: Router, pathname: string): string {
  const parts = matchRoutes(router.routes, pathname)?.map((match) => match.route.path ?? "") ?? ["*"];
  return `/${parts.join("/").split("/").filter(Boolean).join("/")}`;
}

/** What this client calls its own, from the answers the app already holds (same keys as `useOrganizations` and `useMe` in `org.tsx`). */
function clientNames(queryClient: QueryClient): string[] {
  const orgs = queryClient.getQueryData<Organization[]>(["orgs"]) ?? [];
  const me = queryClient.getQueryData<{ email: string | null }>(["me"]);
  return [...orgs.flatMap((org) => [org.name, org.slug, ...org.brands.flatMap((brand) => [brand.name, brand.slug])]), me?.email ?? ""];
}

/** The status of the first `ApiError` in an error and its `cause` chain (bounded: a cycle cannot loop). */
function apiStatus(error: unknown): number | undefined {
  for (let depth = 0; depth < 10 && error instanceof Error; depth++, error = error.cause) {
    if (error instanceof ApiError) return error.status;
  }
  return undefined;
}

// The integration asks for four React Router functions. A data router (`createBrowserRouter`) only needs
// `matchRoutes`; the other three serve the declarative <Routes> API, which this app does not use. Importing
// the real ones would add ~1.3 KB to the first-load `react` chunk (checked on the build), so they are inert.
const routerHooks = { matchRoutes, useLocation: () => window.location, useNavigationType: () => "POP" as const, createRoutesFromChildren: () => [] };

/**
 * Errors, Web Vitals and navigation/API timings, to the DSN the server gave. Everything
 * leaving the browser goes through `scrub.ts`. No replay, no profiling, no user.
 */
export function startSentry(config: SentryConfig, supabaseUrl: string, router: Router, queryClient: QueryClient): void {
  const scope: Scope = {
    origin: window.location.origin,
    hosts: [window.location.hostname, new URL(supabaseUrl, window.location.origin).hostname, ...FONT_HOSTS],
    routeOf: (pathname) => routePattern(router, pathname),
    names: () => clientNames(queryClient),
  };
  init({
    dsn: config.dsn,
    environment: config.environment,
    release: config.release || undefined,
    // v11 replaced `sendDefaultPii` by `dataCollection`: nothing about the user, no headers, cookies, bodies or query strings.
    dataCollection: { userInfo: false, cookies: false, httpHeaders: false, httpBodies: [], urlQueryParams: false, stackFrameVariables: false },
    tracesSampleRate: config.tracesSampleRate,
    maxValueLength: 500,
    // Spans are streamed (the SDK's default): LCP, CLS and INP leave as spans of their own when they are final,
    // which a page-load transaction closed one second after load could not wait for.
    // Trace headers go to our own API and nowhere else (Supabase, signed storage links). Only calls made after the
    // SDK has started carry them: the first requests of the page (configuration, session) run before it is downloaded.
    tracePropagationTargets: [/^\/api\//],
    // Not worth a span each: our own build files (cached, hashed) and every image (one per library thumbnail, so 100+ on a
    // big library). The page-load timings, the LCP span (with the image's host) and the API calls say what they would.
    // Files from other hosts (fonts) are kept.
    ignoreSpans: [{ op: "resource.img" }, { op: /^resource\./, attributes: { "server.address": window.location.host } }],
    integrations: [reactRouterBrowserTracingIntegration(routerHooks), breadcrumbsIntegration({ dom: false })],
    beforeSend: (event, hint) => scrubEvent(event, scope, apiStatus(hint.originalException)),
    beforeSendSpan: (span) => scrubSpan(span, scope),
    beforeBreadcrumb: (crumb) => scrubBreadcrumb(crumb, scope),
  });
  // The router exists before this chunk is loaded: let the integration follow it.
  wrapCreateBrowserRouter(() => router)(router.routes);
}
