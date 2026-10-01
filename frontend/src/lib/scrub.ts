import type { Breadcrumb, BrowserOptions, ErrorEvent } from "@sentry/react";

/**
 * What may leave the browser. Reference file names, site URLs, organization and brand
 * names or slugs and e-mail addresses are client data: everything Sentry is about to send
 * goes through these functions first. They edit what Sentry hands them (its own copy) and
 * return it. `scripts/check-scrub.mjs` runs them on sample data.
 *
 * Two layers: a blacklist of what client data looks like (URLs, ids, e-mail addresses, file names,
 * selectors) and the exact names the signed-in user's own data holds (`Scope.names`). Free text
 * written by third parties (extensions, browsers) is cleaned on a best-effort basis.
 */

export type Scope = {
  /** This page's origin: only its own paths are kept (as patterns). */
  origin: string;
  /** The host names that keep their name: this page's, Supabase's, the font hosts. Any other host becomes `[external]`. */
  hosts: string[];
  /** A path of the app -> its route pattern (`/o/:slug/m/:brand/bibliotheque`). */
  routeOf: (pathname: string) => string;
  /** Names of this client's own data (organizations, brands, slugs, the user's e-mail), read on every use: they load after the page. */
  names: () => string[];
};

type Span = Parameters<NonNullable<BrowserOptions["beforeSendSpan"]>>[0];

