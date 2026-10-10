import { useState } from "react";
import { Moon, Sun } from "lucide-react";
import { Button } from "@/components/ui/button";

const KEY = "tp-theme";
type Theme = "dark" | "light";

/** Dark is the default. Storage may be unavailable, so every access is guarded. */
export function initialTheme(): Theme {
  const q = new URLSearchParams(location.search).get("theme");
  if (q === "dark" || q === "light") return q;
  try { return localStorage.getItem(KEY) === "light" ? "light" : "dark"; } catch { return "dark"; }
}

export function applyTheme(t: Theme) {
  document.documentElement.classList.toggle("dark", t === "dark");
}

export function ThemeSwitch() {
  const [theme, setTheme] = useState<Theme>(initialTheme);
  const next: Theme = theme === "dark" ? "light" : "dark";
  return (
    <Button
      variant="ghost"
      size="icon"
      className="text-muted-foreground size-8 max-sm:size-11"
      aria-label={`Switch to ${next} theme`}
      onClick={() => {
        applyTheme(next);
        setTheme(next);
        try { localStorage.setItem(KEY, next); } catch { /* switched, not saved */ }
      }}
    >
      {theme === "dark" ? <Moon /> : <Sun />}
    </Button>
  );
}
