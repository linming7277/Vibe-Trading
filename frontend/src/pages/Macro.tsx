import { useEffect, useState } from "react";
import { AlertTriangle, ArrowRight, ArrowUpRight, RefreshCw } from "lucide-react";
import { useNavigate } from "react-router";
import { toast } from "sonner";
import { api, type MacroBrief } from "@/lib/api";
import { useWorkspaceMarket } from "@/hooks/useWorkspaceMarket";
import { EmptyState, LoadingState, MARKET_LABELS, MarketTabs, PageHeader, SourceBadge, WorkspacePage } from "@/components/workspace/WorkspaceUI";
import { DecisionFlow } from "@/components/workspace/DecisionFlow";
import { useDecisionFlow } from "@/hooks/useDecisionFlow";

export function Macro() {
  const navigate = useNavigate();
  const { selectMacro } = useDecisionFlow();
  const { market } = useWorkspaceMarket();
  const [brief, setBrief] = useState<MacroBrief | null>(null);
  const [loading, setLoading] = useState(true);
  const load = () => { setLoading(true); api.getMacroBrief(market).then(setBrief).catch(() => setBrief(null)).finally(() => setLoading(false)); };
  useEffect(load, [market]);
  const refresh = async () => { const run = await api.refreshDashboard({ module: "macro", market }); toast.success(run.message); load(); };
  const next = () => {
    if (!brief) return;
    selectMacro({ headline: brief.headline, stance: brief.stance, as_of: brief.as_of });
    navigate("/market/sectors?view=research&flow=1");
  };
  return <WorkspacePage><DecisionFlow current={1} /><PageHeader eyebrow="MACRO" title={`${MARKET_LABELS[market]}宏观环境`} description="先确认市场环境、主线和风险约束，再带着同一份判断进入行业选择。" actions={<><MarketTabs /><button onClick={() => void refresh()} className="inline-flex items-center gap-2 rounded-lg border bg-card px-3 py-2 text-sm"><RefreshCw className="h-4 w-4" />刷新研究</button></>} />{loading ? <LoadingState /> : !brief ? <EmptyState title="暂无宏观简报" /> : <><section className="rounded-2xl border bg-gradient-to-br from-card to-primary/5 p-6 shadow-sm"><div className="flex flex-wrap items-start justify-between gap-4"><div><div className="text-xs font-semibold uppercase tracking-widest text-primary">核心判断</div><h2 className="mt-3 text-2xl font-semibold md:text-3xl">{brief.headline}</h2></div><SourceBadge status={brief.source_status} asOf={brief.as_of} /></div><p className="mt-5 max-w-4xl text-sm leading-7 text-muted-foreground">{brief.summary}</p><div className="mt-6 flex flex-wrap gap-2">{brief.themes.map((theme) => <span key={theme} className="inline-flex items-center gap-1 rounded-lg border bg-background px-3 py-2 text-sm"><ArrowUpRight className="h-3.5 w-3.5 text-primary" />{theme}</span>)}</div></section><section className="grid gap-4 md:grid-cols-2"><article className="rounded-xl border bg-card p-5"><div className="text-xs font-semibold text-primary">市场倾向</div><div className="mt-2 text-2xl font-semibold capitalize">{brief.stance}</div><p className="mt-2 text-sm text-muted-foreground">这会成为后续行业选择的上游约束，不直接生成交易指令。</p></article><article className="rounded-xl border border-warning/30 bg-warning/5 p-5"><div className="flex items-center gap-2 font-semibold text-warning"><AlertTriangle className="h-4 w-4" />需要跟踪的风险</div><ul className="mt-3 space-y-2 text-sm">{brief.risks.map((risk) => <li key={risk}>• {risk}</li>)}</ul></article></section><section className="flex flex-col justify-between gap-4 rounded-xl border border-primary/30 bg-primary/5 p-5 sm:flex-row sm:items-center"><div><div className="font-semibold">确认这份宏观判断</div><p className="mt-1 text-sm text-muted-foreground">保存到当前决策链，并进入行业实时强度与研究评分的交叉选择。</p></div><button onClick={next} className="inline-flex shrink-0 items-center justify-center gap-2 rounded-lg bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground">下一步：选择行业<ArrowRight className="h-4 w-4" /></button></section></>}</WorkspacePage>;
}
