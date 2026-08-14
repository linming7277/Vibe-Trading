import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router";
import { Activity, ArrowRight, Bot, Clock3, Database, RefreshCw, ShieldCheck } from "lucide-react";
import { toast } from "sonner";
import { api, type EngineScore, type StrategyDashboard, type StrategySignal } from "@/lib/api";
import { EMPTY_STRATEGY_OBSERVATIONS, loadStrategyObservations, type StrategyObservations } from "@/lib/strategyObservations";
import { useWorkspaceMarket } from "@/hooks/useWorkspaceMarket";
import { EmptyState, LoadingState, MetricCard, PageHeader, ScoreBar, WorkspacePage } from "@/components/workspace/WorkspaceUI";
import { StrategyObservationPanel } from "@/components/workspace/StrategyObservations";
import { cn } from "@/lib/utils";

type StrategyLine = "value" | "emotion";

const VALUE_SECTIONS: Record<string, [string, string]> = {
  macro: ["宏观状态", "增长、流动性、通胀、政策和风险偏好的确定性状态。"],
  sectors: ["赛道机会", "在同市场、同行业体系内比较宏观适配、景气、盈利和估值。"],
  leaders: ["行业龙头", "产业龙头评分，不等同于当日领涨股。"],
  company: ["公司研究", "从龙头候选进入财务质量、护城河、治理和反方证据。"],
  valuation: ["估值与安全边际", "输入不足时不生成伪精确DCF，只展示可核验估值结果。"],
  timing: ["价值入场", "将公司质量与当前入场时机分开评估。"],
  plans: ["价值计划", "只有确定性信号经过价值委员会批准后才能进入模拟盘。"],
};

const EMOTION_SECTIONS: Record<string, [string, string]> = {
  temperature: ["情绪温度", "基于宽度、涨跌停、价量、资金、新闻和风险的0—100客观评分。"],
  cycle: ["情绪周期", "冰点、修复、发酵、高潮、退潮五阶段滞后状态机。"],
  sectors: ["板块热度", "区分持续性热点与单日涨幅噪声。"],
  short: ["短线候选", "1—5个交易日，重情绪、资金、事件和微观结构。"],
  swing: ["波段候选", "5—60个交易日，重趋势、持续资金、行业和基本面过滤。"],
  plans: ["情绪计划", "只有情绪委员会批准的信号才能进入对应模拟账户。"],
};

function percent(value: number | null | undefined) {
  return value == null ? "—" : `${(value * 100).toFixed(0)}%`;
}

function statusLabel(status?: string) {
  const labels: Record<string, string> = { completed: "已完成", running: "计算中", insufficient_data: "数据不足", failed: "失败", proposed: "待委员会", approved: "已批准", paper_submitted: "已提交模拟盘" };
  return labels[status || ""] || status || "尚未计算";
}

