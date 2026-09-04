/**
 * 价格区结论卡（研究结论，不是交易指令）
 *
 * 人话定义（只用于解释，不构成交易语义）：
 * - 价值范围：同时是该细分行业龙头 Top1/Top2，且估值显示便宜。不在范围 ≠ 公司差，
 *   只是当前不进这张研究表。
 * - 估值：估算「大概值多少钱」，不是预测明天涨跌。
 * - 价格区：一个价格范围，不是一个精确关注点位。
 * - 价格条件：现价相对价格区，值不值得继续盯；档位只用后端已有枚举。
 * - 支撑：历史上多次止跌的价格带。压力：历史上多次上不去的价格带。
 * - 可靠性：估值依据够不够；不够就不能把「便宜」当主结论。
 * - 新鲜度：K 线/报价日期旧不旧；旧了不能当主理由。
 *
 * 后端已有结论，前端只投影；禁止前端用 PE/PB 重算「便宜/贵」或重算档位。
 */
import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { api, type ValuePriceZone, type ValuePriceZones, type ValueStrategyState } from "@/lib/api";
import { cn } from "@/lib/utils";
import { formatNumber } from "@/components/workspace/WorkspaceUI";

const REVIEW_FIRST_STATUSES = new Set(["DATA_REVIEW_REQUIRED", "VALUATION_REVIEW_REQUIRED", "BLOCKED"]);

function errText(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason ?? "");
}

function zoneText(zone: ValuePriceZone): string {
  if (zone.low == null && zone.high == null) return "区间不完整";
  if (zone.low == null) return `低于 ${formatNumber(zone.high)}`;
  if (zone.high == null) return `高于 ${formatNumber(zone.low)}`;
  return `${formatNumber(zone.low)}–${formatNumber(zone.high)}`;
}

/** 闭区间判断；开区间沿用后端 ZoneRange 语义：low=null 表示 (−∞, high]，high=null 表示 [low, ∞)。 */
function priceInZone(price: number, zone: ValuePriceZone): boolean {
  if (zone.low == null) return zone.high != null && price <= zone.high;
  if (zone.high == null) return price >= zone.low;
  return zone.low <= price && price <= zone.high;
}

/** 现价落在哪条带里：六种固定落点句，与日报 _price_position_sentence 同一清单同一优先级。
 * 「未落入」= 带存在但现价不在其中（含全部在上方）；「带不完整」仅用于现价缺失或无可判带。 */
export function describePricePosition(zones: ValuePriceZones | null): string {
  const price = zones?.current_price;
  if (price == null) return "带不完整，无法判断落点";
  const confluence = (zones?.confluence_zones ?? []).slice(0, 2);
  if (confluence.some((zone) => priceInZone(price, zone))) return "现价落在关注带内";
  const support = (zones?.support_zones ?? []).slice(0, 2);
  if (support.some((zone) => priceInZone(price, zone))) return "现价落在观察带内";
  const review = (zones?.upper_review_zones ?? []).slice(0, 2);
  if (review.some((zone) => zone.low != null && price >= zone.low)) return "现价落在复核带内";
  const low = zones?.valuation.fair_value_low;
  if (low != null && price < low) return "现价低于合理价值带下限";
  const hasBound = (rows: ValuePriceZone[]) => rows.some((zone) => zone.low != null || zone.high != null);
  if (hasBound(confluence) || hasBound(support) || hasBound(review)) {
    return "现价未落入关注/观察/复核带";
  }
  return "带不完整，无法判断落点";
}

type LoadState = {
  loading: boolean;
  strategy: ValueStrategyState | null;
  strategyError: string;
  zones: ValuePriceZones | null;
  zonesError: string;
};

