import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// API base: VITE_API (default http://localhost:8000). Run the backend with uvicorn on the Windows/WSL host.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, host: true },
});
