// Loads a TypeScript file of src/ for a check script, imports included (vite compiles it in memory): no test framework needed.
import { createServer } from "vite";

export async function loadTs(path) {
  const server = await createServer({ configFile: false, appType: "custom", logLevel: "silent", server: { middlewareMode: true, hmr: false, ws: false } });
  try {
    return await server.ssrLoadModule(path);
  } finally {
    await server.close();
  }
}
