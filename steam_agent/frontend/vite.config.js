import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => ({
  base: mode === "production" ? "/static/" : "/",
  plugins: [react()],
  build: {
    outDir: "../static",
    emptyOutDir: true,
  },
  server: {
    proxy: {
      "/auth": "http://localhost:8000",
      "/chat": "http://localhost:8000",
      "/threads": "http://localhost:8000",
      "/messages": "http://localhost:8000",
      "/thread-title": "http://localhost:8000",
      "/bind-steam": "http://localhost:8000",
      "/steam-id": "http://localhost:8000",
    },
  },
}));
