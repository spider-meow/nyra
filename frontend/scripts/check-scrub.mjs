// Runs the browser-monitoring scrubbers (src/lib/scrub.ts) on sample client data and fails if any of it
// survives. No test framework: the file is compiled in memory with the TypeScript compiler already in
// devDependencies. Run: `npm run check:scrub`. Spans are the shapes the SDK streams (name + attributes).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

const source = readFileSync(new URL("../src/lib/scrub.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } });
const scrub = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);

const ORG = "11111111-1111-1111-1111-111111111111";
const BRAND = "22222222-2222-2222-2222-222222222222";
let names = ["Remy Martin", "remy-martin", "Louis XIII", "louis-xiii", "a@x.com"];
const scope = {
  origin: "https://nyra.test",
  hosts: ["nyra.test", "x.supabase.co", "fonts.googleapis.com", "fonts.gstatic.com"],
  routeOf: (path) => (path.startsWith("/o/") ? path.replace(/^\/o\/[^/]+\/m\/[^/]+/, "/o/:slug/m/:brand") : "/*"), // like the router: unknown paths match `*`
  names: () => names,
};
const PRIVATE = ["bouteille-secrete", "remy-martin", "remy_martin", "louis-xiii", "Remy Martin", "Louis XIII", "a@x.com", "marie@client.org", ORG, BRAND, ".jpg", "token=", "#access", "SECRETTOKEN", "secret-site", "remymartin"];
const SITE = "https://www.remymartin-secret-site.com";

const api = `/api/orgs/${ORG}/brands/${BRAND}/library`;
const url = (raw) => scrub.scrubUrl(raw, scope);
const text = (raw) => scrub.scrubText(raw, scope);
const LIB = "https://nyra.test/api/orgs/:id/brands/:id/library";

// URLs: ids, file names and query values out, route patterns in.
assert.equal(url(`${api}/bouteille-secrete-2026.jpg/url?within=90&a=a@x.com`), `${LIB}/:file/url?within&a`);
assert.equal(url(`${api}?q=remy-martin&tag=Louis%20XIII`), `${LIB}?q&tag`); // private values that are not e-mail addresses
assert.equal(url(`${api}/thumbs`), `${LIB}/thumbs`);
assert.equal(url(`${api}/export-csv`), `${LIB}/export-csv`);
assert.equal(url(`${api}/thumbs/x.jpg`), `${LIB}/:file/:file`); // a file called like an action
assert.equal(url(`${api}/thumbs/url`), `${LIB}/:file/url`);
assert.equal(url(`${api}/Remy%20Martin.png`), `${LIB}/:file`);
assert.equal(url(`${api}/bouteille.pdf/url/deeper/still`), `${LIB}/:file/url/:file/:file`);
assert.equal(url(`https://x.supabase.co/storage/v1/sign/refs/${ORG}/bouteille-secrete.jpg?token=abc`), "https://x.supabase.co");
assert.equal(url("https://fonts.gstatic.com/s/geist/v1/abc.woff2"), "https://fonts.gstatic.com");
assert.equal(url(`${SITE}/img/Louis XIII bottle.jpg`), "[external]");
assert.equal(url("https://nyra.test/o/remy-martin/m/louis-xiii/bibliotheque?q=remy-martin#access_token=abc"), "https://nyra.test/o/:slug/m/:brand/bibliotheque");
assert.equal(url("blob:https://nyra.test/" + ORG), "[url]");
// Built files keep their name; any other /assets path falls through to the route pattern.
assert.equal(url("https://nyra.test/assets/index-abc12345.js"), "https://nyra.test/assets/index-abc12345.js");
assert.equal(url("https://nyra.test/assets/sentry-B_c-d1Ef.css"), "https://nyra.test/assets/sentry-B_c-d1Ef.css");
assert.equal(url("https://nyra.test/assets/Remy-Martin-Louis-XIII.jpg"), "https://nyra.test/*");
assert.equal(url("https://nyra.test/assets/remy-martin.js"), "https://nyra.test/*");
assert.equal(url("https://nyra.test/assets/Remy-Martin-abcdefgh.jpg"), "https://nyra.test/*"); // right shape, not a script or a style
assert.ok(!url("https://nyra.test/assets/Remy-Martin-abcdefgh.js").includes("Remy"), "a built-looking file still goes through the names");

// Free text.
assert.equal(text("GET " + api + "/bouteille-secrete-2026.jpg/url"), "GET /api/orgs/:id/brands/:id/library/:file/url");
assert.equal(text(`GET ${api}?q=remy-martin`), "GET /api/orgs/:id/brands/:id/library?q");
assert.equal(text("GET www.remymartin-secret-site.com"), "GET [external]"); // streamed http.client span name
assert.equal(text("GET nyra.test"), "GET nyra.test");
assert.equal(text(`Trop gros pour marie@client.org (${ORG})`), "Trop gros pour [email] (:id)");
assert.equal(text(`Impossible de lire bouteille-secrete-2026.jpg pour a@x.com (${ORG})`), "[text naming a file]");
assert.equal(text('body > div#root > img.thumb[alt="bouteille-secrete"][aria-label="Louis XIII"]'), "body > div#root > img.thumb");
assert.equal(text("Erreur sur /o/autre-client/m/autre-marque/bibliotheque."), "Erreur sur /o/:slug/m/:brand/bibliotheque."); // a path of the app, for a client not in the names
assert.equal(text(`Échec pour ${SITE}/page`), "Échec pour [external]");
assert.equal(text("Failed to fetch (www.remymartin-secret-site.com)"), "Failed to fetch ([external])");
assert.equal(text("Failed to fetch (x.supabase.co)"), "Failed to fetch (x.supabase.co)");
assert.equal(text("Failed to fetch (nyra.test:8443)"), "Failed to fetch (nyra.test:8443)");
assert.equal(text("http.client"), "http.client"); // attribute values of the SDK stay readable

// The client's own names, whatever the case or the separator, and nothing shorter than 3 characters.
assert.equal(text("Le visuel de REMY MARTIN, de remy_martin et de Louis-XIII, pour a@x.com"), "Le visuel de [name], de [name] et de [name], pour [email]");
names = ["ab", "Café Müller"];
assert.equal(text("about, Le CAFÉ-MÜLLER"), "about, Le [name]");
names = ["Remy Martin", "remy-martin", "Louis XIII", "louis-xiii", "a@x.com"];

// Long input is cut and stays fast (the e-mail pattern is quadratic on long runs without "@").
for (const long of ["a".repeat(60_000), "a@".repeat(30_000), "a.".repeat(30_000)]) {
  const start = performance.now();
  const clean = text(long);
  assert.ok(performance.now() - start < 250, `text of ${long.length} characters took ${Math.round(performance.now() - start)} ms`);
  assert.ok(clean.length <= 500, `kept ${clean.length} characters`);
}

// Spans as the SDK streams them: a name and flat attributes.
const span = (name, attributes) => scrub.scrubSpan({ name, attributes, trace_id: "t", span_id: "s", start_timestamp: 0, end_timestamp: 1, status: "ok", is_segment: false }, scope);
const spans = [
  span("GET www.remymartin-secret-site.com", { "sentry.op": "http.client", "url.full": `${SITE}/img/bouteille-secrete-2026.jpg?token=SECRETTOKEN`, "server.address": "www.remymartin-secret-site.com", "url.domain": "www.remymartin-secret-site.com", "http.query": "?q=remy-martin", "http.fragment": 7, "http.response.status_code": 200 }),
  span("GET nyra.test", { "url.full": `https://nyra.test${api}/bouteille-secrete-2026.jpg/url?x=1`, "server.address": "nyra.test", "url.domain": "nyra.test", "http.response.status_code": 200 }),
  span("www.remymartin-secret-site.com", { "sentry.op": "resource.img", "url.full": `${SITE}/a.png` }), // resource spans are named after their host
  span("Largest contentful paint", { "sentry.op": "ui.webvital.lcp", "browser.web_vital.lcp.value": 4732, "browser.web_vital.lcp.url": `https://x.supabase.co/storage/v1/sign/refs/${ORG}/bouteille-secrete-2026.jpg?token=SECRETTOKEN`, "browser.web_vital.lcp.element": 'img[aria-label="Louis XIII"][title="Bouteille Secrete.jpg"][alt="Bouteille Secrete"]' }),
  span("Click", { "sentry.op": "ui.interaction.click", "browser.web_vital.inp.target": 'button.save[aria-label="Remy Martin"]', "browser.web_vital.inp.value": 264 }),
  span("Layout shift", { "browser.web_vital.cls.source.1": 'body > div#root > img[alt="remy-martin"]', "browser.web_vital.cls.value": 0.61 }),
  span("/o/remy-martin/m/louis-xiii/bibliotheque", { "sentry.op": "navigation", "url.path": "/o/remy-martin/m/louis-xiii/bibliotheque", "url.full": "https://nyra.test/o/remy-martin/m/louis-xiii/bibliotheque?q=remy-martin", "sentry.segment.name": "/o/remy-martin/m/louis-xiii/bibliotheque" }),
  span("Main UI thread blocked", { "code.file.path": `${SITE}/tracker.js`, "browser.script.invoker": "https://nyra.test/assets/index-abc12345.js" }),
];
assert.equal(spans[0].name, "GET [external]");
assert.equal(spans[0].attributes["sentry.op"], "http.client");
assert.equal(spans[0].attributes["http.response.status_code"], 200);
assert.equal(spans[0].attributes["server.address"], "[external]");
assert.ok(!("http.query" in spans[0].attributes) && !("http.fragment" in spans[0].attributes), "sensitive keys are dropped whatever their value type");
assert.equal(spans[1].name, "GET nyra.test");
assert.equal(spans[1].attributes["url.domain"], "nyra.test");
assert.equal(spans[1].attributes["url.full"], `${LIB}/:file/url?x`);
assert.equal(spans[2].name, "[external]");
assert.equal(spans[3].attributes["browser.web_vital.lcp.url"], "https://x.supabase.co");
assert.equal(spans[3].attributes["browser.web_vital.lcp.value"], 4732);
assert.equal(spans[3].attributes["browser.web_vital.lcp.element"], "img");
assert.equal(spans[4].attributes["browser.web_vital.inp.target"], "button.save");
assert.equal(spans[5].attributes["browser.web_vital.cls.source.1"], "body > div#root > img");
assert.equal(spans[6].name, "/o/:slug/m/:brand/bibliotheque");
assert.equal(spans[6].attributes["url.full"], "https://nyra.test/o/:slug/m/:brand/bibliotheque");
assert.equal(spans[7].attributes["code.file.path"], "[external]");
assert.equal(spans[7].attributes["browser.script.invoker"], "https://nyra.test/assets/index-abc12345.js");
assert.doesNotThrow(() => scrub.scrubSpan({ name: "no attributes" }, scope));
assert.doesNotThrow(() => scrub.scrubSpan({}, scope));

const crumbs = [
  { category: "console", message: "toast: bouteille-secrete-2026.jpg" },
  { category: "ui.click", message: 'img[alt="Louis XIII"]' },
  { category: "fetch", data: { url: `${api}/bouteille-secrete-2026.jpg/url?x=1`, method: "GET", status_code: 400 } },
  { category: "fetch", data: { url: `${SITE}/x`, request_body_size: 12 } },
  { category: "navigation", data: { from: "/o/remy-martin/m/louis-xiii/bibliotheque", to: "/o/remy-martin/m/louis-xiii/reglages" } },
].map((crumb) => scrub.scrubBreadcrumb(crumb, scope));
assert.deepEqual(crumbs.map(Boolean), [false, false, true, true, true]);
assert.equal(crumbs[2].data.url, "/api/orgs/:id/brands/:id/library/:file/url?x");
assert.equal(crumbs[2].data.status_code, 400);
assert.deepEqual(crumbs[3].data, { url: "[external]" }); // the size of a body is dropped with the rest of what the key says is a body
assert.deepEqual(crumbs[4].data, { from: "/o/:slug/m/:brand/bibliotheque", to: "/o/:slug/m/:brand/reglages" });

const live = { url: `${SITE}/x`, method: "GET" }; // a failed fetch lends the SDK's own object: it must come back untouched
scrub.scrubBreadcrumb({ category: "fetch", data: live }, scope);
assert.deepEqual(live, { url: `${SITE}/x`, method: "GET" });

const event = (values) => ({
  message: "bouteille-secrete-2026.jpg",
  user: { id: ORG, email: "a@x.com" },
  extra: { filename: "bouteille-secrete-2026.jpg" },
  transaction: "/o/remy-martin/m/louis-xiii/bibliotheque",
  request: { url: "https://nyra.test/o/remy-martin/m/louis-xiii/bibliotheque#access_token=abc", headers: { Referer: "https://nyra.test/o/remy-martin" }, cookies: { a: "b" } },
  contexts: { trace: { description: "/o/remy-martin/m/louis-xiii/x", data: { "url.full": `${SITE}/a.png`, "lcp.element": 'img[alt="x"]' } } },
  exception: { values },
  breadcrumbs: [{ category: "console", message: "Remy Martin" }, { category: "xhr", data: { url: `${api}/x.jpg` } }],
});
// An ApiError anywhere in the cause chain (the caller found it): every value is reduced to the status.
const chain = scrub.scrubEvent(event([{ type: "Error", value: "Échec pour Remy Martin" }, { type: "Error", value: "Le fichier bouteille-secrete-2026.jpg existe déjà (Louis XIII)" }]), scope, 400);
assert.deepEqual(chain.exception.values, [{ type: "ApiError", value: "HTTP 400" }, { type: "ApiError", value: "HTTP 400" }]);
assert.equal(chain.user, undefined);
assert.equal(chain.request.headers, undefined);
assert.equal(chain.transaction, "/o/:slug/m/:brand/bibliotheque");
assert.equal(chain.breadcrumbs.length, 1);
assert.equal(chain.contexts.trace.data["url.full"], "[external]");
assert.equal(chain.contexts.trace.description, "/o/:slug/m/:brand/x");

const other = scrub.scrubEvent(event([{ type: "Remy Martin Error", value: "x is undefined in Louis XIII.jpg" }, { type: "TypeError", value: "Failed to fetch (www.remymartin-secret-site.com)" }]), scope);
assert.deepEqual(other.exception.values, [{ type: "[name] Error", value: "[text naming a file]" }, { type: "TypeError", value: "Failed to fetch ([external])" }]);

const everything = JSON.stringify([spans, crumbs, chain, other]);
for (const word of PRIVATE) assert.ok(!everything.includes(word), `"${word}" survived scrubbing: ${everything}`);
console.log("scrub: every sample passes, none of the client data survives");