export function StrategyWorkspace({ line }: { line: StrategyLine }) {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const { market } = useWorkspaceMarket();
  const section = pathname.split("/")[2] || (line === "value" ? "macro" : "temperature");
  const [data, setData] = useState<StrategyDashboard | null>(null);
  const [observations, setObservations] = useState<StrategyObservations>(EMPTY_STRATEGY_OBSERVATIONS);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const meta = (line === "value" ? VALUE_SECTIONS : EMOTION_SECTIONS)[section] || [line === "value" ? "价值投资" : "情绪交易", "确定性策略工作台"];
  const observationScope = line === "value"
    ? section === "macro" ? "overview" : section === "sectors" ? "sectors" : "value"
    : section === "temperature" || section === "cycle" ? "overview" : section === "sectors" ? "sectors" : "momentum";

  const load = useCallback(async () => {
    if (market === "US") { setData(null); setLoading(false); return; }
    setLoading(true);
    try {
      const [value, observationData] = await Promise.all([
        (line === "value" ? api.getValueDashboard(market) : api.getEmotionDashboard(market)).catch((reason) => {
          setError(reason instanceof Error ? reason.message : "策略数据读取失败");
          return null;
        }),
        loadStrategyObservations(market, observationScope),
      ]);
      setData(value); setObservations(observationData);
      if (value) setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "策略数据读取失败");
    } finally { setLoading(false); }
  }, [line, market, observationScope]);

  useEffect(() => { void load(); const refresh = () => void load(); window.addEventListener("hengzhi:data-refresh", refresh); return () => window.removeEventListener("hengzhi:data-refresh", refresh); }, [load]);

  const run = async () => {
    if (market === "US") return;
    setRunning(true);
    try {
      const started = await api.createStrategyRun({ strategy_line: line, market, force_refresh: true });
      for (let attempt = 0; attempt < 40; attempt += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 500));
        const current = await api.getStrategyRun(started.id);
        if (!["queued", "running"].includes(current.status)) break;
      }
      await load();
      toast.success("策略引擎计算完成");
    } catch (reason) { toast.error(reason instanceof Error ? reason.message : "策略引擎启动失败"); }
    finally { setRunning(false); }
  };

  const scores = useMemo(() => {
    const values = data?.scores || [];
    if (line === "value") {
      if (section === "macro") return [];
      return section === "sectors" ? values.filter((item) => item.engine === "value_sector") : values.filter((item) => item.engine === "value_leader");
    }
    if (section === "sectors") return values.filter((item) => item.engine === "emotion_sector_heat");
    if (section === "swing") return values.filter((item) => item.engine === "emotion_swing");
    if (section === "short") return values.filter((item) => item.engine === "emotion_short");
    return values;
  }, [data, line, section]);

  if (market === "US") return <WorkspacePage><EmptyState title="双策略线 v1 仅覆盖A股和港股" body="现有美股研究能力仍保留在AI研究和历史页面中，但不混入A/H股策略评分。" /></WorkspacePage>;
  if (loading) return <WorkspacePage><LoadingState label="正在读取策略引擎结果…" /></WorkspacePage>;

  return <WorkspacePage>
    <PageHeader eyebrow={`${line.toUpperCase()} ENGINE / ${market}`} title={meta[0]} description={meta[1]} actions={<button onClick={() => void run()} disabled={running} className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"><RefreshCw className={cn("h-4 w-4", running && "animate-spin")} />重新计算</button>} />
    {error ? <div className="rounded-xl border border-danger/30 bg-danger/5 p-4 text-sm text-danger">{error}</div> : null}
    <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
      <MetricCard label="当前状态" value={data?.regime?.regime || "尚未计算"} icon={<Activity className="h-4 w-4" />} />
      <MetricCard label="状态得分" value={data?.regime?.score?.toFixed(1) ?? "—"} />
      <MetricCard label="数据覆盖率" value={percent(data?.regime?.coverage)} icon={<Database className="h-4 w-4" />} />
      <MetricCard label="引擎运行" value={statusLabel(data?.latest_run?.status)} hint={data?.latest_run?.as_of} icon={<Clock3 className="h-4 w-4" />} />
      <MetricCard label="候选信号" value={data?.signals.length || 0} hint={data?.latest_run?.formula_version || "等待首轮计算"} icon={<ShieldCheck className="h-4 w-4" />} />
    </section>

    {section === "macro" || section === "temperature" || section === "cycle" ? <RegimePanel data={data} /> : null}
    {section === "plans" ? <SignalPanel signals={data?.signals || []} navigate={navigate} /> : <>
      {data?.latest_run?.status === "insufficient_data" ? <div className="rounded-xl border border-warning/30 bg-warning/5 p-4 text-sm"><div className="font-medium text-warning">评分输入覆盖率不足，原始行情仍可浏览</div><p className="mt-1 text-xs leading-5 text-muted-foreground">下方“实时观察”来自事实数据层，不会被包装成评分、候选或交易信号；待 PIT 财务、资金、新闻等输入达到门槛后，确定性评分会自动出现。</p></div> : null}
      <StrategyObservationPanel line={line} section={section} market={market} observations={observations} />
      <ScorePanel scores={scores} line={line} section={section} />
    </>}
  </WorkspacePage>;
}

function RegimePanel({ data }: { data: StrategyDashboard | null }) {
  const regime = data?.regime;
  return <section className="rounded-xl border bg-card p-5 shadow-sm">
    <div className="flex items-center justify-between"><div><div className="text-xs font-semibold text-primary">REGIME SNAPSHOT</div><h2 className="mt-1 text-lg font-semibold">状态依据</h2></div><span className="rounded-full border px-3 py-1 text-xs">{regime?.formula_version || "尚无版本"}</span></div>
    {regime ? <div className="mt-5 grid gap-4 md:grid-cols-2"><div className="rounded-lg bg-muted/50 p-4"><div className="text-sm text-muted-foreground">当前 / 前一状态</div><div className="mt-2 text-xl font-semibold">{regime.regime} <span className="text-sm font-normal text-muted-foreground">← {regime.previous_regime || "首次计算"}</span></div></div><div className="rounded-lg bg-muted/50 p-4"><div className="text-sm text-muted-foreground">触发条件</div><div className="mt-2 text-sm">{regime.triggers.length ? regime.triggers.join("、") : "暂无可用触发指标"}</div></div></div> : <EmptyState title="尚未形成状态" body="点击重新计算；如果覆盖率不足，系统会明确返回数据不足而不是生成推测。" />}
  </section>;
}

