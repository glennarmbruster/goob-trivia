import { defineConfig } from "vite";
import { VitePWA } from "vite-plugin-pwa";

const requestedBase = process.env.BASE_PATH || "/";
const base = `/${requestedBase.replace(/^\/+|\/+$/g, "")}${requestedBase === "/" ? "" : "/"}`;

export default defineConfig({
  base,
  optimizeDeps: {
    exclude: ["@sqlite.org/sqlite-wasm"]
  },
  plugins: [
    VitePWA({
      registerType: "autoUpdate",
      includeAssets: ["favicon.svg"],
      manifest: {
        id: base,
        name: "Goobs Trivia Finder",
        short_name: "Trivia Finder",
        description: "Find likely trivia answers from partial clues, completely offline.",
        theme_color: "#211341",
        background_color: "#100a20",
        display: "standalone",
        start_url: base,
        scope: base,
        icons: [
          {
            src: `${base}app-icon.svg`,
            sizes: "any",
            type: "image/svg+xml",
            purpose: "any maskable"
          }
        ]
      },
      workbox: {
        globPatterns: ["**/*.{js,css,html,svg,wasm}"],
        navigateFallback: `${base}index.html`,
        maximumFileSizeToCacheInBytes: 8 * 1024 * 1024
      }
    })
  ],
  worker: {
    format: "es"
  }
});
