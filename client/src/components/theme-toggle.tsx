import { Button } from "@/components/ui/button";
import { MoonIcon, SunIcon } from "lucide-react";

const KEY = "liverecall-theme";

// Stored choice first, else the system preference. Called before the first render.
export function applyStoredTheme() {
  const stored = localStorage.getItem(KEY);
  const dark = stored ? stored === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  document.documentElement.classList.toggle("dark", dark);
}

export function ThemeToggle() {
  function toggle() {
    const dark = document.documentElement.classList.toggle("dark");
    localStorage.setItem(KEY, dark ? "dark" : "light");
  }

  return (
    <Button aria-label="Toggle color theme" onClick={toggle} size="icon-sm" type="button" variant="ghost">
      <SunIcon className="hidden dark:block" />
      <MoonIcon className="block dark:hidden" />
    </Button>
  );
}
