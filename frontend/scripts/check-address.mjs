// Checks the address of the library (src/pages/library/address.ts) and the page a visitor returns to after signing in (src/lib/paths.ts, `returnPath`):
// which folder and which visual an address names, that the other parameters survive a change, that a link to a visual holds an identifier and nothing
// else, and that no address can send a signed-in visitor to another site. No test framework: the files are compiled in memory.
// Run: `npm run check:address`.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { transformWithOxc } from "vite";

async function load(path) {
  const { code } = await transformWithOxc(readFileSync(new URL(path, import.meta.url), "utf8"), "source.ts", { target: "es2022" });
  return import(`data:text/javascript;base64,${Buffer.from(code).toString("base64")}`);
}
const { readAddress, writeAddress, visualLink } = await load("../src/pages/library/address.ts");
const { returnPath } = await load("../src/lib/paths.ts");

const ID = "3f0c9d3a-4b8e-4f6a-9a51-0c2d7e5b1a42";
const OTHER = "9b1e2c40-7d3f-41aa-8f0e-5a6b7c8d9e01";
const params = (text) => new URLSearchParams(text);

// --- reading ---------------------------------------------------------------------------------------------------------------------
assert.deepEqual(readAddress(params("")), { scope: "all", visual: null }, "nothing in the address: the whole library, no sheet");
assert.deepEqual(readAddress(params(`collection=${ID}`)), { scope: ID, visual: null });
assert.deepEqual(readAddress(params("collection=none")), { scope: "none", visual: null }, "the unfiled visuals");
assert.deepEqual(readAddress(params(`collection=${ID}&visual=${OTHER}`)), { scope: ID, visual: OTHER });
assert.deepEqual(readAddress(params("collection=")), { scope: "all", visual: null }, "an empty value is no value");
assert.deepEqual(readAddress(params(`collection=${"x".repeat(65)}&visual=${"y".repeat(65)}`)), { scope: "all", visual: null }, "too long to be an identifier");
assert.equal(readAddress(params("collection=unknown-thing")).scope, "unknown-thing", "a value that points at nothing is kept: the page says that collection is gone");

// --- writing: one parameter at a time, the others kept, the input never changed -------------------------------------------------------
const base = params(`visual=${OTHER}&q=x`);
assert.equal(writeAddress(base, { scope: ID }).toString(), `visual=${OTHER}&q=x&collection=${ID}`);
assert.equal(writeAddress(params(`collection=${ID}&q=x`), { scope: "all" }).toString(), "q=x", "all: the parameter leaves the address");
assert.equal(writeAddress(params(`collection=${ID}&visual=${OTHER}`), { visual: null }).toString(), `collection=${ID}`, "closing a sheet keeps the folder");
assert.equal(writeAddress(params(`collection=${ID}`), { scope: "none" }).toString(), "collection=none");
assert.equal(writeAddress(base, {}).toString(), base.toString(), "no change: same address");
assert.equal(base.toString(), `visual=${OTHER}&q=x`, "the parameters given are never changed");
assert.deepEqual(readAddress(writeAddress(params(""), { scope: ID, visual: OTHER })), { scope: ID, visual: OTHER }, "what is written is read back");

// --- the link to a visual: the path of the library and an identifier, nothing else -----------------------------------------------------
const link = visualLink("https://nyra.example", "/o/remy-martin/b/louis-xiii/library", ID);
assert.equal(link, `https://nyra.example/o/remy-martin/b/louis-xiii/library?visual=${ID}`);
assert.ok(!/filename|name|token|signature|\.jpg/i.test(new URL(link).search), "no file name, no signed address in a link");

// --- after sign-in: only a page of an organization of this app --------------------------------------------------------------------------
for (const good of ["/o/remy-martin", "/o/remy-martin/b/louis-xiii/library", `/o/remy-martin/b/louis-xiii/library?collection=${ID}`, `/o/x/b/y/library?visual=${ID}&collection=none`]) {
  assert.equal(returnPath(good), good, good);
}
for (const bad of [null, undefined, 42, "", "/", "/login", "/back-office", "//evil.test", "/o//evil.test", "https://evil.test/o/x", "/\\evil.test", "/o/x/\\evil", "/o/x y", "o/x/b/y/library", `/o/x/${"a".repeat(700)}`, "javascript:alert(1)", "/o/x/\nSet-Cookie"]) {
  assert.equal(returnPath(bad), null, String(bad));
}
console.log("check:address ok");
