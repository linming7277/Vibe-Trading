import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router";
import { Activity, ArrowRight, Database, Scale, ShieldCheck } from "lucide-react";
import { api, type StrategyDashboard, type StrategySignal } from "@/lib/api";
import { EMPTY_STRATEGY_OBSERVATIONS, loadStrategyObservations, type StrategyObservations } from "@/lib/strategyObservations";
import { useWorkspaceMarket } from "@/hooks/useWorkspaceMarket";
import { EmptyState, LoadingState, MetricCard, PageHeader, ScoreBar, WorkspacePage } from "@/components/workspace/WorkspaceUI";
import { ObservationPreview } from "@/components/workspace/StrategyObservations";

export function Today() {
  const { market } = useWorkspaceMarket();
  const [value, setValue] = useState<StrategyDashboard | null>(null);
  const [emotion, setEmotion] = useState<StrategyDashboard | null>(null);
  const [observations, setObservations] = useState<StrategyObservations>(EMPTY_STRATEGY_OBSERVATIONS);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    if (market === "US") { setLoading(false); return; }
    setLoading(true);
    try {
      const [valueData, emotionData, observationData] = await Promise.all([
        api.getValueDashboard(market).catch(() => null),
        api.getEmotionDashboard(market).catch(() => null),
        loadStrategyObservations(market),
      ]);
      setValue(valueData); setEmotion(emotionData); setObservations(observationData);
    } finally { setLoading(false); }
  }, [market]);

  useEffect(() => { void load(); const refresh = () => void load(); window.addEventListener("hengzhi:data-refresh", refresh); return () => window.removeEventListener("hengzhi:data-refresh", refresh); }, [load]);
  if (market === "US") return <WorkspacePage><EmptyState title="今日双线总览仅覆盖A股和港股" body="美股原有研究和回测能力仍然保留，但不会混入A/H股价值与情绪评分。" /></WorkspacePage>;
  if (loading) return <WorkspacePage><LoadingState label="正在汇总价值线和情绪线…" /></WorkspacePage>;

  const signals = [...(value?.signals || []), ...(emotion?.signals || [])];
  return <WorkspacePage>
    <PageHeader eyebrow={`TODAY / ${market}`} title="今日双线总览" description="价值线解决买什么、为什么以及什么价格；情绪线解决短期市场处于什么阶段、资金正在聚焦哪里。" />
    <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-6">
      <MetricCard label="价值状态" value={value?.regime?.regime || "尚未计算"} icon={<Scale className="h-4 w-4" />} />
      <MetricCard label="价值覆盖率" value={value?.regime ? `${(value.regime.coverage * 100).toFixed(0)}%` : "—"} icon={<Database className="h-4 w-4" />} />
      <MetricCard label="价值信号" value={value?.signals.length || 0} />
      <MetricCard label="情绪周期" value={emotion?.regime?.regime || "尚未计算"} icon={<Activity className="h-4 w-4" />} />
      <MetricCard label="情绪温度" value={emotion?.regime?.score?.toFixed(1) ?? "—"} />
      <MetricCard label="委员会候选" value={signals.filter((item) => item.status === "proposed").length} icon={<ShieldCheck className="h-4 w-4" />} />
    </section>
    <section className="grid gap-5 xl:grid-cols-2">
      <LineCard line="value" title="价值投资" description="宏观 → 赛道 → 龙头 → 估值 → 入场" to="/value/macro" dashboard={value} observations={observations} />
      <LineCard line="emotion" title="情绪交易" description="温度 → 周期 → 板块 → 短线/波段 → 时机" to="/emotion/temperature" dashboard={emotion} observations={observations} />
    </section>
    <section className="rounded-xl border bg-card shadow-sm"><div className="flex items-center justify-between border-b p-5"><div><div className="text-xs font-semibold text-primary">ACTIONABLE SIGNALS</div><h2 className="mt-1 text-lg font-semibold">待验证信号</h2></div><Link to="/simulation/signals" className="inline-flex items-center gap-1 text-sm text-primary">完整日志<ArrowRight className="h-4 w-4" /></Link></div>{signals.length ? <div className="divide-y">{signals.slice(0, 8).map((signal) => <SignalRow key={signal.id} signal={signal} />)}</div> : <div className="p-5"><EmptyState title="当前没有可交易信号" body="覆盖率不足或未达到确定性阈值时，系统不会用Agent判断补足信号。" /></div>}</section>
  </WorkspacePage>;
}

function LineCard({ line, title, description, to, dashboard, observations }: { line: "value" | "emotion"; title: string; description: string; to: string; dashboard: StrategyDashboard | null; observations: StrategyObservations }) {
  const top = dashboard?.scores.filter((item) => item.base_score != null).slice(0, 4) || [];
  return <article className="rounded-xl border bg-card p-5 shadow-sm"><div className="flex items-start justify-between"><div><h2 className="text-lg font-semibold">{title}</h2><p className="mt-1 text-sm text-muted-foreground">{description}</p></div><Link to={to} className="rounded-lg border p-2 hover:bg-muted"><ArrowRight className="h-4 w-4" /></Link></div><div className="mt-5 space-y-3">{top.length ? top.map((score) => <div key={score.id} className="flex items-center justify-between gap-4 rounded-lg bg-muted/40 p-3"><div><div className="font-medium">{score.subject_id}</div><div className="text-xs text-muted-foreground">覆盖率 {(score.coverage * 100).toFixed(0)}% · {score.formula_version}</div></div><ScoreBar value={score.base_score || 0} compact /></div>) : <ObservationPreview line={line} observations={observations} />}</div></article>;
}

function SignalRow({ signal }: { signal: StrategySignal }) {
  return <Link to={signal.strategy_line === "value" ? "/value/plans" : "/emotion/plans"} className="grid grid-cols-[1fr_110px_110px] items-center gap-4 p-4 hover:bg-muted/40"><div><div className="font-medium">{signal.symbol}</div><div className="mt-1 text-xs text-muted-foreground">{signal.strategy_line} / {signal.horizon} · {signal.status} · 有效至 {signal.valid_until}</div></div><ScoreBar value={signal.base_score} compact /><span className="text-right text-sm">仓位≤{(signal.position_cap * 100).toFixed(0)}%</span></Link>;
}
