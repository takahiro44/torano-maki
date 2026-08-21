import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Tailwind v4 は Vite プラグインとして入れる。
// v3 のような tailwind.config.js / postcss.config.js は不要で、
// 設定は CSS 側（src/index.css）の @theme に書く。
export default defineConfig({
  plugins: [react(), tailwindcss()],
});
