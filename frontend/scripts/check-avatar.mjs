// Checks the avatars of the team page (src/lib/avatar.ts): the same id always gives the same drawing, whatever the order or the number of
// times it is asked; the 15 families are all reachable; a drawing is well formed; and three fixed ids keep the drawing they were validated with
// (if this fails, everyone's avatar changed: the generator, the order of the families or their names were touched).
// No test framework: the file is compiled in memory. Run: `npm run check:avatar`.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { transformWithOxc } from "vite";

const source = readFileSync(new URL("../src/lib/avatar.ts", import.meta.url), "utf8");
const { code } = await transformWithOxc(source, "avatar.ts", { target: "es2022" });
const { motif, avatarSrc, rng, FAMILIES } = await import(`data:text/javascript;base64,${Buffer.from(code).toString("base64")}`);

const id = (n) => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const ids = Array.from({ length: 3000 }, (_, n) => id(n));

// Same id, same drawing; a different id, in general a different one.
const first = ids.map(motif);
assert.deepEqual([...ids].reverse().map(motif).reverse(), first, "the drawing depends on the id only");
assert.equal(motif(ids[7]), first[7]);
assert.ok(new Set(first).size > 2900, "two ids rarely give one drawing");

// Every family is reached (about 200 people each out of 3000), and every drawing is plain SVG with no NaN, no undefined, no script.
assert.equal(FAMILIES.length, 15);
const count = Object.fromEntries(FAMILIES.map(([name]) => [name, 0]));
ids.forEach((who, n) => {
  const [name] = FAMILIES.find(([name, draw]) => draw(rng(`${who}|${name}`)) === first[n]);
  count[name]++;
});
for (const [name, people] of Object.entries(count)) assert.ok(people > 120 && people < 300, `${name}: ${people} people out of 3000`);
for (const drawing of first) assert.ok(drawing.startsWith("<rect") && !/NaN|undefined|Infinity|<script/i.test(drawing));

// An address a browser can show: a data URI, the SVG escaped, the id nowhere in it.
const src = avatarSrc(ids[0]);
assert.ok(src.startsWith("data:image/svg+xml,%3Csvg"), "an SVG image address");
assert.ok(!src.includes(ids[0]) && !decodeURIComponent(src).includes("<script"));

// Fixed ids keep their drawing (validated on 3 November 2026).
const golden = { [id(1)]: "c66a726b45a8", [id(2)]: "c605bc11b96b", [id(3)]: "819640e1eb02" };
for (const [who, hash] of Object.entries(golden)) assert.equal(createHash("sha1").update(motif(who)).digest("hex").slice(0, 12), hash, `the avatar of ${who} changed`);

console.log("avatar: same id, same drawing; 15 families; well formed; the validated drawings did not move");
