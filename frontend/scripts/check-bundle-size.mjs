// Fails when the JavaScript the browser needs for the first screen grows past frontend/size-budget.json.
// First load = what dist/index.html loads up front: all its <script type="module"> and its modulepreload links.
// Every other .js file in dist/assets is lazy (a page opened later, the Sentry SDK). Sizes are gzip, 1 KB = 1000 bytes
// like Vite's build output. Run after `npm run build`: `npm run size`. See docs/DEVELOPMENT.md to change a budget.
// It also fails when the Sentry SDK is not a lazy `sentry-<hash>.js` chunk: a budget on a file that is not there proves nothing.
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { gzipSync } from "node:zlib";

const dist = new URL("../dist/", import.meta.url);
if (!existsSync(new URL("index.html", dist)) || !existsSync(new URL("assets/", dist))) {
  console.error("dist/ is missing or incomplete: run `npm run build` first.");
  process.exit(1);
}
const budget = JSON.parse(readFileSync(new URL("../size-budget.json", import.meta.url), "utf8"));

const kb = (bytes) => bytes / 1000;
const attribute = (tag, name) => new RegExp(`\\s${name}=(["'])(.*?)\\1`).exec(tag)?.[2]; // single or double quotes

/** The files index.html loads up front: every module script (the entry first) and every modulepreload link. */
function firstLoadFiles(html) {
  const tags = html.match(/<(?:script|link)\b[^>]*>/g) ?? [];
  const scripts = tags.filter((tag) => tag.startsWith("<script") && attribute(tag, "type") === "module").map((tag) => attribute(tag, "src"));
  if (!scripts.length) {
    console.error("dist/index.html has no <script type=\"module\">: run `npm run build` first.");
    process.exit(1);
  }
  const preloaded = tags.filter((tag) => attribute(tag, "rel") === "modulepreload").map((tag) => attribute(tag, "href"));
  return [...scripts, ...preloaded].map((url) => url.replace(/^\//, ""));
}

function measure(file) {
  const code = readFileSync(new URL(file, dist));
  return { file: file.replace("assets/", ""), raw: kb(code.length), gzip: kb(gzipSync(code).length) };
}

const html = readFileSync(new URL("index.html", dist), "utf8");
const firstLoad = firstLoadFiles(html).map(measure);
const firstLoadNames = new Set(firstLoad.map((chunk) => chunk.file));
const lazy = readdirSync(new URL("assets/", dist))
  .filter((name) => name.endsWith(".js") && !firstLoadNames.has(name))
  .map((name) => measure(`assets/${name}`));
const isSentry = (chunk) => /^sentry-[\w-]{8}\.js$/.test(chunk.file);
const sentry = lazy.filter(isSentry);
const otherLazy = lazy.filter((chunk) => !isSentry(chunk));
if (!sentry.length) {
  console.error("No lazy `sentry-<hash>.js` chunk in dist/assets: the Sentry SDK must be its own chunk, loaded after the page (src/lib/sentry.ts, imported only by src/lib/monitoring.ts), and it is either missing or loaded up front by index.html.");
  process.exit(1);
}

const sum = (chunks) => chunks.reduce((total, chunk) => total + chunk.gzip, 0);
const largest = (chunks) => chunks.reduce((top, chunk) => (chunk.gzip > top.gzip ? chunk : top), { file: "(none)", gzip: 0 });

// [what is measured, the chunk to blame, its gzip size, the budget]
const checks = [
  ["first-load JS (all chunks of index.html)", { file: firstLoad.map((chunk) => chunk.file).join(", ") }, sum(firstLoad), budget.firstLoadGzipKB],
  ["entry chunk", firstLoad[0], firstLoad[0].gzip, budget.entryGzipKB],
  ["largest lazy chunk (a page)", largest(otherLazy), largest(otherLazy).gzip, budget.lazyChunkGzipKB],
  ["Sentry chunk (lazy, off the critical path)", largest(sentry), largest(sentry).gzip, budget.sentryGzipKB],
];

const shareOf = (gzip, limit) => `${Math.round((gzip / limit) * 100)}%`;
console.log(`${"chunk".padEnd(34)}${"load".padEnd(8)}${"raw KB".padStart(9)}${"gzip KB".padStart(9)}${"of its budget".padStart(15)}`);
// A first-load chunk is a share of the first-load budget, the others of their own (a page, Sentry).
const rows = [
  ...firstLoad.map((chunk) => ["first", chunk, budget.firstLoadGzipKB]),
  ...otherLazy.map((chunk) => ["lazy", chunk, budget.lazyChunkGzipKB]),
  ...sentry.map((chunk) => ["lazy", chunk, budget.sentryGzipKB]),
];
for (const [load, chunk, limit] of rows) {
  console.log(`${chunk.file.padEnd(34)}${load.padEnd(8)}${chunk.raw.toFixed(1).padStart(9)}${chunk.gzip.toFixed(1).padStart(9)}${shareOf(chunk.gzip, limit).padStart(15)}`);
}
console.log();
for (const [label, , gzip, limit] of checks) {
  console.log(`${label}: ${gzip.toFixed(1)} / ${limit} KB gzip (${shareOf(gzip, limit)})`);
}

const failures = checks.filter(([, , gzip, limit]) => gzip > limit);
for (const [label, chunk, gzip, limit] of failures) {
  console.error(`\nOVER BUDGET: ${label}: ${gzip.toFixed(1)} KB gzip > ${limit} KB (${chunk.file}).`);
}
if (failures.length) {
  console.error("\nFind what grew (a new dependency? a page imported eagerly?). Raise a budget only on purpose: see docs/DEVELOPMENT.md.");
  process.exit(1);
}
