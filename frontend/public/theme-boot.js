(() => {
  const root = document.documentElement;
  let savedTheme = null;

  try {
    savedTheme = window.localStorage.getItem("qa-theme");
  } catch {
    // Storage can be unavailable in restricted iframes and WebViews.
  }

  // 系统默认深色：未手动设置主题时固定使用深色，不跟随系统 prefers-color-scheme。
  const dark = savedTheme !== "light"; // "dark" / null → 深色；仅 "light" → 浅色
  root.classList.toggle("dark", dark);
  root.style.colorScheme = dark ? "dark" : "light";
})();
