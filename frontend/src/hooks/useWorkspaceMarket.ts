import { useEffect, useState } from "react";
import type { MarketCode } from "@/lib/api";
import { safeGet, safeSet } from "@/lib/storage";

const KEY = "hengzhi-market";
const EVENT = "hengzhi:market-change";

function readMarket(): MarketCode {
  const value = safeGet(KEY);
  return value === "HK" || value === "US" ? value : "CN";
}

export function useWorkspaceMarket() {
  const [market, setValue] = useState<MarketCode>(readMarket);
  useEffect(() => {
    const sync = () => setValue(readMarket());
    window.addEventListener(EVENT, sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener(EVENT, sync);
      window.removeEventListener("storage", sync);
    };
  }, []);
  const setMarket = (next: MarketCode) => {
    safeSet(KEY, next);
    setValue(next);
    window.dispatchEvent(new CustomEvent(EVENT));
  };
  return { market, setMarket };
}
