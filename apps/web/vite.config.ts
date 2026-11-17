import { defineConfig } from "vite";
export default defineConfig({
  base: "/ui/",
  server: {
    proxy: {
      "/v1": "http://127.0.0.1:8001",
      "/health": "http://127.0.0.1:8001",
    },
  },
  build: { outDir: "dist" },
});