function ScorePanel({ scores, line, section }: { scores: EngineScore[]; line: StrategyLine; section: string }) {
  if (!scores.length) return <section className="rounded-xl border bg-card p-5 shadow-sm"><div className="text-xs font-semibold text-primary">DETERMINISTIC SCORE</div><h2 className="mt-1 text-lg font-semibold">确定性评分</h2><div className="mt-4 rounded-lg border border-dashed p-5 text-sm text-muted-foreground">当前没有满足口径的评分。历史窗口或数据覆盖率不足时，缺失值不会被补成 0，也不会由 Agent 判断代替。</div></section>;
  return <section className="overflow-hidden rounded-xl border bg-card shadow-sm"><div className="border-b px-5 py-4"><div className="text-xs font-semibold text-primary">DETERMINISTIC SCORE</div><h2 className="mt-1 text-lg font-semibold">确定性评分</h2></div><div className="grid grid-cols-[1fr_120px_120px_180px] gap-4 border-b bg-muted/30 px-5 py-3 text-xs font-medium text-muted-foreground"><span>对象</span><span>基础分</span><span>覆盖率</span><span>公式版本</span></div><div className="divide-y">{scores.map((score) => <Link key={score.id} to={score.subject_type === "security" ? `/company/${score.market}/${score.subject_id}` : line === "value" ? "/value/sectors" : "/emotion/sectors"} className="grid grid-cols-[1fr_120px_120px_180px] items-center gap-4 px-5 py-4 hover:bg-muted/40"><div><div className="font-medium">{score.subject_id}</div><div className="mt-1 text-xs text-muted-foreground">{score.status} · {score.data_as_of} · {section}</div></div><div>{score.base_score == null ? "—" : <ScoreBar value={score.base_score} compact />}</div><span className={cn("font-mono text-sm", score.coverage >= .8 ? "text-success" : "text-warning")}>{percent(score.coverage)}</span><span className="truncate font-mono text-xs text-muted-foreground">{score.formula_version}</span></Link>)}</div></section>;
}

function SignalPanel({ signals, navigate }: { signals: StrategySignal[]; navigate: ReturnType<typeof useNavigate> }) {
  const review = async (signal: StrategySignal) => {
    try {
      const committee = await api.createStrategyCommittee({ market: signal.market, symbol: signal.symbol, strategy_line: signal.strategy_line, horizon: signal.horizon, signal_id: signal.id });
      navigate(`/ai/committees/${committee.id}`);
    } catch (reason) { toast.error(reason instanceof Error ? reason.message : "委员会启动失败"); }
  };
  if (!signals.length) return <EmptyState title="尚无可提交计划" body="只有覆盖率达标且通过确定性阈值的信号才会出现在这里。" />;
  return <section className="space-y-3">{signals.map((signal) => <article key={signal.id} className="rounded-xl border bg-card p-5 shadow-sm"><div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between"><div><div className="flex items-center gap-2"><span className="text-lg font-semibold">{signal.symbol}</span><span className="rounded-full border px-2 py-0.5 text-xs">{signal.horizon}</span><span className="rounded-full bg-primary/10 px-2 py-0.5 text-xs text-primary">{statusLabel(signal.status)}</span></div><div className="mt-2 text-sm text-muted-foreground">入场 {signal.entry_low ?? "—"}—{signal.entry_high ?? "—"} · 止损 {signal.stop_price ?? "—"} · 仓位上限 {(signal.position_cap * 100).toFixed(0)}%</div><div className="mt-1 font-mono text-xs text-muted-foreground">{signal.formula_versions.join(" · ")}</div></div><div className="flex items-center gap-3"><ScoreBar value={signal.base_score} compact />{signal.status === "proposed" ? <button onClick={() => void review(signal)} className="inline-flex items-center gap-2 rounded-lg border px-3 py-2 text-sm hover:bg-muted"><Bot className="h-4 w-4" />提交委员会</button> : <Link to="/simulation/signals" className="inline-flex items-center gap-1 text-sm text-primary">查看验证<ArrowRight className="h-4 w-4" /></Link>}</div></div></article>)}</section>;
}
