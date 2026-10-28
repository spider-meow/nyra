// Checks the logic of ordering the collections by hand (src/lib/order.ts): where a dragged row lands, what the keyboard does at the
// ends of the list, and that the order shown at once keeps the other levels as they were. No test framework: the file is
// compiled in memory. Run: `npm run check:order`.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { transformWithOxc } from "vite";

const source = readFileSync(new URL("../src/lib/order.ts", import.meta.url), "utf8");
const { code } = await transformWithOxc(source, "source.ts", { target: "es2022" });
const { moved, slotAt, landing, withOrder } = await import(`data:text/javascript;base64,${Buffer.from(code).toString("base64")}`);

// --- moved: the row ends at the index asked, the others keep their order; the ends of the list stop it ----------------------
const ids = ["a", "b", "c", "d"];
assert.deepEqual(moved(ids, 0, 2), ["b", "c", "a", "d"]);
assert.deepEqual(moved(ids, 3, 0), ["d", "a", "b", "c"]);
assert.deepEqual(moved(ids, 1, 1), ids, "to its own place: nothing moves");
assert.deepEqual(moved(ids, 0, -1), ids, "up from the top stays at the top");
assert.deepEqual(moved(ids, 3, 4), ids, "down from the bottom stays at the bottom");
assert.deepEqual(moved(ids, 9, 0), ids, "a row that is not there moves nothing");
assert.deepEqual(ids, ["a", "b", "c", "d"], "the list given is never changed");

// --- slotAt and landing: four rows of 40 px stacked from y = 0 -------------------------------------------------------
const rows = [0, 1, 2, 3].map((n) => ({ top: n * 40, bottom: n * 40 + 40 }));
assert.equal(slotAt(rows, -50), 0);
assert.equal(slotAt(rows, 19), 0, "above the middle of the first row: before it");
assert.equal(slotAt(rows, 21), 1, "below the middle of the first row: after it");
assert.equal(slotAt(rows, 79), 2);
assert.equal(slotAt(rows, 500), 4, "below everything: after the last one");
assert.equal(slotAt([], 10), 0);
// dragging row 1 ("b"): dropping in slot 1 or 2 (right before or after itself) is no move, slot 4 puts it last, slot 0 first
const result = (from, slot) => moved(ids, from, landing(slot, from));
assert.deepEqual(result(1, 1), ids);
assert.deepEqual(result(1, 2), ids);
assert.deepEqual(result(1, 4), ["a", "c", "d", "b"]);
assert.deepEqual(result(1, 0), ["b", "a", "c", "d"]);
assert.deepEqual(result(3, 1), ["a", "d", "b", "c"]);

// --- withOrder: one level reordered at once, the others untouched ----------------------------------------------------------
const c = (id, parent_id) => ({ id, parent_id, name: id });
const all = [c("x", null), c("y", null), c("x1", "x"), c("x2", "x"), c("y1", "y")];
assert.deepEqual(withOrder(all, null, ["y", "x"]).filter((item) => item.parent_id === null).map((item) => item.id), ["y", "x"]);
assert.deepEqual(withOrder(all, "x", ["x2", "x1"]).filter((item) => item.parent_id === "x").map((item) => item.id), ["x2", "x1"]);
assert.equal(withOrder(all, "x", ["x2", "x1"]).length, 5, "nothing is lost");
const odd = withOrder(all, "x", ["x2", "y1", "nope"]).map((item) => item.id);
assert.deepEqual(odd.filter((id) => id.startsWith("x") && id.length > 1), ["x2", "x1"], "an id of another level or unknown is ignored, a child left out follows");
assert.equal(odd.length, 5);
console.log("order: moves, drop slots and the optimistic order are right");
