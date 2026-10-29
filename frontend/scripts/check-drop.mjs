// Checks the logic of dropping files on the page (src/lib/dropzone.ts): only drags that carry files count, the same file
// dropped twice is kept once, and the drag depth goes back to 0 whatever the order of the browser's enter/leave events
// (it fires `dragenter` on every element crossed and `dragleave` on the one left). No test framework: the file is compiled
// in memory. Run: `npm run check:drop`.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { transformWithOxc } from "vite";

const source = readFileSync(new URL("../src/lib/dropzone.ts", import.meta.url), "utf8");
const { code } = await transformWithOxc(source, "source.ts", { target: "es2022" });
const { carriesFiles, mergeFiles, dragDepth, splitImages, rejectionNotice } = await import(`data:text/javascript;base64,${Buffer.from(code).toString("base64")}`);

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

// --- only images enter the waiting list (AXE-628): a mixed drop keeps its photos, the rest is named ---------------------
const named = (name, type = "") => ({ name, type });
const names = (files) => files.map((f) => f.name);
let split = splitImages([named("film.mp4", "video/mp4")]);
assert.deepEqual([names(split.images), names(split.rejected)], [[], ["film.mp4"]], "a video alone: nothing enters");
split = splitImages([named("a.jpg", "image/jpeg"), named("film.mov", "video/quicktime"), named("b.png", "image/png"), named("clip.mp4")]);
assert.deepEqual([names(split.images), names(split.rejected)], [["a.jpg", "b.png"], ["film.mov", "clip.mp4"]], "a mixed drop: the photos enter, the videos stay out");
split = splitImages([named("scan.tif"), named("PHOTO.JPG"), named("old.BMP", "image/bmp"), named("clip.MP4"), named("notes.pdf", "application/pdf"), named("noext")]);
assert.deepEqual(names(split.images), ["scan.tif", "PHOTO.JPG", "old.BMP"], "no type: the extension decides, in any case");
assert.deepEqual(names(split.rejected), ["clip.MP4", "notes.pdf", "noext"]);
assert.equal(splitImages([named("fake.jpg", "video/mp4")]).images.length, 0, "a video type refuses even an image extension");
assert.equal(splitImages([named("photo.heic", "image/heic")]).images.length, 0, "a format the server does not take");
const notice = rejectionNotice([named("a.mp4", "video/mp4"), named("b.mov"), named("c.pdf", "application/pdf"), named("d.mkv")]);
assert.equal(notice.message, "4 fichiers non pris en charge");
assert.match(notice.description, /Écarté : a\.mp4, b\.mov, c\.pdf et 1 autre\(s\)\./);
assert.equal(rejectionNotice([named("a.mp4"), named("b.MOV")]).message, "2 vidéos non prises en charge");
assert.equal(rejectionNotice([named("a.mp4")]).message, "1 vidéo non prise en charge");

// --- the weight a file may have before it is sent (AXE-630): a TIFF may weigh more, the others keep the lower limit -----------------
const limitsSource = readFileSync(new URL("../src/lib/uploadLimits.ts", import.meta.url), "utf8");
const limits = await import(`data:text/javascript;base64,${Buffer.from((await transformWithOxc(limitsSource, "limits.ts", { target: "es2022" })).code).toString("base64")}`);
assert.deepEqual([limits.MAX_BYTES, limits.MAX_TIFF_BYTES], [30_000_000, 100_000_000], "the server's limits (routes/common.py)");
assert.equal(limits.megabytes(45_300_000), "45,3 Mo");
assert.equal(limits.heavyReason({ name: "photo.jpg", size: 30_000_000 }), null, "exactly the limit goes");
assert.equal(limits.heavyReason({ name: "scan.TIF", size: 72_000_000 }), null, "a 72 MB TIFF goes");
assert.equal(limits.heavyReason({ name: "scan.tiff", size: 100_000_000 }), null);
assert.equal(limits.heavyReason({ name: "photo.png", size: 30_000_001 }), "Fichier trop lourd : 30,0 Mo, 30,0 Mo au plus.");
assert.equal(limits.heavyReason({ name: "photo.jpg", size: 45_300_000 }), "Fichier trop lourd : 45,3 Mo, 30,0 Mo au plus.");
assert.equal(limits.heavyReason({ name: "Scan.Tif", size: 130_000_000 }), "Fichier trop lourd : 130,0 Mo, 100,0 Mo au plus pour un TIFF. Exportez-le en JPEG ou réduisez-le.");
assert.notEqual(limits.heavyReason({ name: "tif.jpg", size: 40_000_000 }), null, "a name that only contains tif is not a TIFF");

console.log("drop: only file drags count, no duplicate kept, the drag depth always returns to 0, only images enter, the weight limits match the server's");
