import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// The API runs on :8000 (uvicorn); proxying keeps the browser same-origin, so no CORS config.
const api = { target: "http://127.0.0.1:8000" };
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { proxy: { "/studio": api, "/queries": api, "/results": api, "/health": api } },
});
