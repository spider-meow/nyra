// Checks what the Actions menu of a selection says about it (src/pages/library/selectionSummary.ts): how many visuals already have a
// date, how many tags and collections they hold, and the one-line hints built from them. No test framework: the file is compiled in
// memory. Run: `npm run check:selection`.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { transformWithOxc } from "vite";

const source = readFileSync(new URL("../src/pages/library/selectionSummary.ts", import.meta.url), "utf8");
const { code } = await transformWithOxc(source, "source.ts", { target: "es2022" });
const { summarize, dateHint, whereHint, creditHint } = await import(`data:text/javascript;base64,${Buffer.from(code).toString("base64")}`);

const item = (over) => ({ expiry_date: "", unlimited_rights: false, credit: "", tags: [], collection_id: null, ...over });

// --- counts ---------------------------------------------------------------------------------------------------------
const items = [
  item({ expiry_date: "2027-01-01", credit: "Studio", tags: ["a", "b"], collection_id: "c1" }),
  item({ expiry_date: "2027-02-01", tags: ["b"], collection_id: "c1" }),
  item({ unlimited_rights: true, collection_id: "c2" }),
  item({}),
];
assert.deepEqual(summarize(items), { count: 4, withDate: 2, withCredit: 1, unlimited: 1, tags: 2, unclassified: 1, collections: 2 });
assert.deepEqual(summarize([]), { count: 0, withDate: 0, withCredit: 0, unlimited: 0, tags: 0, unclassified: 0, collections: 0 });

// --- the hints ------------------------------------------------------------------------------------------------------
assert.equal(dateHint(17, 12), "12 sur 17 ont déjà une date");
assert.equal(dateHint(17, 0), "Aucun n'a de date");
assert.equal(dateHint(17, 17), "Tous ont déjà une date");
assert.equal(dateHint(1, 0), "Il n'a pas de date");
assert.equal(dateHint(1, 1), "Il a déjà une date");
assert.equal(creditHint(17, 0), "Aucun n'a de crédit");
assert.equal(creditHint(17, 17), "Tous ont déjà un crédit");
assert.equal(creditHint(17, 3), "3 sur 17 ont déjà un crédit");
assert.equal(creditHint(1, 1), "Il a déjà un crédit");
assert.equal(whereHint(5, 0), "Aucun n'est classé");
assert.equal(whereHint(0, 1), "Aujourd'hui dans 1 collection");
assert.equal(whereHint(0, 3), "Aujourd'hui dans 3 collections");
assert.equal(whereHint(1, 2), "2 collections, 1 non classé");
assert.equal(whereHint(4, 1), "1 collection, 4 non classés");

console.log("selection: counts and hints of the Actions menu are right");
