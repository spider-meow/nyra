// Checks the move of the interface's addresses from French to English (src/lib/paths.ts): every old address leads
// to its new page with its query renamed and its fragment kept (e-mailed sign-in links carry their key there),
// every new page exists in the router, and no old name is left in the interface's code.
// No test framework: the file is compiled in memory. Run: `npm run check:paths`.
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { transformWithOxc } from "vite";

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");
const { code: outputText } = await transformWithOxc(read("../src/lib/paths.ts"), "source.ts", { target: "es2022" });
const paths = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);
const { legacyTarget, legacySearch, currentPage, LEGACY_PAGES, LEGACY_BACK_OFFICE, RENAMED_PAGES } = paths;

// --- sign-in pages: the fragment (the key of an e-mailed link) survives ---------------------------------------
assert.equal(legacyTarget("/connexion"), "/login");
assert.equal(legacyTarget("/connexion", "", "#access_token=abc&type=invite"), "/login#access_token=abc&type=invite");
assert.equal(legacyTarget("/mot-de-passe", "", "#access_token=abc&refresh_token=def&type=recovery"), "/set-password#access_token=abc&refresh_token=def&type=recovery");
assert.equal(legacyTarget("/mot-de-passe", "?error=access_denied&error_code=otp_expired", ""), "/set-password?error=access_denied&error_code=otp_expired");

// --- back office --------------------------------------------------------------------------------------------
assert.equal(legacyTarget("/interne"), "/back-office");
assert.equal(legacyTarget("/interne/organisations"), "/back-office/organizations");
assert.equal(legacyTarget("/interne/facturation"), "/back-office/billing");
assert.equal(legacyTarget("/interne/comptes"), "/back-office/accounts");
assert.equal(legacyTarget("/interne/platform-admins"), "/back-office/platform-admins");

// --- every page of a brand, and /m/ -> /b/ -------------------------------------------------------------------
for (const [old, now] of Object.entries(LEGACY_PAGES)) {
  assert.equal(legacyTarget(`/o/remy-martin/m/louis-xiii/${old}`), `/o/remy-martin/b/louis-xiii/${now}`);
  assert.equal(legacyTarget(`/o/remy-martin/${old}`), `/o/remy-martin/${now}`); // from before brands
}
assert.equal(legacyTarget("/o/remy-martin/m/louis-xiii"), "/o/remy-martin/b/louis-xiii");
assert.equal(legacyTarget("/o/remy-martin/m/louis-xiii/"), "/o/remy-martin/b/louis-xiii");
assert.equal(legacyTarget("/o/remy-martin/m"), "/o/remy-martin");
assert.equal(legacyTarget("/o/m/m/b/reglages"), "/o/m/b/b/settings"); // an organization or a brand named like a path word
assert.equal(legacyTarget("/o/remy-martin/m/louis-xiii/page-inconnue"), "/o/remy-martin/b/louis-xiii/page-inconnue"); // its own "page not found" then

// --- a page renamed after the move to English keeps its address as a redirect, query and fragment kept -------------------
assert.equal(legacyTarget("/o/remy-martin/b/louis-xiii/site-images"), "/o/remy-martin/b/louis-xiii/online-images");
assert.equal(legacyTarget("/o/remy-martin/b/louis-xiii/site-images", "?view=logos", "#x"), "/o/remy-martin/b/louis-xiii/online-images?view=logos#x");
assert.equal(legacyTarget("/o/remy-martin/m/louis-xiii/images-du-site"), "/o/remy-martin/b/louis-xiii/online-images");
assert.equal(legacyTarget("/o/remy-martin/b/louis-xiii/online-images"), null);

