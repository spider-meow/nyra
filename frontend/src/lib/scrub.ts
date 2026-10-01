import type { Breadcrumb, BrowserOptions, ErrorEvent, withStaticSpan } from "@sentry/react";

/**
 * What may leave the browser. Reference file names, site URLs, organization and brand
 * names or slugs and e-mail addresses are client data: everything Sentry is about to send
 * goes through these functions first. They edit what Sentry hands them (its own copy) and
 * return it. `scripts/check-scrub.mjs` runs them on sample data.
 */

export type Scope = {
  /** This page's origin: only its own paths are kept (as patterns), other hosts are reduced to their origin. */
  origin: string;
  /** A path of the app -> its route pattern (`/o/:slug/m/:brand/bibliotheque`). */
  routeOf: (pathname: string) => string;
};

type TransactionEvent = Parameters<NonNullable<BrowserOptions["beforeSendTransaction"]>>[0];
type Span = Parameters<Parameters<typeof withStaticSpan>[0]>[0];

const UUID = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/gi;
const EMAIL = /[^\s@<>"']+@[^\s@<>"']+\.[^\s@<>"']+/g;
const FILE_EXTENSION = /\.(?:jpe?g|png|gif|webp|avif|tiff?|bmp|heic|svg|pdf|csv|xlsx?|zip)\b/i;
const SELECTOR_ATTRIBUTE = /\[[\w-]+="[^"]*"\]/g; // [alt="…"], [aria-label="…"]: in the element selectors of clicks and web vitals
const URL_IN_TEXT = /https?:\/\/[^\s"'<>)]+/g;
const APP_PATH_IN_TEXT = /\/o\/[^\s"'<>)]+/g;
const HTTP_NAME = /^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS) (\S+)$/;
const URL_OR_PATH = /^(?:https?:\/\/|\/)/;
const SENSITIVE_KEY = /query|fragment|header|body|cookie/i;
/** What the page does, as breadcrumbs. Everything else (console, clicks, inputs) can carry text typed or shown by the user. */
const KEPT_BREADCRUMBS = new Set(["navigation", "fetch", "xhr"]);
/** The `/library/<x>` routes of the API that are not a file name. */
const LIBRARY_ACTIONS = new Set(["delete", "expiry", "export-csv", "import-csv", "tags", "thumbs", "upload"]);

/** `/api/orgs/<uuid>/brands/<uuid>/library/<name>/url` -> `/api/orgs/:id/brands/:id/library/:file/url`. */
function apiPath(path: string): string {
  const segments = path.replace(UUID, ":id").split("/");
  const next = segments.indexOf("library") + 1;
  if (next > 0 && next < segments.length && !LIBRARY_ACTIONS.has(segments[next])) segments[next] = ":file";
  return segments.join("/");
}

/** A URL without what identifies a client: no query values, no fragment (invitation links carry tokens there). */
export function scrubUrl(raw: string, scope: Scope): string {
  let url: URL;
  try {
    url = new URL(raw, scope.origin);
  } catch {
    return "[url]"; // not a URL after all: say nothing about it
  }
  if (!/^https?:$/.test(url.protocol)) return "[url]"; // blob: and data: URLs
  if (url.origin !== scope.origin) return url.origin; // signed storage links hold file names and tokens
  if (url.pathname.startsWith("/assets/")) return url.origin + url.pathname;
  if (!url.pathname.startsWith("/api/")) return url.origin + scope.routeOf(url.pathname);
  const keys = [...new Set(url.searchParams.keys())];
  return `${url.origin}${apiPath(url.pathname)}${keys.length ? `?${keys.join("&")}` : ""}`;
}

/** `scrubUrl`, keeping a path relative when it was. */
function scrubRef(value: string, scope: Scope): string {
  const clean = scrubUrl(value, scope);
  return value.startsWith("/") && clean.startsWith(scope.origin) ? clean.slice(scope.origin.length) : clean;
}

/**
 * Free text (error messages, span names): URLs, ids, e-mail addresses and selector attributes out. A text that still
 * names an image or a document is dropped whole: a file name can hold spaces, so there is no telling where it starts.
 */
export function scrubText(text: string, scope: Scope): string {
  const http = HTTP_NAME.exec(text);
  if (http) return `${http[1]} ${scrubRef(http[2], scope)}`;
  const clean = text
    .replace(SELECTOR_ATTRIBUTE, "")
    .replace(URL_IN_TEXT, (url) => scrubUrl(url, scope))
    .replace(APP_PATH_IN_TEXT, (path) => scope.routeOf(path))
    .replace(UUID, ":id")
    .replace(EMAIL, "[email]");
  return FILE_EXTENSION.test(clean) ? "[text naming a file]" : clean;
}

const scrubString = (value: string, scope: Scope) => (URL_OR_PATH.test(value) ? scrubRef(value, scope) : scrubText(value, scope));

/** Span and breadcrumb attributes: queries, fragments, headers and bodies dropped, every other string cleaned. */
export function scrubData(data: Record<string, unknown>, scope: Scope): void {
  for (const [key, value] of Object.entries(data)) {
    if (typeof value !== "string") continue; // sizes, durations, status codes
    if (SENSITIVE_KEY.test(key)) delete data[key];
    else data[key] = scrubString(value, scope);
  }
}

export function scrubSpan(span: Span, scope: Scope): Span {
  if (span.description) span.description = scrubString(span.description, scope);
  scrubData(span.data, scope);
  return span;
}

export function scrubBreadcrumb(crumb: Breadcrumb, scope: Scope): Breadcrumb | null {
  if (!crumb.category || !KEPT_BREADCRUMBS.has(crumb.category)) return null;
  if (crumb.message) crumb.message = scrubString(crumb.message, scope);
  if (crumb.data) scrubData(crumb.data, scope);
  return crumb;
}

/** What error events and transactions have in common: no user, no extras, no headers (Referer), page URLs as patterns. */
function scrubCommon(event: ErrorEvent | TransactionEvent, scope: Scope): void {
  event.user = undefined;
  event.extra = undefined;
  event.request = event.request?.url ? { url: scrubUrl(event.request.url, scope) } : undefined;
  if (event.transaction) event.transaction = scrubString(event.transaction, scope);
  event.breadcrumbs = event.breadcrumbs?.flatMap((crumb) => scrubBreadcrumb(crumb, scope) ?? []);
  const trace = event.contexts?.trace;
  if (trace?.data) scrubData(trace.data, scope);
  if (typeof trace?.description === "string") trace.description = scrubString(trace.description, scope);
}

/** `apiStatus`: the event is an `ApiError`, whose message is the server's (it can name a file): only the status is kept. */
export function scrubEvent(event: ErrorEvent, scope: Scope, apiStatus?: number): ErrorEvent {
  scrubCommon(event, scope);
  if (event.message) event.message = scrubText(event.message, scope);
  for (const exception of event.exception?.values ?? []) {
    if (apiStatus !== undefined) {
      exception.type = "ApiError";
      exception.value = `HTTP ${apiStatus}`;
    } else if (exception.value) {
      exception.value = scrubText(exception.value, scope);
    }
  }
  return event;
}

export function scrubTransaction(event: TransactionEvent, scope: Scope): TransactionEvent {
  scrubCommon(event, scope);
  for (const span of event.spans ?? []) scrubSpan(span, scope);
  return event;
}
