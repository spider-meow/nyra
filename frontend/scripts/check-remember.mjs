// End-to-end check of "Rester connecté": real Chromium, the dev server, Supabase and the API mocked.
// Checked: the session lands in localStorage (box ticked) or sessionStorage (unticked), and after a
// browser restart (localStorage kept, sessionStorage lost) only the ticked one is still signed in.
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { createServer } from "vite";

const user = { id: "u1", email: "a@b.co", aud: "authenticated", role: "authenticated", app_metadata: {}, user_metadata: {}, created_at: "2024-01-01T00:00:00Z" };
const session = () => ({ access_token: "at", refresh_token: "rt", token_type: "bearer", expires_in: 3600, expires_at: Math.floor(Date.now() / 1000) + 3600, user });

async function mocked(browser, options) {
  const context = await browser.newContext(options);
  await context.route("https://sb.test/**", (route) => route.fulfill({ json: route.request().url().includes("/token") ? session() : user }));
  await context.route("**/api/**", (route) =>
    route.fulfill({ json: route.request().url().endsWith("/auth/config") ? { supabaseUrl: "https://sb.test", anonKey: "k", sentry: null } : [] }),
  );
  return context;
}

const server = await createServer({ server: { port: 5199, strictPort: true }, logLevel: "error" });
await server.listen();
const origin = "http://localhost:5199";
const browser = await chromium.launch({ args: ["--no-sandbox"] });
try {
  for (const remember of [true, false]) {
    const context = await mocked(browser);
    const page = await context.newPage();
    await page.goto(`${origin}/login`);
    await page.fill("#email", "a@b.co");
    await page.fill("#password", "password123");
    const box = page.getByLabel("Rester connecté");
    assert.equal(await box.isChecked(), true, "ticked by default");
    if (!remember) await box.click();
    await page.click("button[type=submit]");
    await page.waitForFunction(() => !location.pathname.startsWith("/login"));
    const where = await page.evaluate(() => ({ local: localStorage.getItem("sb-sb-auth-token") !== null, session: sessionStorage.getItem("sb-sb-auth-token") !== null }));
    assert.deepEqual(where, { local: remember, session: !remember });

    const restarted = await mocked(browser, { storageState: await context.storageState() });
    const next = await restarted.newPage();
    await next.goto(`${origin}/`);
    await next.waitForFunction(() => location.pathname !== "/" || document.body.innerText.length > 0);
    await next.waitForTimeout(1000);
    assert.equal(new URL(next.url()).pathname.startsWith("/login"), !remember, `after restart (remember=${remember})`);
    await context.close();
    await restarted.close();
  }
  console.log("remember: ticked keeps the session across a restart, unticked ends it");
} finally {
  await browser.close();
  await server.close();
}
