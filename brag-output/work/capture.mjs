// node capture.mjs stills t1 t2 ...   -> stills/t.png
// node capture.mjs frames            -> frames/00000.png ... (600 frames at 30 fps)
import { createRequire } from "node:module";
const { chromium } = createRequire(import.meta.url)("/opt/node-tools/node_modules/playwright");
import { mkdirSync } from "node:fs";
import { pathToFileURL } from "node:url";

const [mode, ...rest] = process.argv.slice(2);
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 });
page.on("pageerror", (e) => { console.error("page error:", e.message); process.exitCode = 1; });
await page.goto(pathToFileURL(new URL("scene.html", import.meta.url).pathname).href);
await page.evaluate(() => window.ready);

const shot = async (t, path) => {
  await page.evaluate((time) => window.renderAt(time), t);
  await page.screenshot({ path, type: "png" });
};
if (mode === "stills") {
  mkdirSync("stills", { recursive: true });
  for (const t of rest) await shot(Number(t), `stills/${t}.png`);
} else {
  mkdirSync("frames", { recursive: true });
  const fps = 30, total = 20 * fps;
  for (let i = 0; i < total; i++) await shot(i / fps, `frames/${String(i).padStart(5, "0")}.png`);
}
await browser.close();
