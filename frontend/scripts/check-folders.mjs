// Checks the collection picker of « Classer » (src/pages/library/folderSearch.ts): the tree is listed parent first with its level and path,
// the search finds a collection or a sub-collection by every word typed (case and accents ignored, any order, in the whole path), and
// the highlight marks exactly what was found. No test framework: vite compiles the files in memory. Run: `npm run check:folders`.
import assert from "node:assert/strict";
import { loadTs } from "./load-ts.mjs";

const { collectionIndex } = await loadTs("/src/pages/library/collections.ts");
const { folderRows, searchFolders, highlight } = await loadTs("/src/pages/library/folderSearch.ts");

const c = (id, name, parent_id = null) => ({ id, name, parent_id });
const index = collectionIndex([
  c("a", "ACTIVATION"), c("a1", "Summer", "a"), c("a1x", "Summer 2026", "a1"), c("a2", "Hiver", "a"), c("b", "PRODUCTS"), c("b1", "Été", "b"), c("b1x", "Summer", "b1"),
]);

// The tree, parents before children, with level and path.
const rows = folderRows(index);
assert.deepEqual(rows.map((row) => [row.id, row.depth]), [["a", 0], ["a1", 1], ["a1x", 2], ["a2", 1], ["b", 0], ["b1", 1], ["b1x", 2]]);
assert.equal(rows[2].path, "ACTIVATION › Summer › Summer 2026");
assert.equal(rows[2].parent, "ACTIVATION › Summer");
assert.equal(rows[0].parent, "", "a top-level collection has nothing above it");

// Search: nothing typed shows all; every word, case and accents ignored, any order, in the whole path.
const ids = (query) => searchFolders(rows, query).map((row) => row.id);
assert.equal(ids("").length, 7);
assert.equal(ids("   ").length, 7);
assert.deepEqual(ids("summer"), ["a1", "a1x", "b1x"], "a name found anywhere in the path, sub-collections of a match included");
assert.deepEqual(ids("SUMMER 2026"), ["a1x"], "every word, case ignored");
assert.deepEqual(ids("2026 summer"), ["a1x"], "any order");
assert.deepEqual(ids("ete"), ["b1", "b1x"], "accents ignored");
assert.deepEqual(ids("activation hiver"), ["a2"], "a word of the parent and a word of the name");
assert.deepEqual(ids("zzz"), [], "nothing found");

// Highlight: exactly what was found, in the original spelling.
const joined = (pieces) => pieces.map((piece) => (piece.hit ? `[${piece.text}]` : piece.text)).join("");
assert.equal(joined(highlight("Summer 2026", "summer")), "[Summer] 2026");
assert.equal(joined(highlight("Summer 2026", "SUM 26")), "[Sum]mer 20[26]");
assert.equal(joined(highlight("Été d'Été", "ete")), "[Été] d'[Été]", "accents ignored, the text is shown as it was written");
assert.equal(joined(highlight("Summer", "")), "Summer", "nothing typed marks nothing");
assert.equal(joined(highlight("Summer", "zzz")), "Summer");
assert.equal(highlight("Summer", "summer").map((piece) => piece.text).join(""), "Summer", "the pieces always give the text back");
assert.equal(joined(highlight("aaaa", "aa")), "[aaaa]", "two finds side by side are one piece");

// A large library stays instant: 600 collections searched in well under a frame.
const many = Array.from({ length: 200 }, (_, i) => [c(`t${i}`, `Top ${i}`), c(`m${i}`, `Middle ${i} été`, `t${i}`), c(`l${i}`, `Leaf ${i}`, `m${i}`)]).flat();
const big = folderRows(collectionIndex(many));
const started = performance.now();
const found = searchFolders(big, "middle ete 7");
assert.ok(found.length > 0 && performance.now() - started < 100, "a search on 600 collections is instant");

console.log("folders: tree order, search without accents or case, highlight, and a large library are right");
