import { useEffect, useState } from "react";

export type Theme = "dark" | "light";
const KEY = "prsentinel.theme";

function current(): Theme {
  return document.documentElement.dataset.theme === "light" ? "light" : "dark";
}

/** The chosen theme. The page script in index.html applies it before the first paint. */
export function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(current);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", theme === "light" ? "#ffffff" : "#010409");
    try {
      localStorage.setItem(KEY, theme);
    } catch {
      // Storage can be blocked; the choice then lasts until the page is closed.
    }
  }, [theme]);
  return [theme, () => setTheme(theme === "dark" ? "light" : "dark")];
}
