// Checks the logic of dropping files on the page (src/lib/dropzone.ts): only drags that carry files count, the same file
// dropped twice is kept once, and the drag depth goes back to 0 whatever the order of the browser's enter/leave events
// (it fires `dragenter` on every element crossed and `dragleave` on the one left). No test framework: the file is compiled
// in memory. Run: `npm run check:drop`.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { transformWithOxc } from "vite";

const source = readFileSync(new URL("../src/lib/dropzone.ts", import.meta.url), "utf8");
const { code } = await transformWithOxc(source, "source.ts", { target: "es2022" });
const { carriesFiles, mergeFiles, dragDepth } = await import(`data:text/javascript;base64,${Buffer.from(code).toString("base64")}`);

// --- only files light the drop zone -----------------------------------------------------------------------------------
assert.equal(carriesFiles(["Files"]), true);
assert.equal(carriesFiles(["text/plain", "Files"]), true);
for (const types of [["text/uri-list", "text/plain"], ["text/html"], [], undefined]) assert.equal(carriesFiles(types), false, String(types));

// --- the same file twice is kept once; a file of the same name but another size is another file ----------------------
const file = (name, size, lastModified = 1) => ({ name, size, lastModified });
const a = file("a.png", 10);
assert.deepEqual(mergeFiles([a], [file("a.png", 10), file("b.png", 5)]).map((f) => f.name), ["a.png", "b.png"]);
assert.equal(mergeFiles([a], [file("a.png", 11)]).length, 2);
assert.equal(mergeFiles([], [a, file("a.png", 10)]).length, 1, "twice in the same drop");
assert.equal(mergeFiles([a], []).length, 1);

// --- the depth: into a child (enter child, leave parent) never reads as « left »; a drop or the last leave resets it ---
let depth = 0;
for (const event of ["enter", "enter", "leave", "enter", "leave", "leave"]) depth = dragDepth(depth, event);
assert.equal(depth, 0);
depth = dragDepth(dragDepth(0, "enter"), "enter");
assert.equal(dragDepth(depth, "drop"), 0);
assert.equal(dragDepth(0, "leave"), 0, "a stray leave never goes below 0");
assert.equal(dragDepth(dragDepth(0, "enter"), "leave"), 0);

console.log("drop: only file drags count, no duplicate kept, the drag depth always returns to 0");
