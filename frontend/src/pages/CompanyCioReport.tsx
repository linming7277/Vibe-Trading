/**
 * 公司研究页（价值投资 → 公司研究）：输入公司代码或名称，展示最新 CIO 深度报告全文（19 节）。
 * 数据源：/api/research/cio/company-search + /api/research/cio/{stock_code}。只读，零 LLM 触发。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Bot, Loader2, Search } from "lucide-react";
import { FinancialAgentFloat } from "@/components/value/FinancialAgentFloat";
import ReactMarkdown, { type Options as ReactMarkdownOptions } from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, type CompanyCioReport, type CompanySearchResult, type TdxSecurityOverview } from "@/lib/api";
import { CandlestickChart } from "@/components/charts/CandlestickChart";
import { PageHeader, WorkspacePage } from "@/components/workspace/WorkspaceUI";
import { cn } from "@/lib/utils";

function ProfitScenarioChart({ payload }: { payload: Record<string, unknown> }) {
  const W = 560, H = 220, PAD_L = 46, PAD_R = 16, PAD_T = 14, PAD_B = 30;
  const baseYear = String(payload.base_year || "");
  const baseProfit = typeof payload.base_profit === "number" ? payload.base_profit / 1e8 : null;
  const SCENARIOS: Array<{ key: string; color: string }> = [
    { key: "BEAR", color: "#2563eb" },
    { key: "BASE", color: "#7c3aed" },
    { key: "BULL", color: "#d97706" },
  ];
  const scenarios = (payload.scenarios || {}) as Record<string, { label?: string; forecast?: Array<{ year?: string; net_profit?: number }> }>;
  const lines = SCENARIOS.map(({ key, color }) => {
    const sc = scenarios[key];
    const label = String(sc?.label || key);
    const points = (sc?.forecast || [])
      .map((row) => ({ year: String(row.year || "").replace(/E$/, ""), value: typeof row.net_profit === "number" ? row.net_profit / 1e8 : null }))
      .filter((p) => p.value != null) as Array<{ year: string; value: number }>;
    if (baseProfit != null) points.unshift({ year: baseYear, value: baseProfit });
    return { label, color, points };
  }).filter((line) => line.points.length >= 2);

  if (!lines.length) return null;
  const all = lines.flatMap((line) => line.points.map((p) => p.value));
  const min = Math.min(...all), max = Math.max(...all);
  const span = max - min || 1;
  const years = [...new Set(lines.flatMap((l) => l.points.map((p) => p.year)))].sort();
  const xOf = (year: string) => {
    const idx = years.indexOf(year);
    return PAD_L + (idx / Math.max(1, years.length - 1)) * (W - PAD_L - PAD_R);
  };
  const yOf = (v: number) => PAD_T + (1 - (v - min) / span) * (H - PAD_T - PAD_B);
  const yLabel = (v: number) => `${v.toFixed(1)}亿`;

  return (
    <div className="mt-3 rounded-lg border bg-muted/20 p-3" onClick={(e) => e.stopPropagation()}>
      <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
        <span className="text-xs font-medium">净利三情景走势（亿）</span>
        <span className="flex gap-2 text-[11px] text-muted-foreground">
          {lines.map((line) => (
            <span key={line.label} className="inline-flex items-center gap-1">
              <span className="inline-block h-0.5 w-4" style={{ background: line.color }} />{line.label}
            </span>
          ))}
        </span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="h-44 w-full">
        {[min, (min + max) / 2, max].map((v, i) => (
          <g key={i}>
            <line x1={PAD_L} x2={W - PAD_R} y1={yOf(v)} y2={yOf(v)} stroke="currentColor" strokeOpacity={0.12} />
            <text x={PAD_L - 6} y={yOf(v) + 3} textAnchor="end" fontSize={9} fill="currentColor" opacity={0.6}>{yLabel(v)}</text>
          </g>
        ))}
        {years.map((year: string) => (
          <text key={year} x={xOf(year)} y={H - 10} textAnchor="middle" fontSize={9} fill="currentColor" opacity={0.6}>{year}</text>
        ))}
        {lines.map((line) => (
          <polyline key={line.label} fill="none" stroke={line.color} strokeWidth={1.8}
            points={line.points.map((p) => `${xOf(p.year).toFixed(1)},${yOf(p.value).toFixed(1)}`).join(" ")} />
        ))}
        {lines.map((line) => line.points.map((p, i: number) => (
          <circle key={`${line.label}-${i}`} cx={xOf(p.year)} cy={yOf(p.value)} r={2.4} fill={line.color} />
        )))}
      </svg>
      <p className="mt-1 text-[11px] text-muted-foreground">三条线均从基年 {baseYear} 实际值出发，向上为情景更乐观方向；仅为系统推演，不构成预测承诺。</p>
    </div>
  );
}

function toText(node: unknown): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(toText).join("");
  return "";
}

function asDict(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

function asRows(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

/** 与公司研究页同一转换：TDX overview 的 klines（宽表按代码取列）→ 蜡烛数据。 */
function klineBars(data: TdxSecurityOverview | null, symbol: string, period: string, dividendType: string) {
  if (!data) return [];
  const item = (data as unknown as { klines?: Array<Record<string, unknown>> })
    .klines?.find((row) => row.period === period && row.dividend_type === dividendType);
  if (!item) return [];
  const source = asDict(item.data);
  const rows = (name: string): Array<Record<string, unknown>> => asRows(source[name]) as Array<Record<string, unknown>>;
  const map = (name: string) => new Map(rows(name).map((row: Record<string, unknown>) => [String(row.index), Number(row[symbol])]));
  const open = map("Open"), high = map("High"), low = map("Low"), close = map("Close"), volume = map("Volume");
  return [...close.keys()].sort().map((time) => ({
    time: time.slice(0, 10),
    open: open.get(time) ?? 0,
    high: high.get(time) ?? 0,
    low: low.get(time) ?? 0,
    close: close.get(time) ?? 0,
    volume: volume.get(time) ?? 0,
  })).filter((bar) => bar.close > 0);
}

