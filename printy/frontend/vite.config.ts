import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vite";

// Relative base: Home Assistant Ingress serves the app under a dynamic prefix.
export default defineConfig({
  base: "./",
  plugins: [vue()],
  build: { outDir: "../backend/printy/static", emptyOutDir: true },
  server: { proxy: { "/api": "http://127.0.0.1:8099" } },
});
