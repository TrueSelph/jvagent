import { defineConfig } from "vite";
import { resolve } from "path";

// Build config for the framework-free loader (loader.js). Emits a single IIFE
// with everything inlined — no code-splitting, no React — so the customer's
// one <script> tag is self-contained. Runs AFTER the app build with
// emptyOutDir:false so it doesn't wipe app.html/assets.
export default defineConfig({
  build: {
    outDir: "dist",
    emptyOutDir: false,
    lib: {
      entry: resolve(__dirname, "src/loader/loader.ts"),
      name: "JvMessengerLoader",
      formats: ["iife"],
      fileName: () => "loader.js",
    },
    rollupOptions: {
      output: {
        entryFileNames: "loader.js",
        inlineDynamicImports: true,
      },
    },
  },
});
