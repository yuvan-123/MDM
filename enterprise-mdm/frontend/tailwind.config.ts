import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./features/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        brand: {
          50: "#f0f4ff",
          600: "#3b4fd8",
          700: "#2f3fb0",
        },
      },
    },
  },
  plugins: [],
};

export default config;
