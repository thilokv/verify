import type { Config } from "tailwindcss";
export default {
  content: ["./src/**/*.{ts,tsx}"],
  theme: { extend: { colors: { accent: "#2C3E72", verified: "#1F6B4A" } } },
  plugins: [],
} satisfies Config;