export function CompanyCioReportPage() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<CompanySearchResult[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [report, setReport] = useState<CompanyCioReport | null>(null);
  const [loadingReport, setLoadingReport] = useState(false);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<CompanySearchResult | null>(null);
  const [focusA, setFocusA] = useState<CompanySearchResult[] | null>(null);
  const [agentOpen, setAgentOpen] = useState(false);
  const [overview, setOverview] = useState<TdxSecurityOverview | null>(null);
  const timer = useRef<number | null>(null);

  const titleToId = useMemo(() => {
    const map: Record<string, string> = {};
    for (const section of report?.sections || []) map[section.title] = `cio-${section.section_type}`;
    return map;
  }, [report]);

  const REPORT_MARKDOWN = useMemo<ReactMarkdownOptions>(() => ({
    remarkPlugins: [remarkGfm],
    components: {
      h1: ({ children }) => <h2 className="mt-6 border-b pb-1 text-xl font-semibold">{children}</h2>,
      h2: ({ children }) => {
        const label = toText(children).trim();
        const id = titleToId[label];
        return <h2 id={id} className="mt-6 scroll-mt-20 border-b pb-1.5 text-lg font-semibold first:mt-0">{children}</h2>;
      },
      h3: ({ children }) => <h4 className="mt-4 text-base font-semibold">{children}</h4>,
      p: ({ children }) => <p className="mt-2 text-sm leading-7">{children}</p>,
      ul: ({ children }) => <ul className="mt-2 list-disc space-y-1 pl-5 text-sm leading-6">{children}</ul>,
      ol: ({ children }) => <ol className="mt-2 list-decimal space-y-1 pl-5 text-sm leading-6">{children}</ol>,
      table: ({ children }) => <div className="mt-3 overflow-x-auto"><table className="w-full min-w-[560px] text-xs">{children}</table></div>,
      th: ({ children }) => <th className="border-b bg-muted/40 px-2.5 py-2 text-left font-medium">{children}</th>,
      td: ({ children }) => <td className="border-b px-2.5 py-2 align-top tabular-nums">{children}</td>,
      blockquote: ({ children }) => <blockquote className="mt-2 border-l-2 border-primary/40 pl-3 text-sm text-muted-foreground">{children}</blockquote>,
    },
  }), [titleToId]);

  const runSearch = useCallback(async (q: string) => {
    if (!q.trim()) { setResults(null); return; }
    setSearching(true);
    try {
      const data = await api.searchCompanies(q.trim());
      setResults(data);
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "搜索失败");
      setResults([]);
    } finally {
      setSearching(false);
    }
  }, []);

  useEffect(() => {
    api.getFocusSelection()
      .then((data) => setFocusA((data.A || []).map((item) => ({
        stock_code: item.stock_code,
        company_name: item.company_name,
        focus_tier: "重点研究",
        in_pool: true,
      }))))
      .catch(() => setFocusA([]));
  }, []);
  useEffect(() => {
    if (timer.current) window.clearTimeout(timer.current);
    if (!query.trim()) { setResults(null); return; }
    timer.current = window.setTimeout(() => { void runSearch(query); }, 350);
    return () => { if (timer.current) window.clearTimeout(timer.current); };
  }, [query, runSearch]);

  const openCompany = async (item: CompanySearchResult) => {
    setSelected(item);
    setResults(null);
    setQuery(item.company_name);
    setLoadingReport(true);
    setError("");
    try {
      const reportPromise = api.getCompanyCioReport(item.stock_code);
      // 单股 K 线缓存按需刷新（读本机通达信日线）；若首次后仍无 K 线，自动重试一次
      const refreshKline = () => api.getTdxKline({ symbol: item.stock_code, period: "1d", count: 120, dividend_type: "front" }).catch(() => undefined);
      const readOverview = () => api.getTdxSecurityOverview(item.stock_code).catch(() => null);
      try {
        await refreshKline();
        let overviewData = await readOverview();
        if (!klineBars(overviewData, item.stock_code, "1d", "front").length) {
          await refreshKline();
          overviewData = await readOverview();
        }
        setOverview(overviewData);
      } catch { /* K 线刷新失败不阻断报告 */ }
      setReport(await reportPromise);
    } catch (reason) {
      setReport(null);
      setError(reason instanceof Error ? reason.message : "读取报告失败");
    } finally {
      setLoadingReport(false);
    }
  };

  const selectedCompany = selected?.company_name || selected?.stock_code || "";

  return (
    <WorkspacePage>
      <PageHeader
        eyebrow="VALUE / COMPANY RESEARCH"
        title="公司研究"
        description="输入公司代码或名称，查看系统为它生成的最新 CIO 深度研究报告（19 节全文）。报告每晚为低估值龙头池自动更新。"
      />

      {focusA && focusA.length ? (
        <nav aria-label="重点研究公司" className="mb-4 flex flex-wrap items-center gap-2">
          <span className="text-xs font-semibold text-muted-foreground">重点研究：</span>
          {focusA.map((item) => (
            <button key={item.stock_code} type="button" onClick={() => void openCompany(item)}
              className={cn("rounded-full border px-3 py-1.5 text-sm transition-colors",
                selected?.stock_code === item.stock_code
                  ? "border-primary bg-primary text-primary-foreground"
                  : "border-border bg-card text-foreground hover:border-primary/50 hover:text-primary")}>
              {item.company_name}
            </button>
          ))}
        </nav>
      ) : null}

      <section className="rounded-xl border bg-card p-5 shadow-sm">
        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="输入公司名称或代码，如：江河集团 / 601886.SH / 600216"
            className="w-full rounded-lg border bg-background py-2.5 pl-9 pr-3 text-sm outline-none focus:border-primary/60"
          />
          {searching ? <Loader2 className="absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 animate-spin text-muted-foreground" /> : null}
        </div>
        {results && results.length ? (
          <div className="mt-3 divide-y rounded-lg border">
            {results.map((item) => (
              <button key={item.stock_code} type="button" onClick={() => void openCompany(item)}
                className="flex w-full items-center justify-between gap-3 px-4 py-2.5 text-left text-sm hover:bg-muted/30">
                <span className="min-w-0"><strong className="truncate">{item.company_name}</strong> <span className="ml-1 font-mono text-xs text-muted-foreground">{item.stock_code}</span></span>
                {item.focus_tier ? <span className="shrink-0 rounded-full bg-primary/10 px-2.5 py-1 text-[11px] font-medium text-primary">{item.focus_tier}</span> : <span className="shrink-0 rounded-full bg-muted px-2.5 py-1 text-[11px] text-muted-foreground">低估值龙头池外</span>}
              </button>
            ))}
          </div>
        ) : null}
        {results && !results.length && query.trim() ? (
          <p className="mt-3 text-sm text-muted-foreground">没有匹配的公司，换个关键词试试。</p>
        ) : null}
      </section>

      {error ? <div className="rounded-xl border border-danger/30 bg-danger/5 p-4 text-sm text-danger">{error}</div> : null}
      {loadingReport ? <div className="flex min-h-40 items-center justify-center gap-2 rounded-xl border border-dashed text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />正在读取 {selectedCompany} 的 CIO 报告…</div> : null}

      {selected ? (
        <section className="mt-4 rounded-xl border bg-card shadow-sm">
          <div className="flex items-baseline justify-between gap-3 border-b p-5">
            <h2 className="text-lg font-semibold">最近 K 线走势</h2>
            <span className="text-xs text-muted-foreground">约 6 个月日K · 前复权</span>
          </div>
          <div className="p-5">
            {(() => { const bars = klineBars(overview, selected.stock_code, "1d", "front"); return bars.length ? <CandlestickChart data={bars} height={260} compact /> : <div className="rounded-lg border border-dashed border-border p-4 text-sm text-muted-foreground">历史行情不足，暂无法展示走势。</div>; })()}
          </div>
        </section>
      ) : null}

      {report ? (
        <div className="grid grid-cols-1 gap-6 xl:grid-cols-[210px_minmax(0,1fr)]">
        <aside className="hidden xl:block">
          <div className="sticky top-4 max-h-[calc(100vh-2rem)] overflow-y-auto rounded-xl border bg-card p-3">
            <div className="mb-1.5 px-1 text-xs font-semibold text-muted-foreground">报告目录（点击直达）</div>
            <nav className="space-y-0.5">
              {(report.sections || []).map((section) => (
                <button key={section.section_type} type="button"
                  onClick={() => document.getElementById(`cio-${section.section_type}`)?.scrollIntoView({ behavior: "smooth", block: "start" })}
                  className="flex w-full items-center gap-2 rounded px-2 py-1 text-left text-xs text-muted-foreground hover:bg-muted hover:text-foreground">
                  <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full",
                    section.freshness_status === "FRESH" ? "bg-primary"
                    : section.freshness_status === "STALE" ? "bg-amber-500" : "bg-muted-foreground/40")} />
                  <span className="truncate">{section.title}</span>
                </button>
              ))}
            </nav>
          </div>
        </aside>
        <section className="min-w-0 rounded-xl border bg-card shadow-sm">
          <div className="flex flex-wrap items-baseline justify-between gap-3 border-b p-5">
            <div>
              <h2 className="text-xl font-semibold">CIO 深度研究报告 · {selectedCompany}</h2>
              <p className="mt-1 text-xs text-muted-foreground">研究基准日 {report.research_as_of} · 综合来源 {report.synthesis_source} · 19 节结构</p>
            </div>
            <button type="button" onClick={() => setAgentOpen(true)}
              className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:opacity-90">
              <Bot className="h-3.5 w-3.5" />问财报研究员
            </button>

          </div>
          <div className="divide-y divide-border/60">
            {(report.sections || []).map((section) => {
              const isForecast = section.section_type === "profit_forecast_detail";
              const chartPayload = isForecast
                ? ((section.structured_payload || {}) as Record<string, unknown>)
                : undefined;
              const dotCls =
                section.freshness_status === "REFRESHED" ? "bg-primary"
                : section.freshness_status === "REUSED" ? "bg-muted-foreground/30"
                : "bg-amber-400";
              return (
                <div key={section.section_type} id={`cio-${section.section_type}`} className="scroll-mt-20 px-5 py-4">
                  <div className="flex items-center justify-between gap-2">
                    <h3 className="text-sm font-semibold text-foreground">{section.title}</h3>
                    <span className={cn("inline-block h-1.5 w-1.5 shrink-0 rounded-full", dotCls)}
                      title={section.freshness_status} />
                  </div>
                  <div className="mt-1.5 text-sm leading-7 text-foreground/90">
                    <ReactMarkdown {...REPORT_MARKDOWN}>{section.narrative_md}</ReactMarkdown>
                  </div>
                  {isForecast && chartPayload ? (
                    <div className="mt-3 rounded-lg border bg-muted/10 p-3">
                      <ProfitScenarioChart payload={chartPayload} />
                    </div>
                  ) : null}
                </div>
              );
            })}
          </div>
          <div className="border-t bg-muted/20 px-5 py-3 text-xs leading-5 text-muted-foreground">
            左侧目录可点击直达对应章节；圆点颜色表示该节资料新鲜度。本报告由系统确定性数据综合生成，仅供研究参考，不构成任何操作建议。
          </div>
        </section>
        </div>
      ) : selected && !loadingReport && !error ? (
        <section className="rounded-xl border border-dashed bg-card p-8 text-center text-sm text-muted-foreground">
          {selectedCompany} 暂无 CIO 报告。报告每晚为低估值龙头池公司自动生成；池外公司暂不生成。
        </section>
      ) : null}

      {selected ? (
        <FinancialAgentFloat
          open={agentOpen}
          target={{ stock_code: selected.stock_code, stock_name: selected.company_name, as_of: report?.research_as_of ?? "", level3_name: "公司研究" }}
          onClose={() => setAgentOpen(false)}
        />
      ) : null}
    </WorkspacePage>
  );
}
