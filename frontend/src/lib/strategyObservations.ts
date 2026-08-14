import {
  api,
  type MarketCode,
  type TdxMarketOverview,
  type TdxRankResult,
  type TdxScreenerResult,
  type TdxSectorResult,
} from "@/lib/api";

export interface StrategyObservations {
  overview: TdxMarketOverview | null;
  sectors: TdxSectorResult | null;
  valueUniverse: TdxScreenerResult | null;
  momentum: TdxRankResult | null;
}

export const EMPTY_STRATEGY_OBSERVATIONS: StrategyObservations = {
  overview: null,
  sectors: null,
  valueUniverse: null,
  momentum: null,
};

async function optional<T>(read: () => Promise<T>): Promise<T | null> {
  try {
    return await read();
  } catch {
    return null;
  }
}

/**
 * Loads factual market observations independently from deterministic strategy
 * scores. A failed optional feed must not hide the other available feeds.
 */
export type ObservationScope = "all" | "overview" | "sectors" | "value" | "momentum";

export async function loadStrategyObservations(market: MarketCode, scope: ObservationScope = "all"): Promise<StrategyObservations> {
  if (market !== "CN") return EMPTY_STRATEGY_OBSERVATIONS;
  const [overview, sectors, valueUniverse, momentum] = await Promise.all([
    scope === "all" || scope === "overview" ? optional(() => api.getTdxMarketOverview()) : null,
    scope === "all" || scope === "sectors" ? optional(() => api.getTdxSectors({ category: "行业", limit: 12 })) : null,
    scope === "all" || scope === "value" ? optional(() => api.screenTdxSecurities({
      include_st: false,
      include_quit: false,
      include_bj: false,
      sort: "market_cap_100m",
      direction: "desc",
      limit: 12,
    })) : null,
    scope === "all" || scope === "momentum" ? optional(() => api.getTdxMarketRanks({ category: "涨幅榜", limit: 12 })) : null,
  ]);
  return { overview, sectors, valueUniverse, momentum };
}
