/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Neutral slate-based surface palette for a dense, professional terminal feel.
        surface: {
          0: "rgb(var(--surface-0) / <alpha-value>)", // app background
          1: "rgb(var(--surface-1) / <alpha-value>)", // panels / cards
          2: "rgb(var(--surface-2) / <alpha-value>)", // raised elements
          3: "rgb(var(--surface-3) / <alpha-value>)", // hover / borders-inner
        },
        line: "rgb(var(--line) / <alpha-value>)",
        accent: {
          DEFAULT: "#3b82f6",
          soft: "rgb(var(--accent-soft) / <alpha-value>)",
        },
        pos: "#3fb98a", // gains
        neg: "#e5634d", // losses
        warn: "#e0a83d",
      },
      fontSize: {
        "2xs": ["0.6875rem", { lineHeight: "0.9rem" }],
      },
    },
  },
  plugins: [],
};
