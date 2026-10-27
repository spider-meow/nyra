// Fails when a file served from dist/ names a source map: the maps are written ("hidden" in vite.config.ts) for Sentry only,
// and the Dockerfile keeps them out of the web image. Run after `npm run build`: `npm run check:maps`.
import { existsSync, readFileSync, readdirSync } from "node:fs";

const assets = new URL("../dist/assets/", import.meta.url);
if (!existsSync(assets)) {
  console.error("dist/assets is missing: run `npm run build` first.");
  process.exit(1);
}
const files = readdirSync(assets).filter((name) => /\.(js|css)$/.test(name));
const naming = files.filter((name) => /\/\/# sourceMappingURL=|\/\*# sourceMappingURL=/.test(readFileSync(new URL(name, assets), "utf8")));
const maps = readdirSync(assets).filter((name) => name.endsWith(".map"));
if (naming.length || !maps.length) {
  console.error(naming.length ? `These files name a source map: ${naming.join(", ")}` : "No .map file was written: sourcemap is not \"hidden\" in vite.config.ts.");
  process.exit(1);
}
console.log(`${files.length} files name no source map; ${maps.length} maps written.`);