const UUID = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/gi;
const EMAIL = /[^\s@<>"']+@[^\s@<>"']+\.[^\s@<>"']+/g;
const FILE_EXTENSION = /\.(?:jpe?g|png|gif|webp|avif|tiff?|bmp|heic|svg|pdf|csv|xlsx?|zip)\b/i;
const SELECTOR_ATTRIBUTE = /\[[\w-]+="[^"]*"\]/g; // [alt="…"], [aria-label="…"]: in the element selectors of clicks and web vitals
const URL_IN_TEXT = /https?:\/\/[^\s"'<>)]+/g;
const APP_PATH_IN_TEXT = /\/o\/[^\s"'<>)]+/g;
const BARE_HOST = /^(?:[\w-]+\.)+[\w-]+(?::\d+)?$/; // a resource span is named after its host
const HOST_IN_PARENS = /\(((?:[\w-]+\.)+[\w-]+(?::\d+)?)\)/g; // "Failed to fetch (www.client-site.com)": Sentry appends the host
const HTTP_NAME = /^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS) (\S+)$/; // `GET /path`, or `GET host` on streamed spans
const URL_OR_PATH = /^(?:https?:\/\/|\/)/;
const SENSITIVE_KEY = /query|fragment|header|body|cookie/i;
const HOST_KEY = /address|host|domain/i;
/** The files Vite builds: `/assets/index-42NU51MY.js`. Any other `/assets/…` path is not ours to name. */
const BUILT_ASSET = /^\/assets\/[\w.-]+-[\w-]{8}\.(?:js|css)$/;
/** What the page does, as breadcrumbs. Everything else (console, clicks, inputs) can carry text typed or shown by the user. */
const KEPT_BREADCRUMBS = new Set(["navigation", "fetch", "xhr"]);
/** The `/library/<x>` routes of the API that are not a file name. */
const LIBRARY_ACTIONS = new Set(["delete", "expiry", "export-csv", "import-csv", "tags", "thumbs", "upload"]);
/** Longest text kept: also bounds the regex work below (the e-mail pattern is quadratic on long runs without `@`). */
const MAX_TEXT = 500;
/** Names shorter than this are not replaced: they would eat ordinary words. */
const MIN_NAME = 3;

/** `/api/orgs/<uuid>/brands/<uuid>/library/<name>/url` -> `/api/orgs/:id/brands/:id/library/:file/url`. */
function apiPath(path: string): string {
  const segments = path.replace(UUID, ":id").split("/");
  const next = segments.indexOf("library") + 1;
  if (next === 0) return segments.join("/");
  // After `library`: a lone action keeps its name, a trailing `url` too; the rest is a file name.
  const tail = segments.slice(next);
  const keep = (segment: string, index: number) => !segment || (index === 0 ? tail.length === 1 && LIBRARY_ACTIONS.has(segment) : segment === "url");
  return [...segments.slice(0, next), ...tail.map((segment, index) => (keep(segment, index) ? segment : ":file"))].join("/");
}

// The one piece of shared state: the last pattern built, and the names it was built from (one entry: it cannot grow).
let nameCache: { key: string; pattern: RegExp | null } = { key: "", pattern: null };

/** The known names of this client as one case-insensitive pattern; words may be separated by any punctuation (`Remy Martin`, `remy-martin`, `remy_martin`). */
function namesPattern(list: string[]): RegExp | null {
  const key = list.join("\n");
  if (key !== nameCache.key) {
    // ponytail: one alternation of every name; fine for the few organizations and brands of a user, upgrade to a Set lookup if one account ever holds thousands.
    const words = [...new Set(list.filter((name) => name.length >= MIN_NAME))].sort((a, b) => b.length - a.length);
    const alternatives = words.map((name) => name.split(/[^\p{L}\p{N}]+/u).filter(Boolean).join("[^\\p{L}\\p{N}]*")); // only letters and digits remain: nothing to escape
    nameCache = { key, pattern: alternatives.length ? new RegExp(alternatives.join("|"), "giu") : null };
  }
  return nameCache.pattern;
}

function named(text: string, scope: Scope): string {
  const pattern = namesPattern(scope.names());
  return pattern ? text.replace(pattern, "[name]") : text;
}

const scrubHost = (host: string, scope: Scope) => (scope.hosts.includes(host.replace(/:\d+$/, "")) ? host : "[external]");

function rewriteUrl(raw: string, scope: Scope): string {
  let url: URL;
  try {
    url = new URL(raw, scope.origin);
  } catch {
    return "[url]"; // not a URL after all: say nothing about it
  }
  if (!/^https?:$/.test(url.protocol)) return "[url]"; // blob: and data: URLs
  if (url.origin !== scope.origin) return scope.hosts.includes(url.hostname) ? url.origin : "[external]"; // signed storage links hold file names and tokens
  if (BUILT_ASSET.test(url.pathname)) return url.origin + url.pathname;
  if (!url.pathname.startsWith("/api/")) return url.origin + scope.routeOf(url.pathname);
  const keys = [...new Set(url.searchParams.keys())];
  return `${url.origin}${apiPath(url.pathname)}${keys.length ? `?${keys.join("&")}` : ""}`;
}

/** A URL without what identifies a client: no query values, no fragment (invitation links carry tokens there), no unknown host, no known name. */
export function scrubUrl(raw: string, scope: Scope): string {
  return named(rewriteUrl(raw, scope), scope);
}

/** `scrubUrl`, keeping a path relative when it was. */
function scrubRef(value: string, scope: Scope): string {
  const clean = scrubUrl(value, scope);
  return value.startsWith("/") && clean.startsWith(scope.origin) ? clean.slice(scope.origin.length) : clean;
}

function cleanText(text: string, scope: Scope): string {
  const http = HTTP_NAME.exec(text);
  if (http) return `${http[1]} ${URL_OR_PATH.test(http[2]) ? scrubRef(http[2], scope) : scrubHost(http[2], scope)}`;
  const clean = text
    .replace(SELECTOR_ATTRIBUTE, "")
    .replace(URL_IN_TEXT, (url) => rewriteUrl(url, scope))
    .replace(HOST_IN_PARENS, (_, host: string) => `(${scrubHost(host, scope)})`)
    .replace(APP_PATH_IN_TEXT, (path) => scope.routeOf(path))
    .replace(UUID, ":id")
    .replace(EMAIL, "[email]");
  return FILE_EXTENSION.test(clean) ? "[text naming a file]" : clean;
}

/**
 * Free text (error messages, span names): URLs, ids, e-mail addresses, selector attributes and the client's own names out,
 * cut to `MAX_TEXT` characters. A text that still names an image or a document is dropped whole: a file name can hold
 * spaces, so there is no telling where it starts.
 */
export function scrubText(text: string, scope: Scope): string {
  return named(cleanText(text.slice(0, MAX_TEXT), scope), scope);
}

const scrubString = (value: string, scope: Scope) => (URL_OR_PATH.test(value) ? scrubRef(value, scope) : scrubText(value, scope));

/** Span and breadcrumb attributes: queries, fragments, headers and bodies dropped (whatever their value), hosts and every other string cleaned. */
export function scrubData(data: Record<string, unknown>, scope: Scope): void {
  for (const [key, value] of Object.entries(data)) {
    if (SENSITIVE_KEY.test(key)) delete data[key];
    else if (typeof value === "string") data[key] = HOST_KEY.test(key) ? scrubHost(value, scope) : scrubString(value, scope);
  }
}

/** Every span the SDK streams, whatever makes it (navigation, API call, file, web vital, interaction). */
export function scrubSpan(span: Span, scope: Scope): Span {
  if (span.name) span.name = BARE_HOST.test(span.name) ? scrubHost(span.name, scope) : scrubString(span.name, scope);
  if (span.attributes) scrubData(span.attributes, scope);
  return span;
}

export function scrubBreadcrumb(crumb: Breadcrumb, scope: Scope): Breadcrumb | null {
  if (!crumb.category || !KEPT_BREADCRUMBS.has(crumb.category)) return null;
  if (crumb.message) crumb.message = scrubString(crumb.message, scope);
  if (crumb.data) {
    crumb.data = { ...crumb.data }; // a failed fetch hands the SDK's own `fetchData` over: cleaning it in place would change the error's message
    scrubData(crumb.data, scope);
  }
  return crumb;
}

/**
 * `apiStatus`: the error, or one in its `cause` chain, is an `ApiError`, whose message is the server's (it can name a file):
 * the whole chain is reduced to its status.
 */
export function scrubEvent(event: ErrorEvent, scope: Scope, apiStatus?: number): ErrorEvent {
  event.user = undefined;
  event.extra = undefined;
  event.request = event.request?.url ? { url: scrubUrl(event.request.url, scope) } : undefined; // no headers (Referer)
  if (event.transaction) event.transaction = scrubString(event.transaction, scope);
  if (event.message) event.message = scrubText(event.message, scope);
  event.breadcrumbs = event.breadcrumbs?.flatMap((crumb) => scrubBreadcrumb(crumb, scope) ?? []);
  const trace = event.contexts?.trace;
  if (trace?.data) scrubData(trace.data, scope);
  if (typeof trace?.description === "string") trace.description = scrubString(trace.description, scope);
  for (const exception of event.exception?.values ?? []) {
    exception.type = apiStatus === undefined ? exception.type && scrubText(exception.type, scope) : "ApiError";
    exception.value = apiStatus === undefined ? exception.value && scrubText(exception.value, scope) : `HTTP ${apiStatus}`;
  }
  return event;
}
