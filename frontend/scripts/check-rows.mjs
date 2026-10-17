// Checks how "À traiter" groups matches by photo (src/pages/review/rows.ts): several references that match one
// photo share one row, and a decision filter or a repeated reference never makes a row list itself.
// No test framework: the file is compiled in memory. Run: `npm run check:rows`.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

const source = readFileSync(new URL("../src/pages/review/rows.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } });
const { photoRows, focusOn } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);

// A photo is named by its key, whatever the files or the order of their ids.
const hit = (ids, decision = null) => ({ site_image_id: ids[0], site_image_ids: ids, photo_key: [...ids].sort()[0], decision });
const grp = (id, hits) => ({ reference_id: id, filename: id + ".jpg", hits });
// refs A, B, C all match photo P (two copies p1,p2); A and C also match Q; B alone matches R.
const groups = [
  grp("A", [hit(["p1", "p2"]), hit(["q1"])]),
  grp("B", [hit(["p2", "p1"]), hit(["r1"])]),   // same photo, ids listed in another order
  grp("C", [hit(["p1", "p2"]), hit(["q1"], "ecarte")]),
];
let rows = photoRows(groups, () => true);
assert.equal(rows.length, 3, "three photos, not seven pairs");
const p = rows.find((r) => r.key === "p1");
assert.equal(p.group.reference_id, "A");
assert.deepEqual(p.others.map((o) => o.group.reference_id), ["B", "C"]);
assert.deepEqual(rows.find((r) => r.key === "q1").others.map((o) => o.group.reference_id), ["C"]);
assert.equal(rows.find((r) => r.key === "r1").others.length, 0);
// a decision filter that hides C's decided pair: Q keeps only A
rows = photoRows(groups, (h) => h.decision === null);
assert.deepEqual(rows.find((r) => r.key === "q1").others, []);
// the same reference twice on one photo never lists itself
const twice = photoRows([grp("A", [hit(["x1"]), hit(["x1"])])], () => true);
assert.equal(twice[0].others.length, 0);
// focusOn: another reference first, the rest as others, same key; unknown id falls back to the first
const f = focusOn(p, "C");
assert.equal(f.group.reference_id, "C"); assert.equal(f.key, "p1");
assert.deepEqual(f.others.map((o) => o.group.reference_id).sort(), ["A", "B"]);
assert.equal(focusOn(p, "nope").group.reference_id, "A");
assert.equal(focusOn(p, null).others.length, 2);
console.log("rows: grouping by photo ok");
