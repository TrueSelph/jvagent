import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'
// NOTE: jvchat is on Tailwind v4 (@tailwindcss/postcss). After pulling these
// changes, restart `npm run dev` — Vite does not hot-reload PostCSS config.

/** Keep route chunks and heavy deps under the default 500k warning threshold. */
function manualChunks(id: string): string | undefined {
  if (!id.includes('node_modules')) {
    return undefined
  }
  if (
    /[\\/]node_modules[\\/](?:react(?!-)|react-dom|react-router|scheduler|react-is|@remix-run[\\/]router)[\\/]/.test(
      id
    )
  ) {
    return 'vendor-react'
  }
  if (id.includes('cytoscape-dagre') || /[\\/]node_modules[\\/]dagre[\\/]/.test(id)) {
    return 'vendor-dagre-bridge'
  }
  if (/[\\/]node_modules[\\/]cytoscape(?!-)/.test(id)) {
    return 'vendor-cytoscape'
  }
  if (id.includes('d3-graphviz') || id.includes('@hpcc-js')) {
    return 'vendor-graphviz'
  }
  if (/[\\/]node_modules[\\/](d3(-[a-z0-9-]+)?|internmap|delaunator|robust-predicates)[\\/]/.test(id)) {
    return 'vendor-d3'
  }
  if (
    /[\\/]node_modules[\\/](react-markdown|remark-|rehype-|unified|mdast|micromark|hast|vfile|bail|ccount|entities|is-|character-|decode-|escape-|markdown-|zwitch|trim-lines|space-separated-tokens|comma-separated-tokens|property-information|hast-)[\\/]/.test(
      id
    )
  ) {
    return 'vendor-markdown'
  }
  return undefined
}

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5173,
    open: true,
  },
  build: {
    // vendor-graphviz (~800k min) is wasm-heavy; only fetched on DOT graph path
    chunkSizeWarningLimit: 900,
    rollupOptions: {
      input: {
        main: path.resolve(__dirname, 'index.html'),
      },
      output: {
        manualChunks,
      },
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
  },
})

