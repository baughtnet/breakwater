import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development, API calls go to a core running locally (BW_DEMO=1 python -m breakwater.main).
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": "http://127.0.0.1:8080", "/ca.pem": "http://127.0.0.1:8080" } },
  build: { outDir: "dist", sourcemap: false },
});