export function ValuePriceZoneConclusionCard({ stockCode }: { stockCode: string }) {
  const [state, setState] = useState<LoadState>({ loading: true, strategy: null, strategyError: "", zones: null, zonesError: "" });
  useEffect(() => {
    let alive = true;
    setState({ loading: true, strategy: null, strategyError: "", zones: null, zonesError: "" });
    void Promise.allSettled([api.getValueStrategyState(stockCode), api.getCompanyPriceZones(stockCode)]).then(([strategy, zoneResult]) => {
      if (!alive) return;
      setState({
        loading: false,
        strategy: strategy.status === "fulfilled" ? strategy.value : null,
        strategyError: strategy.status === "rejected" ? errText(strategy.reason) : "",
        zones: zoneResult.status === "fulfilled" ? zoneResult.value : null,
        zonesError: zoneResult.status === "rejected" ? errText(zoneResult.reason) : "",
      });
    });
    return () => { alive = false; };
  }, [stockCode]);

  if (state.loading) {
    return <section className="rounded-xl border bg-card p-5 text-sm text-muted-foreground"><Loader2 className="mr-2 inline h-4 w-4 animate-spin" />读取价格区研究结论…</section>;
  }
  if (!state.strategy && !state.zones) {
    return <section className="rounded-xl border border-danger/30 bg-card p-5"><h2 className="font-semibold">价格区（研究结论，不是交易指令）</h2><p className="mt-2 text-sm text-muted-foreground">价格区资料暂不可用：研究结论与价格带接口都失败了。</p>{state.strategyError ? <p className="mt-1 text-xs text-muted-foreground">研究结论：{state.strategyError}</p> : null}{state.zonesError ? <p className="mt-1 text-xs text-muted-foreground">价格带：{state.zonesError}</p> : null}</section>;
  }

  const strategy = state.strategy;
  const zones = state.zones;
  const attention = strategy?.price_attention;
  const reliability = attention?.valuation_reliability;
  const structure = strategy?.freshness.price_structure;
  const suspension = strategy?.freshness.suspension;
  const reviewFirst = REVIEW_FIRST_STATUSES.has(String(attention?.effective_status ?? ""));

  const badge = (label: string, value: string, tone?: string) => (
    <span className={cn("rounded-lg border px-3 py-1.5 text-sm", tone ?? "border-border bg-card text-foreground")}>
      <span className="text-xs text-muted-foreground">{label}：</span>
      <strong className="font-semibold">{value}</strong>
    </span>
  );

  const attentionTone =
    attention?.effective_status === "HIGH_ATTENTION" || attention?.effective_status === "ATTENTION"
      ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-700"
      : reviewFirst
        ? "border-amber-500/40 bg-amber-500/10 text-amber-800"
        : "border-border bg-card text-foreground";

  const band = (title: string, hint: string, rows: ValuePriceZone[], empty: string) => (
    <article className="rounded-lg border border-border p-3">
      <div className="text-xs font-medium">{title}</div>
      <p className="mt-0.5 text-[11px] leading-4 text-muted-foreground">{hint}</p>
      {rows.length ? (
        <div className="mt-2 space-y-1.5 text-sm tabular-nums">{rows.map((zone, index) => <div key={`${title}-${index}`}>{zoneText(zone)}</div>)}</div>
      ) : (
        <p className="mt-2 text-sm text-muted-foreground">{empty}</p>
      )}
    </article>
  );

  const cautionLines: string[] = [];
  if (reviewFirst && strategy) {
    cautionLines.push(String(strategy.price_attention.effective_label));
    for (const item of strategy.price_attention.cautions) cautionLines.push(item);
  } else {
    for (const item of strategy?.price_attention.cautions ?? []) cautionLines.push(item);
  }
  if (strategy?.freshness.notice) cautionLines.push(strategy.freshness.notice);
  const valuation = zones?.valuation;
  const fairBandReady = valuation != null && valuation.fair_value_low != null && valuation.fair_value_high != null;
  if (valuation && (reliability?.status !== "RELIABLE" || !fairBandReady)) {
    for (const item of valuation.limitations) cautionLines.push(item);
    if (valuation.message) cautionLines.push(valuation.message);
  }

  const position = describePricePosition(zones);
  const historical = zones?.historical_valuation;
  const metrics = historical?.historical_percentiles;

  return (
    <section className="rounded-xl border border-primary/25 bg-card p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="text-xs font-medium text-primary">价格区</div>
          <h2 className="mt-1 text-lg font-semibold">价格区（研究结论，不是交易指令）</h2>
          <p className="mt-1 text-xs text-muted-foreground">只告诉你现价落在哪一带、该不该继续盯；不会给出任何交易指令。</p>
        </div>
        {strategy ? <span className="rounded bg-primary/10 px-2 py-1 text-xs text-primary">{strategy.primary_action.label}</span> : null}
      </div>

      <div className="mt-4 flex flex-wrap gap-2">
        {strategy
          ? badge("价值范围", strategy.eligibility.status === "IN_VALUE_SCOPE" ? "在范围内" : "不在范围内")
          : badge("价值范围", "资料不足")}
        {strategy
          ? badge("价格条件", attention?.effective_label ?? "资料不足", attentionTone)
          : badge("价格条件", `策略结论暂不可用${state.strategyError ? `（${state.strategyError}）` : ""}`, "border-amber-500/40 bg-amber-500/10 text-amber-800")}
        {reliability ? badge("估值依据", reliability.label, reliability.status === "RELIABLE" ? "border-border bg-card text-foreground" : "border-amber-500/40 bg-amber-500/10 text-amber-800") : badge("估值依据", "资料不足")}
        {structure ? badge("行情新旧", structure.label, structure.status === "FRESH" || structure.status === "ACCEPTABLE" ? "border-border bg-card text-foreground" : "border-amber-500/40 bg-amber-500/10 text-amber-800") : badge("行情新旧", "资料不足")}
        {suspension?.status === "SUSPENDED_INFERRED"
          ? badge("交易状态", "停牌中（推断）", "border-amber-500/40 bg-amber-500/10 text-amber-800")
          : null}
      </div>

      {suspension?.status === "SUSPENDED_INFERRED" ? (
        <p className="mt-3 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-800">
          停牌中（推断）：今天没开盘，现价沿用停牌前收盘，不是程序坏了。{suspension.reason ? `（${suspension.reason}）` : ""}
        </p>
      ) : null}

      {strategy ? <p className="mt-4 rounded-lg bg-muted/40 p-3 text-sm leading-6">{strategy.summary}</p> : null}

      <div className="mt-4 space-y-3">
        <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 rounded-lg border border-border p-3">
          <span className="text-sm"><span className="text-xs text-muted-foreground">现价：</span><strong className="text-base tabular-nums">{zones?.current_price != null ? formatNumber(zones.current_price) : "资料不足"}</strong></span>
          <span className="text-xs text-muted-foreground">行情日期：{strategy?.freshness.market_price_as_of || zones?.as_of || "资料不足"}</span>
          {zones ? <span className="text-sm font-medium">{position}</span> : null}
        </div>
        {zones ? (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            <article className="rounded-lg border border-border p-3">
              <div className="text-xs font-medium">合理价值带</div>
              <p className="mt-0.5 text-[11px] leading-4 text-muted-foreground">估算这家公司大概值多少钱的价格范围。</p>
              {fairBandReady ? (
                <div className="mt-2 text-sm tabular-nums">
                  <div>{formatNumber(valuation!.fair_value_low)}–{formatNumber(valuation!.fair_value_high)}</div>
                  <div className="text-xs text-muted-foreground">中枢 {formatNumber(valuation!.fair_value_mid)}</div>
                </div>
              ) : (
                <p className="mt-2 text-sm text-muted-foreground">{valuation?.message || "合理价值带暂不可用。"}</p>
              )}
            </article>
            {band("关注带", "价值区间与历史支撑重叠的带", zones.confluence_zones.slice(0, 2), "当前没有形成可解释的关注带。")}
            {band("观察带", "历史上多次止跌的价格带（备援）", zones.support_zones.slice(0, 2), zones.data_quality.daily_history.message || "历史日线不足，暂不计算观察带。")}
            {band("复核带", "估值偏高且与历史压力重叠，需要复核", zones.upper_review_zones.slice(0, 2), "当前没有需要复核的上方重叠区。")}
          </div>
        ) : (
          <p className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-800">价格带暂不可用{state.zonesError ? `：${state.zonesError}` : ""}</p>
        )}
      </div>

      <div className="mt-4 rounded-lg border border-border p-3">
        <div className="text-xs font-medium text-muted-foreground">阻断与降级原因</div>
        {cautionLines.length ? (
          <ul className="mt-2 space-y-1 text-sm leading-6">{[...new Set(cautionLines)].map((line, index) => <li key={`${index}-${line}`}>· {line}</li>)}</ul>
        ) : (
          <p className="mt-2 text-sm text-muted-foreground">当前无额外警示。</p>
        )}
      </div>

      <details className="mt-4 rounded-lg border border-border">
        <summary className="cursor-pointer px-3 py-2 text-xs font-medium">计算明细，不是主结论</summary>
        <div className="space-y-3 border-t p-3 text-xs leading-5 text-muted-foreground">
          {strategy ? (
            <>
              <div>价格关注原始档：{attention?.raw_level}（{attention?.raw_label}）；有效档位 {attention?.effective_status}。</div>
              {attention?.reasons.length ? <div>入场研究条件代码：{attention.reasons.join("、")}</div> : null}
              <div>估值可靠性：{reliability?.label}；可比同行样本 {reliability?.peer_sample_count ?? "资料不足"} 家{reliability?.reasons.length ? `；${reliability.reasons.join("；")}` : ""}</div>
              <div>价格结构：最近K线 {structure?.last_bar_date || "资料不足"}；现价行情 {structure?.current_quote_date || "资料不足"}；间隔 {structure?.gap_calendar_days ?? "?"} 个自然日（{structure?.gap_semantics || "口径未知"}）。</div>
              <div>研究结论版本 {strategy.formula_version}{zones ? `；价格带版本 ${zones.formula_version}` : ""}。</div>
            </>
          ) : null}
          {metrics ? (
            <div className="grid gap-2 sm:grid-cols-3">
              {([["PE", "pe_ttm"], ["PB", "pb_mrq"], ["股息率", "dividend_yield"]] as const).map(([label, key]) => {
                const metric = metrics[key];
                return (
                  <div key={key} className="rounded border border-border/60 p-2">
                    <div>{label}（计算明细）</div>
                    {metric?.status === "READY"
                      ? <div>{formatNumber(metric.current)} · 历史分位 {Math.round(metric.percentile ?? 0)}% · {metric.plain}</div>
                      : <div>历史估值数据不足，暂时不能判断。</div>}
                  </div>
                );
              })}
            </div>
          ) : null}
          {valuation?.methods.length ? <div>估值方法：{valuation.methods.map((method) => `${method.name}${method.peer_count ? `（可比 ${method.peer_count} 家）` : ""}`).join("；")}。</div> : null}
          {zones ? <div>历史估值：{zones.data_quality.historical_valuation.message}</div> : null}
          {zones ? <div>PE/PB 仅使用交易日已公告的财务数据；股息率以通达信除权日作为保守可见日期。</div> : null}
          {zones ? (
            <details className="rounded border border-border/60">
              <summary className="cursor-pointer px-2 py-1.5">支撑、压力与重合区域明细</summary>
              <div className="space-y-2 border-t p-2">
                {([["历史主要支撑", zones.support_zones], ["历史主要压力", zones.resistance_zones], ["价值与支撑重叠关注区", zones.confluence_zones], ["偏高估值与压力重叠区", zones.upper_review_zones]] as const).map(([title, rows]) => (
                  <div key={title}>
                    <div className="font-medium">{title}</div>
                    {rows.length ? rows.slice(0, 3).map((zone, index) => (
                      <div key={`${title}-${index}`} className="tabular-nums">{zoneText(zone)}{zone.reasons?.length ? ` · ${zone.reasons.slice(0, 2).join("；")}` : ""}</div>
                    )) : <div>暂无。</div>}
                  </div>
                ))}
              </div>
            </details>
          ) : null}
        </div>
      </details>
    </section>
  );
}
