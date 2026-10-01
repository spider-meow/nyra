import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // `nyra serve` listens on 8000 by default.
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
  build: {
    rollupOptions: {
      output: {
        // Libraries change far less often than the app: separate files cache separately.
        // A path test, not a list of package names: `react-dom/client` (the part that
        // matters, ~180 KB) is another entry point of its package and a name list misses it.
        manualChunks(id) {
          if (/node_modules\/(react|react-dom|react-router|scheduler)\//.test(id)) return "react";
          if (id.includes("node_modules/@supabase/")) return "supabase";
          if (id.includes("node_modules/@tanstack/")) return "query";
        },
      },
    },
  },
});
