import { useCallback, useEffect, useRef, useState } from "react";
import { safeGet, safeSet } from "@/lib/storage";
import { publishThemeChange } from "@/lib/theme-store";

const STORAGE_KEY = "qa-theme";

// 系统默认深色：未手动设置主题时固定使用深色，不跟随系统 prefers-color-scheme。
function getPreferredTheme(): boolean {
  const saved = safeGet(STORAGE_KEY);
  if (saved === "dark") return true;
  if (saved === "light") return false;
  return true;
}

export function useDarkMode() {
  const [dark, setDark] = useState(getPreferredTheme);
  const darkRef = useRef(dark);

  useEffect(() => {
    darkRef.current = dark;
    document.documentElement.classList.toggle("dark", dark);
    document.documentElement.style.colorScheme = dark ? "dark" : "light";
    publishThemeChange();
  }, [dark]);

  useEffect(() => {
    const syncTheme = (nextDark: boolean) => {
      const changed = darkRef.current !== nextDark;
      darkRef.current = nextDark;
      setDark(nextDark);
      if (!changed) publishThemeChange();
    };

    const onStorage = (event: StorageEvent) => {
      if (event.key !== STORAGE_KEY && event.key !== null) return;
      syncTheme(getPreferredTheme());
    };
    window.addEventListener("storage", onStorage);

    // 默认固定深色，不监听系统 prefers-color-scheme 变化。
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  const toggle = useCallback(() => {
    const nextDark = !darkRef.current;
    darkRef.current = nextDark;
    safeSet(STORAGE_KEY, nextDark ? "dark" : "light");
    setDark(nextDark);
  }, []);

  return { dark, toggle };
}
