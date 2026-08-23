/** @type {import('tailwindcss').Config} */
export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        midnight: {
          950: "#070A10",
          900: "#0B0F17",
          850: "#0F141E",
          800: "#151B28",
          700: "#1E2637",
          600: "#2A344A",
          500: "#3A4765",
          400: "#5A6988",
        },
        emerald: {
          350: "#34D399",
          450: "#10B981",
          550: "#059669",
        },
        ruby: {
          450: "#F43F5E",
          550: "#E11D48",
        },
        amber: {
          450: "#F59E0B",
        },
        gridline: {
          DEFAULT: "rgba(16, 185, 129, 0.07)",
        },
      },
      fontFamily: {
        display: ['"Space Grotesk"', "system-ui", "sans-serif"],
        sans: ['"DM Sans"', "system-ui", "sans-serif"],
        mono: ['"JetBrains Mono"', "ui-monospace", "SFMono-Regular", "monospace"],
      },
      boxShadow: {
        glow: "0 0 0 1px rgba(16, 185, 129, 0.18), 0 10px 40px -12px rgba(16, 185, 129, 0.28)",
        card: "0 1px 0 rgba(255,255,255,0.03) inset, 0 10px 30px -20px rgba(0,0,0,0.6)",
      },
      backgroundImage: {
        "grid-faint":
          "linear-gradient(to right, rgba(16,185,129,0.045) 1px, transparent 1px), linear-gradient(to bottom, rgba(16,185,129,0.045) 1px, transparent 1px)",
        "radial-fade":
          "radial-gradient(ellipse at 15% 0%, rgba(16,185,129,0.15), transparent 55%), radial-gradient(ellipse at 85% 100%, rgba(59,130,246,0.1), transparent 55%)",
      },
      animation: {
        "fade-up": "fadeUp 0.6s ease-out both",
        "pulse-dot": "pulseDot 1.6s ease-in-out infinite",
        shimmer: "shimmer 2.2s linear infinite",
      },
      keyframes: {
        fadeUp: {
          "0%": { opacity: "0", transform: "translateY(10px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        pulseDot: {
          "0%, 100%": { opacity: "0.3", transform: "scale(0.9)" },
          "50%": { opacity: "1", transform: "scale(1.1)" },
        },
        shimmer: {
          "0%": { backgroundPosition: "-400px 0" },
          "100%": { backgroundPosition: "400px 0" },
        },
      },
    },
  },
  plugins: [],
};
