import { useCallback, useEffect, useMemo, useState } from "react";
import { Activity, CheckCircle2, CircleAlert, Clock3, Database, Loader2, RefreshCw, Server } from "lucide-react";
import { toast } from "sonner";
import { api, type TdxRecord, type TdxStatus, type ValueDataStatus, type ValueRefreshModule } from "@/lib/api";
import { cn } from "@/lib/utils";
import { PageHeader, WorkspacePage, formatNumber } from "@/components/workspace/WorkspaceUI";

function statusLabel(status: string) {
  if (status === "ready") return "已更新";
  if (status === "running") return "更新中";
  if (status === "failed") return "失败";
  if (status === "partial") return "部分缓存";
  return "未更新";
}

function formatTime(value?: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

export function DataCenter() {
  const [data, setData] = useState<TdxStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [starting, setStarting] = useState("");
  const [error, setError] = useState("");
  const [dataset, setDataset] = useState("quotes");
  const [records, setRecords] = useState<TdxRecord[]>([]);
  const [recordTotal, setRecordTotal] = useState(0);
  const [recordLoading, setRecordLoading] = useState(false);
  const [recordQuery, setRecordQuery] = useState("");
  const [valueData, setValueData] = useState<ValueDataStatus | null>(null);
  const [valueStarting, setValueStarting] = useState<ValueRefreshModule | "">("");

  const loadValue = useCallback(async () => {
    try { setValueData(await api.getValueDataStatus()); }
    catch { setValueData(null); }
  }, []);

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      const next = await api.getTdxStatus();
      setData(next);
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法读取通达信数据状态");
    } finally {
      if (!quiet) setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); void loadValue(); }, [load, loadValue]);
  useEffect(() => {
    if (!data?.active_job || !["queued", "running"].includes(data.active_job.status)) return;
    const timer = window.setInterval(() => void load(true), 1200);
    return () => window.clearInterval(timer);
  }, [data?.active_job?.id, data?.active_job?.status, load]);
  const valueActive = valueData?.recent_jobs?.find((item) => ["queued", "running"].includes(item.status));
  useEffect(() => {
    if (!valueActive) return;
    const timer = window.setInterval(() => void loadValue(), 1500);
    return () => window.clearInterval(timer);
  }, [loadValue, valueActive?.id, valueActive?.status]);

  const busy = Boolean(data?.active_job && ["queued", "running"].includes(data.active_job.status));
  const readyCount = useMemo(() => data?.modules.filter((item) => item.status === "ready").length ?? 0, [data]);
  const totalItems = useMemo(() => data?.modules.reduce((sum, item) => sum + (item.item_count || 0), 0) ?? 0, [data]);
  const quoteCount = useMemo(() => data?.modules.find((item) => item.code === "quote")?.item_count ?? 0, [data]);
  const financeModule = useMemo(() => data?.modules.find((item) => item.code === "fundamental"), [data]);
  const financeCount = financeModule?.item_count ?? 0;
  const financeCoverage = typeof financeModule?.metadata?.coverage_pct === "number"
    ? financeModule.metadata.coverage_pct
    : 0;

  const loadRecords = useCallback(async (target = dataset, query = recordQuery) => {
    setRecordLoading(true);
    try {
      const result = await api.getTdxData(target, { query, limit: 100 });
      setRecords(result.items);
      setRecordTotal(result.total);
    } catch {
      setRecords([]);
      setRecordTotal(0);
    } finally {
      setRecordLoading(false);
    }
  }, [dataset, recordQuery]);

  useEffect(() => { void loadRecords(dataset, ""); }, [dataset]); // eslint-disable-line react-hooks/exhaustive-deps

  const update = async (module: string) => {
    setStarting(module);
    try {
      const job = await api.startTdxUpdate(module);
      toast.success(module === "all" ? "全量更新已启动" : "模块更新已启动");
      setData((current) => current ? { ...current, active_job: job } : current);
      void load(true);
    } catch (reason) {
      toast.error(reason instanceof Error ? reason.message : "启动更新失败");
    } finally {
      setStarting("");
    }
  };

  const updateValue = async (module: ValueRefreshModule) => {
    setValueStarting(module);
    try {
      const job = await api.startValueRefresh([module], valueData?.latest_score_as_of || undefined);
      setValueData((current) => current ? { ...current, recent_jobs: [job, ...current.recent_jobs] } : current);
      toast.success(module === "all" ? "价值线全部更新已启动" : "价值线模块更新已启动");
    } catch (reason) {
      toast.error(reason instanceof Error ? reason.message : "价值线更新启动失败");
    } finally { setValueStarting(""); }
  };

  return (
    <WorkspacePage>
      <PageHeader
        eyebrow="DATA / TONGDAXIN"
        title="通达信数据中心"
        description="统一管理A股行情、榜单、指数、板块、财务、基金、公式与历史数据。所有接口只读，不连接交易、持仓或资产。"
        actions={
          <button
            onClick={() => void update("all")}
            disabled={busy || Boolean(starting)}
            className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-sm hover:opacity-90 disabled:opacity-50"
          >
            <RefreshCw className={cn("h-4 w-4", (busy || starting === "all") && "animate-spin")} />
            一键更新全部
          </button>
        }
      />

      {loading && !data ? (
        <div className="flex min-h-56 items-center justify-center gap-2 rounded-xl border border-dashed text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />正在读取数据中心…</div>
      ) : null}

      {error ? <div className="rounded-xl border border-danger/30 bg-danger/5 p-4 text-sm text-danger">{error}</div> : null}

      <ValueLineDataPanel data={valueData} active={valueActive} starting={valueStarting} onUpdate={updateValue} />

      {data ? (
        <>
          <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <article className="rounded-xl border bg-card p-4 shadow-sm"><div className="flex items-center justify-between text-xs text-muted-foreground"><span>客户端</span><Server className="h-4 w-4" /></div><div className={cn("mt-3 text-xl font-semibold", data.client_process_running ? "text-success" : "text-danger")}>{data.client_process_running ? "已登录运行" : "未检测到"}</div><div className="mt-1 truncate text-xs text-muted-foreground" title={data.tdx_home}>{data.tdx_home}</div></article>
            <article className="rounded-xl border bg-card p-4 shadow-sm"><div className="flex items-center justify-between text-xs text-muted-foreground"><span>已就绪模块</span><CheckCircle2 className="h-4 w-4" /></div><div className="mt-3 text-2xl font-semibold">{readyCount} / {data.modules.length}</div><div className="mt-1 text-xs text-muted-foreground">失败不会覆盖上次成功缓存</div></article>
            <article className="rounded-xl border bg-card p-4 shadow-sm"><div className="flex items-center justify-between text-xs text-muted-foreground"><span>缓存记录</span><Database className="h-4 w-4" /></div><div className="mt-3 text-2xl font-semibold">{formatNumber(totalItems, 0)}</div><div className="mt-1 text-xs text-muted-foreground">各模块主数据记录合计</div></article>
            <article className="rounded-xl border bg-card p-4 shadow-sm"><div className="flex items-center justify-between text-xs text-muted-foreground"><span>当前任务</span><Activity className="h-4 w-4" /></div><div className="mt-3 text-lg font-semibold">{data.active_job?.message || "空闲"}</div><div className="mt-1 text-xs text-muted-foreground">{data.active_job ? `${data.active_job.progress}/${data.active_job.total}` : "可以启动更新"}</div></article>
          </section>

          <section className="grid gap-3 md:grid-cols-3">
            <article className="rounded-xl border bg-card p-4 text-sm shadow-sm"><div className="text-xs text-muted-foreground">行情覆盖率</div><div className="mt-2 text-xl font-semibold">{quoteCount ? "100%" : "0%"}</div><div className="mt-1 text-xs text-muted-foreground">有效缓存 {formatNumber(quoteCount, 0)} 条</div></article>
            <article className="rounded-xl border bg-card p-4 text-sm shadow-sm"><div className="text-xs text-muted-foreground">财务估值覆盖率</div><div className="mt-2 text-xl font-semibold">{`${financeCoverage.toFixed(2)}%`}</div><div className="mt-1 text-xs text-muted-foreground">已缓存 {formatNumber(financeCount, 0)} 只证券；覆盖率按证券列表计算，缺失值不以0替代</div></article>
            <article className="rounded-xl border bg-card p-4 text-sm shadow-sm"><div className="text-xs text-muted-foreground">统一单位</div><div className="mt-2 font-semibold">北京时间 · 元 / 万元 / 亿元</div><div className="mt-1 text-xs text-muted-foreground">快照成交量为手；K线成交量按接口原始股数展示</div></article>
          </section>

          {busy && data.active_job ? (
            <section className="rounded-xl border border-primary/25 bg-primary/5 p-4">
              <div className="flex items-center justify-between gap-4 text-sm"><span className="inline-flex items-center gap-2 font-medium"><Loader2 className="h-4 w-4 animate-spin" />{data.active_job.message}</span><span className="font-mono text-xs text-muted-foreground">{data.active_job.progress}/{data.active_job.total}</span></div>
              <div className="mt-3 h-2 overflow-hidden rounded-full bg-muted"><div className="h-full rounded-full bg-primary transition-all" style={{ width: `${data.active_job.total ? data.active_job.progress / data.active_job.total * 100 : 3}%` }} /></div>
            </section>
          ) : null}

          <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {data.modules.map((module) => {
              const progress = module.total ? Math.min(100, module.progress / module.total * 100) : 0;
              return (
                <article key={module.code} className="flex min-h-56 flex-col rounded-xl border bg-card p-5 shadow-sm">
                  <div className="flex items-start justify-between gap-3">
                    <div><h2 className="font-semibold">{module.label}</h2><p className="mt-1 text-xs leading-5 text-muted-foreground">{module.description}</p></div>
                    <span className={cn("rounded-full px-2 py-1 text-[11px] font-medium", module.status === "ready" && "bg-success/10 text-success", module.status === "running" && "bg-primary/10 text-primary", module.status === "partial" && "bg-warning/10 text-warning", module.status === "failed" && "bg-danger/10 text-danger", !["ready", "running", "partial", "failed"].includes(module.status) && "bg-muted text-muted-foreground")}>{statusLabel(module.status)}</span>
                  </div>
                  <div className="mt-4 flex items-end justify-between"><div><div className="text-2xl font-semibold">{formatNumber(module.item_count || 0, 0)}</div><div className="text-[11px] text-muted-foreground">主记录</div></div><div className="text-right text-[11px] text-muted-foreground"><Clock3 className="mr-1 inline h-3 w-3" />{formatTime(module.updated_at)}</div></div>
                  {module.status === "running" ? <div className="mt-3"><div className="h-1.5 overflow-hidden rounded-full bg-muted"><div className="h-full bg-primary transition-all" style={{ width: `${Math.max(2, progress)}%` }} /></div><div className="mt-1 text-[11px] text-muted-foreground">{module.message}</div></div> : null}
                  {module.error ? <div className="mt-3 line-clamp-2 text-xs text-danger"><CircleAlert className="mr-1 inline h-3.5 w-3.5" />{module.error}</div> : null}
                  <div className="mt-3 flex flex-wrap gap-1.5">{Object.entries(module.metadata || {}).slice(0, 5).map(([key, value]) => <span key={key} className="rounded bg-muted px-2 py-1 text-[10px] text-muted-foreground">{key} {typeof value === "number" ? formatNumber(value, 2) : String(value)}</span>)}</div>
                  <div className="mt-auto pt-4"><button onClick={() => void update(module.code)} disabled={busy || Boolean(starting)} className="inline-flex w-full items-center justify-center gap-2 rounded-lg border px-3 py-2 text-sm font-medium hover:bg-muted disabled:opacity-50"><RefreshCw className={cn("h-3.5 w-3.5", (starting === module.code || module.status === "running") && "animate-spin")} />单独更新{module.label}</button></div>
                </article>
              );
            })}
          </section>

          <details className="rounded-xl border bg-card shadow-sm">
            <summary className="cursor-pointer list-none p-5"><div className="text-xs font-semibold text-primary">ADVANCED DIAGNOSTICS</div><div className="mt-1 font-semibold">高级诊断：缓存数据浏览</div><p className="mt-1 text-xs text-muted-foreground">仅用于排查字段、缓存和数据源问题；业务查询请使用市场、筛选和公司详情页面。</p></summary>
            <div className="border-t">
              <DataBrowser
                dataset={dataset}
                setDataset={setDataset}
                records={records}
                total={recordTotal}
                loading={recordLoading}
                query={recordQuery}
                setQuery={setRecordQuery}
                onSearch={() => void loadRecords()}
              />
            </div>
          </details>

          <section className="rounded-xl border bg-card p-5 shadow-sm">
            <h2 className="font-semibold">更新说明</h2>
            <div className="mt-3 grid gap-3 text-sm text-muted-foreground md:grid-cols-2">
              <p>行情、榜单、指数、基金和公式通常较快；板块需要读取587个板块的成分，耗时稍长。</p>
              <p>财务估值按全市场逐只读取，运行时间最长。更新过程中已有缓存始终可查询。</p>
              <p>个股分红、股本、日内微观统计和K线通过个股详情接口按需更新并缓存。</p>
              <p>专业三大报表依赖 <code className="rounded bg-muted px-1">vipdoc/cw/gpcw*.dat</code>，没有数据包时会明确标记不可用。</p>
            </div>
          </section>
        </>
      ) : null}
    </WorkspacePage>
  );
}

