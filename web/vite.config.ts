import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `npm run build` writes straight into the Python package, so
// `python -m hoopsim web` works without Node. `npm run dev` proxies the API
// to a running `python -m hoopsim web --no-open` on port 8000.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../hoopsim/web/static",
    emptyOutDir: true,
    chunkSizeWarningLimit: 800,
  },
  server: {
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
});
