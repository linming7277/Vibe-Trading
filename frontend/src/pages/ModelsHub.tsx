import { useEffect, useState } from "react";
import { Link, useLocation } from "react-router";
import { ArrowRight, Database, FileSearch, GitCompareArrows, Landmark } from "lucide-react";
import { api, type StrategyDataStatus, type StrategyFormula } from "@/lib/api";
import { EmptyState, LoadingState, PageHeader, WorkspacePage } from "@/components/workspace/WorkspaceUI";

export function ModelsHub() {
  const section = useLocation().pathname.split("/")[2] || "strategies";
  const [formulas, setFormulas] = useState<StrategyFormula[]>([]);
  const [dataStatus, setDataStatus] = useState<StrategyDataStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const titles: Record<string, [string, string]> = {
    strategies: ["公式与策略版本", "确定性公式、适用市场、覆盖率门槛和 Strategy Store 登记状态。"],
    evidence: ["证据库", "按数据日期、可获得时间和来源追踪研究、评分与委员会引用。"],
  };
  const [title, description] = titles[section] || titles.strategies;

  useEffect(() => {
    setLoading(true);
    Promise.all([api.getStrategyFormulas(), api.getStrategyDataStatus()])
      .then(([formulaData, status]) => { setFormulas(formulaData.items); setDataStatus(status); })
      .catch(() => { setFormulas([]); setDataStatus(null); })
      .finally(() => setLoading(false));
  }, []);

  return <WorkspacePage>
    <PageHeader eyebrow="DATA & MODEL GOVERNANCE" title={title} description={description} />
    <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
      <Link to="/models/data" className="rounded-xl border bg-card p-5 hover:border-primary/40"><Database className="h-5 w-5 text-primary" /><div className="mt-4 font-semibold">数据中心</div><div className="mt-1 text-sm text-muted-foreground">最新缓存、历史分区和覆盖率</div><ArrowRight className="mt-4 h-4 w-4" /></Link>
      <Link to="/models/value-line-data" className="rounded-xl border bg-card p-5 hover:border-primary/40"><Landmark className="h-5 w-5 text-primary" /><div className="mt-4 font-semibold">价值线资料</div><div className="mt-1 text-sm text-muted-foreground">逐项查看价值线依赖的数据、用途和缺失边界</div><ArrowRight className="mt-4 h-4 w-4" /></Link>
      <Link to="/models/factors" className="rounded-xl border bg-card p-5 hover:border-primary/40"><GitCompareArrows className="h-5 w-5 text-primary" /><div className="mt-4 font-semibold">因子库</div><div className="mt-1 text-sm text-muted-foreground">公开因子、样本外验证和相关性</div><ArrowRight className="mt-4 h-4 w-4" /></Link>
      <Link to="/models/evidence" className="rounded-xl border bg-card p-5 hover:border-primary/40"><FileSearch className="h-5 w-5 text-primary" /><div className="mt-4 font-semibold">证据库</div><div className="mt-1 text-sm text-muted-foreground">数据来源和决策引用</div><ArrowRight className="mt-4 h-4 w-4" /></Link>
    </section>
    {loading ? <LoadingState label="正在读取公式与数据来源状态…" /> : section === "evidence" ? <EvidenceTable status={dataStatus} /> : <FormulaTable items={formulas} />}
  </WorkspacePage>;
}

function FormulaTable({ items }: { items: StrategyFormula[] }) {
  if (!items.length) return <EmptyState title="尚无已登记公式" body="确定性引擎不会从 Agent 提示词读取公式权重。" />;
  return <section className="overflow-hidden rounded-xl border bg-card shadow-sm">
    <div className="border-b p-5"><h2 className="font-semibold">确定性公式注册表</h2><p className="mt-1 text-sm text-muted-foreground">每个版本不可原地修改；变更权重必须创建新版本并重新验证。</p></div>
    <div className="overflow-x-auto"><table className="w-full min-w-[760px] text-sm"><thead className="bg-muted/50 text-left text-xs text-muted-foreground"><tr><th className="p-3">策略线</th><th className="p-3">公式</th><th className="p-3">版本</th><th className="p-3">覆盖率门槛</th><th className="p-3">权重/规则</th></tr></thead><tbody className="divide-y">{items.map((item) => <tr key={item.id}><td className="p-3">{item.strategy_line === "value" ? "价值" : "情绪"}</td><td className="p-3 font-medium">{item.name}</td><td className="p-3 font-mono text-xs">{item.version}</td><td className="p-3">{item.minimum_coverage == null ? "规则引擎" : `${(item.minimum_coverage * 100).toFixed(0)}%`}</td><td className="p-3 text-xs text-muted-foreground">{Object.keys(item.weights).length ? Object.entries(item.weights).map(([key, value]) => `${key} ${(value * 100).toFixed(0)}%`).join(" · ") : "版本化确定性规则"}</td></tr>)}</tbody></table></div>
  </section>;
}

function EvidenceTable({ status }: { status: StrategyDataStatus | null }) {
  if (!status) return <EmptyState title="数据来源状态不可用" body="系统不会用零值或 Agent 推断替代缺失数据。" />;
  return <section className="space-y-4">
    <div className="grid gap-3 sm:grid-cols-2">{status.providers.map((provider) => <article key={provider.market} className="rounded-xl border bg-card p-5"><div className="flex items-center justify-between"><strong>{provider.market === "CN" ? "A股" : "港股"}</strong><span className="rounded-full bg-muted px-2 py-1 text-xs">{provider.status}</span></div><div className="mt-3 text-sm">当前来源：{provider.provider || "不可用"}</div><div className="mt-2 text-xs text-muted-foreground">降级链：{provider.fallback_chain.join(" → ")}</div></article>)}</div>
    {status.catalog.length ? <div className="overflow-hidden rounded-xl border bg-card"><div className="border-b p-4 text-sm font-semibold">PIT 历史分区（{status.partitions}）</div><div className="divide-y">{status.catalog.slice(0, 50).map((item) => <div key={item.id} className="grid grid-cols-[80px_1fr_120px_90px] gap-3 p-3 text-sm"><span>{item.market}</span><span>{item.dataset}</span><span>{item.data_as_of}</span><span>{(item.coverage * 100).toFixed(0)}%</span></div>)}</div></div> : <EmptyState title="历史仓库尚未回填" body="页面明确显示空覆盖，不会把当前行情伪装成 2015 年以来的 PIT 历史。" />}
  </section>;
}
