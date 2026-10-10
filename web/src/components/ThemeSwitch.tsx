import { useState } from "react";

type Theme = "dark" | "light";
const KEY = "tp-theme";

/** Reads the saved theme; dark is the default. Storage can be unavailable, so every access is guarded. */
export function initialTheme(): Theme {
  const q = new URLSearchParams(location.search).get("theme");
  if (q === "dark" || q === "light") return q;
  try { return localStorage.getItem(KEY) === "light" ? "light" : "dark"; } catch { return "dark"; }
}

export function applyTheme(t: Theme) {
  if (t === "light") document.documentElement.dataset.theme = "light";
  else delete document.documentElement.dataset.theme;
}

/** One small button in the top bar: dark for the evening, light for a bright desk. */
export function ThemeSwitch() {
  const [theme, setTheme] = useState<Theme>(initialTheme);
  const next: Theme = theme === "dark" ? "light" : "dark";
  return (
    <button
      className="iconbtn"
      aria-label={`Switch to ${next} theme`}
      title={`Switch to ${next} theme`}
      onClick={() => {
        applyTheme(next);
        setTheme(next);
        try { localStorage.setItem(KEY, next); } catch { /* not saved; still switched */ }
      }}
    >
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" aria-hidden>
        {theme === "dark" ? (
          <path d="M13.5 9.5A5.5 5.5 0 016.5 2.5a5.5 5.5 0 107 7z" />
        ) : (
          <>
            <circle cx="8" cy="8" r="3" />
            <path d="M8 1.5v1.5M8 13v1.5M1.5 8H3M13 8h1.5M3.4 3.4l1 1M11.6 11.6l1 1M3.4 12.6l1-1M11.6 4.4l1-1" />
          </>
        )}
      </svg>
    </button>
  );
}
