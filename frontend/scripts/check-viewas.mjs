// Checks the interface side of « Voir en tant que » (src/lib/viewAs.ts, AXE-634): which headers a request carries and which rights
// a screen gets under each role. The server side (real 403s, no elevation) is tested in backend/tests/test_isolation.py. No test
// framework: the file is compiled in memory. Run: `npm run check:viewas`.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { transformWithOxc } from "vite";

const source = readFileSync(new URL("../src/lib/viewAs.ts", import.meta.url), "utf8");
const { code } = await transformWithOxc(source, "source.ts", { target: "es2022" });
const { viewAsHeaders, rightsFor, parseViewAs, getViewAs, setViewAs, subscribeViewAs } = await import(`data:text/javascript;base64,${Buffer.from(code).toString("base64")}`);

const view = (role, write = false) => ({ orgId: "o1", role, write });

// --- headers: only the organization being viewed, and « write » only as a client ----------------------------------------
assert.deepEqual(viewAsHeaders(null, "/orgs/o1/brands/b/library"), {}, "as the Nyra team: nothing sent");
assert.deepEqual(viewAsHeaders(view("admin"), "/orgs/o1/usage"), { "X-Nyra-View-As": "admin" });
assert.deepEqual(viewAsHeaders(view("client"), "/orgs/o1/usage"), { "X-Nyra-View-As": "client" }, "read-only by default");
assert.deepEqual(viewAsHeaders(view("client", true), "/orgs/o1/usage"), { "X-Nyra-View-As": "client", "X-Nyra-View-Write": "1" });
assert.deepEqual(viewAsHeaders(view("admin", true), "/orgs/o1/usage"), { "X-Nyra-View-As": "admin" }, "« write » means nothing as an administrator");
for (const path of ["/orgs/o2/usage", "/orgs/o10/usage", "/orgs/o1", "/staff/users", "/me", "/orgs"]) assert.deepEqual(viewAsHeaders(view("client"), path), {}, path);

// --- rights: a chosen role drops the team's own settings; the Nyra team keeps everything -----------------------------
assert.deepEqual(rightsFor(null, true, "admin"), { admin: true, platformAdmin: true }, "the Nyra team");
assert.deepEqual(rightsFor(null, false, "client"), { admin: false, platformAdmin: false }, "a client");
assert.deepEqual(rightsFor(null, false, "admin"), { admin: true, platformAdmin: false }, "an administrator");
assert.deepEqual(rightsFor(view("admin"), true, "admin"), { admin: true, platformAdmin: false }, "as an administrator");
assert.deepEqual(rightsFor(view("client"), true, "admin"), { admin: false, platformAdmin: false }, "as a client");

// --- what is read back from the tab's storage must be well formed ---------------------------------------------------
assert.deepEqual(parseViewAs({ orgId: "o1", role: "client", write: true }), view("client", true));
for (const bad of [null, undefined, "client", {}, { orgId: "o1", role: "staff", write: false }, { orgId: 1, role: "admin", write: false }, { orgId: "o1", role: "admin" }]) {
  assert.equal(parseViewAs(bad), null, JSON.stringify(bad));
}

// --- the shared choice tells whoever listens, and can be dropped --------------------------------------------------------
let heard = 0;
const stop = subscribeViewAs(() => heard++);
setViewAs(view("client"));
assert.deepEqual(getViewAs(), view("client"));
setViewAs(null);
assert.equal(getViewAs(), null);
stop();
setViewAs(view("admin"));
assert.equal(heard, 2, "two changes heard, none after unsubscribing");

console.log("viewas: headers only for the viewed organization, rights drop the team's settings, the choice is well formed");
