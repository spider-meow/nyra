// Checks that the e-mail preview can run nothing: the <iframe> of the back office's E-mails page has `sandbox=""` (no script, no
// popup, no navigation), and no file of src/ writes HTML into the page (`dangerouslySetInnerHTML`, `innerHTML`).
// A text check on the sources, no framework. Run: `npm run check:emails`.
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";

const src = new URL("../src/", import.meta.url);
const page = readFileSync(new URL("pages/backoffice/Emails.tsx", src), "utf8");
const frames = page.match(/<iframe\b[^>]*>/g) ?? [];
assert.ok(frames.length > 0, "Emails.tsx has no <iframe>: the preview moved, update this check");
for (const frame of frames) {
  assert.match(frame, /\ssandbox=""/, `an <iframe> of Emails.tsx has no sandbox="": ${frame}`);
  assert.doesNotMatch(frame, /allow-/, `an <iframe> of Emails.tsx opens a permission of the sandbox: ${frame}`);
}

const sources = (dir) => readdirSync(dir, { withFileTypes: true }).flatMap((entry) =>
  entry.isDirectory() ? sources(new URL(`${entry.name}/`, dir)) : /\.tsx?$/.test(entry.name) ? [new URL(entry.name, dir)] : []);
const writers = sources(src).filter((file) => /dangerouslySetInnerHTML|\.innerHTML\b|\.outerHTML\b/.test(readFileSync(file, "utf8")));
assert.deepEqual(writers.map((file) => file.pathname), [], "HTML is written into the page by these files");

console.log("emails: the preview iframe is sandboxed with no permission, no source writes HTML into the page");
