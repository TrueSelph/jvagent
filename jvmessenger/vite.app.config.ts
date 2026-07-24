import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "path";

// Build config for the iframe chat app (app.html + hashed assets). Served by
// `jvagent messenger` from the messenger origin and framed by the loader.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": resolve(__dirname, "./src") },
  },
  base: "/",
  server: {
    port: 5174,
    open: true,
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    rollupOptions: {
      input: {
        app: resolve(__dirname, "app.html"),
      },
    },
  },
});