function ValueLineDataPanel({ data, active, starting, onUpdate }: {
  data: ValueDataStatus | null;
  active?: ValueDataStatus["recent_jobs"][number];
  starting: ValueRefreshModule | "";
  onUpdate: (module: ValueRefreshModule) => Promise<void>;
}) {
  const busy = Boolean(active) || Boolean(starting);
  return <section className="rounded-xl border border-primary/20 bg-card shadow-sm">
    <div className="flex flex-wrap items-start justify-between gap-3 border-b p-5">
      <div><div className="text-xs font-semibold text-primary">VALUE LINE DATA</div><h2 className="mt-1 text-xl font-semibold">价值线数据</h2><p className="mt-1 text-sm text-muted-foreground">固定顺序：专业财务 → 历史行情 → 宏观 → 政策 → 评分。页面查询只读缓存，模块失败保留上次成功结果。</p></div>
      <button onClick={() => void onUpdate("all")} disabled={busy} className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"><RefreshCw className={cn("h-4 w-4", (starting === "all" || active?.status === "running") && "animate-spin")} />全部更新</button>
    </div>
    <div className="grid gap-3 p-5 md:grid-cols-2 xl:grid-cols-5">{data?.modules.map((module) => {
      const progress = module.total ? Math.min(100, module.progress / module.total * 100) : 0;
      const coverage = typeof module.metadata?.coverage === "number" ? module.metadata.coverage : null;
      return <article key={module.code} className="flex min-h-44 flex-col rounded-lg border p-4">
      <div className="flex items-start justify-between gap-2"><div><div className="font-medium">{module.label}</div><div className="mt-1 text-xs text-muted-foreground">{statusLabel(module.status)}</div></div><span className={cn("h-2.5 w-2.5 rounded-full", module.status === "ready" ? "bg-success" : module.status === "partial" ? "bg-warning" : module.status === "failed" ? "bg-danger" : module.status === "running" ? "bg-primary animate-pulse" : "bg-muted-foreground/30")} /></div>
      <div className="mt-3 text-xs text-muted-foreground">最近成功：{formatTime(module.last_success_at || module.updated_at)}</div>
      <div className="mt-1 text-xs text-muted-foreground">记录 {formatNumber(module.item_count || 0, 0)}{coverage !== null ? ` · 覆盖 ${(coverage * 100).toFixed(1)}%` : ""}</div>
      {module.status === "running" ? <div className="mt-2"><div className="h-1.5 overflow-hidden rounded-full bg-muted"><div className="h-full bg-primary transition-all" style={{ width: `${Math.max(2, progress)}%` }} /></div><div className="mt-1 text-[11px] text-muted-foreground">{module.message || `${module.progress}/${module.total}`}</div></div> : null}
      {module.error ? <div className="mt-2 line-clamp-2 text-xs text-danger">{module.error}</div> : null}
      <button onClick={() => void onUpdate(module.code)} disabled={busy} className="mt-auto inline-flex items-center justify-center gap-1.5 rounded-lg border px-3 py-2 text-xs font-medium hover:bg-muted disabled:opacity-50"><RefreshCw className={cn("h-3.5 w-3.5", starting === module.code && "animate-spin")} />单独更新</button>
    </article>})}</div>
    <div className="grid gap-3 border-t p-5 text-sm md:grid-cols-3">
      <div className="rounded-lg bg-muted/40 p-3"><div className="text-xs text-muted-foreground">专业财务包</div><div className="mt-1 font-medium">{data?.professional_finance.status === "ready" ? "可用" : "需要下载"} · {data?.professional_finance.file_count || 0} 个报告期</div><div className="mt-1 text-xs text-muted-foreground">{data?.professional_finance.first_period || "—"} 至 {data?.professional_finance.last_period || "—"}</div></div>
      <div className="rounded-lg bg-muted/40 p-3"><div className="text-xs text-muted-foreground">最近评分日期</div><div className="mt-1 font-medium">{data?.latest_score_as_of || "尚未生成"}</div><div className="mt-1 text-xs text-muted-foreground">完整性不足不会覆盖旧评分缓存</div></div>
      <div className="rounded-lg bg-muted/40 p-3"><div className="text-xs text-muted-foreground">可选定时模板（默认关闭）</div><div className="mt-1 font-medium">{data?.schedule_template?.name || "价值线工作日收盘后更新"}</div><div className="mt-1 font-mono text-xs text-muted-foreground">{data?.schedule_template?.cron || "0 17 * * 1-5"} · Asia/Shanghai</div></div>
    </div>
    {active ? <div className="border-t bg-primary/5 px-5 py-3 text-sm"><span className="inline-flex items-center gap-2 font-medium"><Loader2 className="h-4 w-4 animate-spin" />正在更新 {active.current_module || "准备中"}</span><span className="ml-3 text-xs text-muted-foreground">{active.progress}/{active.total}</span></div> : null}
  </section>;
}

