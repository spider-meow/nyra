// Runs the browser-monitoring scrubbers (src/lib/scrub.ts) on sample client data and fails if any of it
// survives. No test framework: the file is compiled in memory with the TypeScript compiler already in
// devDependencies. Run: `npm run check:scrub`.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

const source = readFileSync(new URL("../src/lib/scrub.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } });
const scrub = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);

const ORG = "11111111-1111-1111-1111-111111111111";
const BRAND = "22222222-2222-2222-2222-222222222222";
const scope = {
  origin: "https://nyra.test",
  routeOf: (path) => path.replace(/^\/o\/[^/]+\/m\/[^/]+/, "/o/:slug/m/:brand"),
};
const PRIVATE = ["bouteille-secrete", "remy-martin", "louis-xiii", "Remy Martin", "Louis XIII", "a@x.com", ORG, BRAND, ".jpg", "token=", "#access"];

const api = `/api/orgs/${ORG}/brands/${BRAND}/library`;
assert.equal(scrub.scrubUrl(`${api}/bouteille-secrete-2026.jpg/url?within=90&a=a@x.com`, scope), "https://nyra.test/api/orgs/:id/brands/:id/library/:file/url?within&a");
assert.equal(scrub.scrubUrl(`${api}/thumbs`, scope), "https://nyra.test/api/orgs/:id/brands/:id/library/thumbs");
assert.equal(scrub.scrubUrl(`https://x.supabase.co/storage/v1/sign/refs/${ORG}/bouteille-secrete.jpg?token=abc`, scope), "https://x.supabase.co");
assert.equal(scrub.scrubUrl("https://nyra.test/o/remy-martin/m/louis-xiii/bibliotheque?q=a@x.com#access_token=abc", scope), "https://nyra.test/o/:slug/m/:brand/bibliotheque");
assert.equal(scrub.scrubUrl("https://nyra.test/assets/index-abc.js", scope), "https://nyra.test/assets/index-abc.js");
assert.equal(scrub.scrubUrl("blob:https://nyra.test/" + ORG, scope), "[url]");

assert.equal(scrub.scrubText("GET " + api + "/bouteille-secrete-2026.jpg/url", scope), "GET /api/orgs/:id/brands/:id/library/:file/url");
assert.equal(scrub.scrubText(`Trop gros pour a@x.com (${ORG})`, scope), "Trop gros pour [email] (:id)");
assert.equal(scrub.scrubText(`Impossible de lire bouteille-secrete-2026.jpg pour a@x.com (${ORG})`, scope), "[text naming a file]");
assert.equal(scrub.scrubText('body > div#root > img.thumb[alt="bouteille-secrete"][aria-label="Louis XIII"]', scope), "body > div#root > img.thumb");

const crumbs = [
  { category: "console", message: "toast: bouteille-secrete-2026.jpg" },
  { category: "ui.click", message: 'img[alt="Louis XIII"]' },
  { category: "fetch", data: { url: `${api}/bouteille-secrete-2026.jpg/url?x=1`, method: "GET", status_code: 400 } },
  { category: "navigation", data: { from: "/o/remy-martin/m/louis-xiii/bibliotheque", to: "/o/remy-martin/m/louis-xiii/reglages" } },
].map((crumb) => scrub.scrubBreadcrumb(crumb, scope));
assert.deepEqual(crumbs.map(Boolean), [false, false, true, true]);
assert.equal(crumbs[2].data.url, "/api/orgs/:id/brands/:id/library/:file/url?x");
assert.equal(crumbs[2].data.status_code, 400);
assert.deepEqual(crumbs[3].data, { from: "/o/:slug/m/:brand/bibliotheque", to: "/o/:slug/m/:brand/reglages" });

const error = scrub.scrubEvent({
  message: "bouteille-secrete-2026.jpg",
  user: { id: ORG, email: "a@x.com" },
  extra: { filename: "bouteille-secrete-2026.jpg" },
  transaction: "/o/remy-martin/m/louis-xiii/bibliotheque",
  request: { url: "https://nyra.test/o/remy-martin/m/louis-xiii/bibliotheque#access_token=abc", headers: { Referer: "https://nyra.test/o/remy-martin" }, cookies: { a: "b" } },
  exception: { values: [{ type: "Error", value: "Le fichier bouteille-secrete-2026.jpg existe déjà (Louis XIII)" }] },
  breadcrumbs: [{ category: "console", message: "Remy Martin" }, { category: "xhr", data: { url: `${api}/x.jpg` } }],
}, scope, 400);
assert.deepEqual(error.exception.values, [{ type: "ApiError", value: "HTTP 400" }]);
assert.equal(error.user, undefined);
assert.equal(error.request.headers, undefined);
assert.equal(error.transaction, "/o/:slug/m/:brand/bibliotheque");
assert.equal(error.breadcrumbs.length, 1);

const other = scrub.scrubEvent({ exception: { values: [{ type: "TypeError", value: "x is undefined in Louis XIII.jpg" }] } }, scope);
assert.equal(other.exception.values[0].value, "[text naming a file]");

const transaction = scrub.scrubTransaction({
  type: "transaction",
  transaction: "/o/remy-martin/m/louis-xiii/bibliotheque",
  user: { email: "a@x.com" },
  contexts: { trace: { description: "/o/remy-martin/m/louis-xiii/bibliotheque", data: { "url.full": "https://nyra.test/o/remy-martin/m/louis-xiii/bibliotheque?x=1", "lcp.url": "https://x.supabase.co/storage/bouteille-secrete.jpg?token=abc", "lcp.element": 'img[alt="bouteille-secrete"]', "url.query": "?q=a@x.com", "http.response_content_length": 12 } } },
  spans: [{ description: `GET ${api}/bouteille-secrete-2026.jpg/url`, data: { "url.full": `https://nyra.test${api}/bouteille-secrete-2026.jpg/url`, "http.query": "?a=1", "http.response.status_code": 200 } }],
}, scope);
assert.equal(transaction.contexts.trace.data["http.response_content_length"], 12);
assert.equal(transaction.contexts.trace.data["lcp.url"], "https://x.supabase.co");
assert.equal(transaction.spans[0].data["http.response.status_code"], 200);

const everything = JSON.stringify([crumbs, error, other, transaction]);
for (const text of PRIVATE) assert.ok(!everything.includes(text), `"${text}" survived scrubbing: ${everything}`);
console.log("scrub: every sample passes, none of the client data survives");