// --- query: names renamed, values and order kept, repeated parameters kept ------------------------------------
assert.equal(
  legacyTarget("/o/r/m/l/a-traiter", "?fenetre=3650&statut=%3C30j&onglet=verify&pays=FR&pays=US&decision=retenu"),
  "/o/r/b/l/review?window=3650&status=%3C30j&tab=verify&country=FR&country=US&decision=retenu",
);
assert.equal(legacyTarget("/o/r/m/l/reglages", "?section=equipe"), "/o/r/b/l/settings?section=team");
assert.equal(legacyTarget("/o/r/m/l/reglages", "?section=marques"), "/o/r/b/l/settings?section=general");
assert.equal(legacyTarget("/o/r/m/l/reglages", "?section=profil"), "/o/r/b/l/settings?section=general");
assert.equal(legacyTarget("/o/r/m/l/reglages", "?section=lecture"), "/o/r/b/l/settings?section=reading");
assert.equal(legacyTarget("/o/r/m/l/reglages", "?section=compte"), "/o/r/b/l/settings?section=account");
assert.equal(legacyTarget("/o/r/m/l/reglages", "?section=detection"), "/o/r/b/l/settings?section=detection");
assert.equal(legacyTarget("/o/r/m/l/a-traiter", "?statut=expire", "#x"), "/o/r/b/l/review?status=expire#x");
assert.equal(legacySearch(""), "");
assert.equal(legacySearch("?window=30&q=a+b"), "?window=30&q=a+b"); // new names pass through untouched

// --- new addresses, and anything else, are left alone ---------------------------------------------------------
for (const path of ["/", "/login", "/set-password", "/back-office", "/back-office/billing", "/o/remy-martin",
  "/o/remy-martin/b/louis-xiii", "/o/remy-martin/b/louis-xiii/library", "/o/remy-martin/autre", "/o", "/nimporte-quoi"]) {
  assert.equal(legacyTarget(path), null, path);
}
assert.equal(currentPage("bibliotheque"), "library");
assert.equal(currentPage("library"), "library");
assert.equal(currentPage("constructor"), "constructor"); // not taken for a key of the table

// --- the router knows every new page, and keeps the old ones only as redirects ---------------------------------
const main = read("../src/main.tsx");
for (const page of [...Object.values(LEGACY_PAGES), ...Object.values(RENAMED_PAGES), ...Object.values(LEGACY_BACK_OFFICE), "platform-admins"]) {
  assert.ok(main.includes(`path: "${page}"`), `the router has no page "${page}"`);
}
for (const path of ["/login", "/set-password", "/back-office", "b/:brand"]) assert.ok(main.includes(`path: "${path}"`), `the router has no "${path}"`);
for (const old of ["/connexion", "/mot-de-passe", "/interne/*", "/o/:slug/m/*", ...Object.keys(RENAMED_PAGES)]) {
  assert.ok(main.includes(`path: "${old}", lazy: legacy }`), `"${old}" must redirect`);
}

// --- no old name left in the interface's code (paths.ts holds the table) --------------------------------------
const OLD = [...Object.keys(LEGACY_PAGES), ...Object.keys(LEGACY_BACK_OFFICE), "connexion", "mot-de-passe", "interne", "fenetre", "statut", "onglet", "pays", "equipe", "lecture", "compte"];
// After a quote, a slash, `?`, `&` or `=` (a path, a parameter, a section), and the old `/m/` level, in code or shown text.
const quoted = new RegExp(`["'\`/?&=](${OLD.join("|")})["'\`/?#=&]|/m/[{$:\\w]`);
const src = new URL("../src/", import.meta.url);
for (const file of readdirSync(src, { recursive: true })) {
  if (!/\.(ts|tsx)$/.test(file) || file.endsWith("paths.ts")) continue;
  const lines = readFileSync(new URL(file.replaceAll("\\", "/"), src), "utf8").split("\n");
  lines.forEach((line, index) => {
    if (/^\s*(\/\/|\*|\/\*\*)/.test(line)) return; // comments describe history freely
    const hit = line.match(quoted);
    assert.ok(!hit || line.includes("lazy: legacy"), `old name "${hit?.[1]}" left in src/${file}:${index + 1}`);
  });
}

console.log("paths: every old address leads to its new page, no old name left");