const DATASETS = [
  ["quotes", "实时行情"], ["ranks", "榜单"], ["indices", "指数"], ["sectors", "板块"],
  ["fundamentals", "财务估值"], ["funds", "基金债券"], ["formulas", "公式"],
  ["ipo", "新股申购"], ["history_availability", "历史状态"],
  ["financial_history", "专业财务历史"], ["value_sector_scores_v2", "价值行业 V2"], ["value_leader_scores_v2", "价值龙头 V2"],
] as const;

function value(payload: Record<string, unknown>, ...keys: string[]) {
  for (const key of keys) {
    const item = payload[key];
    if (item !== undefined && item !== null && item !== "") return item;
  }
  return "—";
}

function pct(item: unknown) {
  const number = typeof item === "number" ? item : Number(item);
  if (!Number.isFinite(number)) return "—";
  return `${number >= 0 ? "+" : ""}${number.toFixed(2)}%`;
}

function DataBrowser({ dataset, setDataset, records, total, loading, query, setQuery, onSearch }: {
  dataset: string;
  setDataset: (value: string) => void;
  records: TdxRecord[];
  total: number;
  loading: boolean;
  query: string;
  setQuery: (value: string) => void;
  onSearch: () => void;
}) {
  return (
    <section className="overflow-hidden rounded-xl border bg-card shadow-sm">
      <div className="border-b p-5">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div><div className="text-xs font-semibold text-primary">DATA EXPLORER</div><h2 className="mt-1 text-xl font-semibold">缓存数据浏览</h2><p className="mt-1 text-xs text-muted-foreground">当前数据集 {formatNumber(total, 0)} 条，最多展示前100条。</p></div>
          <div className="flex gap-2"><input value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => event.key === "Enter" && onSearch()} placeholder="代码或名称" className="w-44 rounded-lg border bg-background px-3 py-2 text-sm outline-none focus:border-primary" /><button onClick={onSearch} className="rounded-lg border px-3 py-2 text-sm font-medium hover:bg-muted">搜索</button></div>
        </div>
        <div className="mt-4 flex flex-wrap gap-2">{DATASETS.map(([code, label]) => <button key={code} onClick={() => { setDataset(code); setQuery(""); }} className={cn("rounded-full border px-3 py-1.5 text-xs font-medium transition", dataset === code ? "border-primary bg-primary text-primary-foreground" : "hover:bg-muted")}>{label}</button>)}</div>
      </div>
      {loading ? <div className="flex min-h-48 items-center justify-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />正在查询缓存…</div> : records.length ? (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px] text-left text-sm">
            <thead className="bg-muted/50 text-xs text-muted-foreground"><tr><th className="px-4 py-3">类别</th><th className="px-4 py-3">代码 / 名称</th><th className="px-4 py-3">现价</th><th className="px-4 py-3">涨跌幅 / 排名</th><th className="px-4 py-3">核心数据</th><th className="px-4 py-3">补充信息</th></tr></thead>
            <tbody className="divide-y">{records.map((record) => {
              const item = record.payload;
              const change = value(item, "change_pct", "ZAF");
              const code = value(item, "code", "Code", "acCode");
              const name = value(item, "name", "Name", "acName");
              return <tr key={`${record.dataset}:${record.key}`} className="hover:bg-muted/30"><td className="px-4 py-3 text-xs text-muted-foreground">{record.category || "—"}</td><td className="px-4 py-3"><div className="font-medium">{String(name)}</div><div className="font-mono text-xs text-muted-foreground">{String(code)}</div></td><td className="px-4 py-3 font-mono">{String(value(item, "price", "Now", "NowPrice"))}</td><td className={cn("px-4 py-3 font-mono", Number(change) > 0 && "text-market-up", Number(change) < 0 && "text-market-down")}>{change !== "—" ? pct(change) : `#${String(value(item, "rank"))}`}</td><td className="px-4 py-3 text-xs">{dataset === "fundamentals" ? `PE ${String(value(item, "pe_ttm", "pe_dynamic"))} · PB ${String(value(item, "pb_mrq"))}` : dataset === "sectors" ? `成分 ${String(value(item, "member_count"))}` : dataset === "quotes" ? `成交量 ${String(value(item, "volume_lots"))}手` : String(value(item, "main_business", "SGDate", "period", "member_count"))}</td><td className="max-w-xs truncate px-4 py-3 text-xs text-muted-foreground" title={String(value(item, "main_business", "path", "description"))}>{String(value(item, "main_business", "path", "ReportDate", "data_as_of", "isSys"))}</td></tr>;
            })}</tbody>
          </table>
        </div>
      ) : <div className="flex min-h-48 flex-col items-center justify-center text-sm text-muted-foreground"><Database className="mb-2 h-6 w-6" />该数据集尚未更新，点击上方对应模块的“单独更新”。</div>}
    </section>
  );
}
