import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// API base: VITE_API, else same host as the page on port 8000 (so a phone on the same Wi-Fi works).
// `base: "./"` keeps the build relocatable (e.g. served by the backend under /care/).
export default defineConfig({
  plugins: [react()],
  base: "./",
  server: { port: 5174, host: true },
});
