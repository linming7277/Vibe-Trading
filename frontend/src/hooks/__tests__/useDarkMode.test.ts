import { act, renderHook } from "@testing-library/react";
import { useDarkMode } from "../useDarkMode";

function mockMatchMedia(matches: boolean) {
  const listeners = new Set<(event: MediaQueryListEvent) => void>();
  const mediaQuery = {
    matches,
    media: "(prefers-color-scheme: dark)",
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn((_type: string, listener: (event: MediaQueryListEvent) => void) => {
      listeners.add(listener);
    }),
    removeEventListener: vi.fn((_type: string, listener: (event: MediaQueryListEvent) => void) => {
      listeners.delete(listener);
    }),
    dispatchEvent: vi.fn(),
  } as unknown as MediaQueryList;

  vi.spyOn(window, "matchMedia").mockReturnValue(mediaQuery);
  return {
    emit(nextMatches: boolean) {
      listeners.forEach((listener) => listener({ matches: nextMatches } as MediaQueryListEvent));
    },
  };
}

describe("useDarkMode", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.classList.remove("dark");
    document.documentElement.style.colorScheme = "";
    mockMatchMedia(false);
  });

  it("uses the product dark default when no preference is stored", () => {
    const { result } = renderHook(() => useDarkMode());

    expect(result.current.dark).toBe(true);
    expect(localStorage.getItem("qa-theme")).toBeNull();
  });

  it("keeps the product dark default when the system theme is dark", () => {
    mockMatchMedia(true);

    const { result } = renderHook(() => useDarkMode());

    expect(result.current.dark).toBe(true);
    expect(localStorage.getItem("qa-theme")).toBeNull();
  });

  it("uses a stored preference over the system theme", () => {
    localStorage.setItem("qa-theme", "dark");

    const { result } = renderHook(() => useDarkMode());

    expect(result.current.dark).toBe(true);
  });

  it("persists a preference only when toggled", () => {
    const { result } = renderHook(() => useDarkMode());
    expect(localStorage.getItem("qa-theme")).toBeNull();

    act(() => result.current.toggle());
    expect(localStorage.getItem("qa-theme")).toBe("light");

    act(() => result.current.toggle());
    expect(localStorage.getItem("qa-theme")).toBe("dark");
  });

  it("keeps the root class and color scheme in sync", () => {
    const { result } = renderHook(() => useDarkMode());
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(document.documentElement.style.colorScheme).toBe("dark");

    act(() => result.current.toggle());
    expect(document.documentElement.classList.contains("dark")).toBe(false);
    expect(document.documentElement.style.colorScheme).toBe("light");
  });

  it("re-reads the stored preference after a cross-tab storage event", () => {
    const { result } = renderHook(() => useDarkMode());
    localStorage.setItem("qa-theme", "dark");

    act(() => window.dispatchEvent(new StorageEvent("storage", { key: "qa-theme" })));

    expect(result.current.dark).toBe(true);
    expect(document.documentElement.style.colorScheme).toBe("dark");
  });

  it("returns to the product dark default when the stored preference is removed", () => {
    localStorage.setItem("qa-theme", "light");
    const system = mockMatchMedia(false);
    const { result } = renderHook(() => useDarkMode());
    localStorage.removeItem("qa-theme");

    act(() => {
      window.dispatchEvent(new StorageEvent("storage", { key: "qa-theme" }));
      system.emit(true);
    });

    expect(result.current.dark).toBe(true);
    expect(localStorage.getItem("qa-theme")).toBeNull();
  });
});
