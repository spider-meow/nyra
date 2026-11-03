// Checks the rules of the team page (src/pages/settings/team.ts): the order is always the same (administrators first, then by name),
// names are read from the address, the search ignores case and accents, and the only administrator is recognised (the server refuses to
// demote or remove them, the page says so before the click). No test framework: the file is compiled in memory. Run: `npm run check:team`.
import assert from "node:assert/strict";
import { loadTs } from "./load-ts.mjs";

const { displayName, orderMembers, matches, isLastAdmin, splitPending, SEARCH_ABOVE } = await loadTs("/src/pages/settings/team.ts");

const who = (email, role = "client", pending = false) => ({ user_id: email, email, role, you: false, pending, invited_at: pending ? "2026-10-29T09:00:00Z" : null });

// Names come from the address.
assert.equal(displayName("adrien.duchemin@maison.fr"), "Adrien Duchemin");
assert.equal(displayName("inès_roux-martin@x.fr"), "Inès Roux Martin");
assert.equal(displayName("contact@x.fr"), "Contact");
assert.equal(displayName("...@x.fr"), "...@x.fr", "an address with no word is shown as it is");

// Administrators first, then by name, then by address; the same whatever the order they arrive in.
const team = [who("zoe.a@x.fr"), who("claire.b@x.fr", "admin"), who("anna.c@x.fr"), who("zoe.a@y.fr"), who("bob.d@x.fr", "admin")];
const order = orderMembers(team).map((member) => member.email);
assert.deepEqual(order, ["bob.d@x.fr", "claire.b@x.fr", "anna.c@x.fr", "zoe.a@x.fr", "zoe.a@y.fr"]);
assert.deepEqual(orderMembers([...team].reverse()).map((member) => member.email), order);
assert.equal(team[0].email, "zoe.a@x.fr", "the list given is not modified");

// Search: every word, any case, accents ignored, name or address.
const ines = who("inès.roux@maison.fr");
assert.ok(matches(ines, "INES"), "case and accents");
assert.ok(matches(ines, "roux maison"), "every word, in the name and the address");
assert.ok(matches(ines, "  "), "nothing typed shows everyone");
assert.ok(!matches(ines, "roux paul"), "one word that is not found excludes");
assert.ok(SEARCH_ABOVE === 8);

// The only administrator.
const lonely = who("a@x.fr", "admin");
assert.ok(isLastAdmin([lonely, who("b@x.fr")], lonely));
assert.ok(!isLastAdmin([lonely, who("c@x.fr", "admin")], lonely));
assert.ok(!isLastAdmin([lonely, who("b@x.fr")], who("b@x.fr")), "a client is never the last administrator");

// Invitations nobody opened yet are apart from the members, each group in the same order.
const mixed = [who("zoe@x.fr", "client", true), who("bob@x.fr"), who("amy@x.fr", "client", true), who("cat@x.fr", "admin")];
const split = splitPending(mixed);
assert.deepEqual(split.active.map((member) => member.email), ["cat@x.fr", "bob@x.fr"]);
assert.deepEqual(split.waiting.map((member) => member.email), ["amy@x.fr", "zoe@x.fr"]);
assert.deepEqual(splitPending([]), { active: [], waiting: [] });

console.log("team: names, order, search, pending invitations and the last administrator are right");
