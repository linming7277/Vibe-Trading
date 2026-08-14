import { useCallback, useEffect, useState } from "react";
import { useLocation } from "react-router";
import { Activity, CircleDollarSign, FlaskConical, ShieldCheck } from "lucide-react";
import { api, type PaperAccount, type PaperNav, type StrategySignal } from "@/lib/api";
import { EmptyState, LoadingState, MetricCard, PageHeader, ScoreBar, WorkspacePage, formatNumber } from "@/components/workspace/WorkspaceUI";

export function SimulationHub() {
  const { pathname } = useLocation();
  const section = pathname.split("/")[2] || "accounts";
  const [accounts, setAccounts] = useState<PaperAccount[]>([]);
  const [navs, setNavs] = useState<Record<string, PaperNav>>({});
  const [signals, setSignals] = useState<StrategySignal[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const accountRows = await api.listPaperAccounts();
      setAccounts(accountRows);
      const navRows = await Promise.all(accountRows.map((item) => api.getPaperNav(item.id)));
      setNavs(Object.fromEntries(navRows.map((item) => [item.account_id, item])));
      const [valueCn, valueHk, emotionCn, emotionHk] = await Promise.all([
        api.getValueSignals("CN"), api.getValueSignals("HK"), api.getEmotionSignals("CN"), api.getEmotionSignals("HK"),
      ]);
      setSignals([...valueCn, ...valueHk, ...emotionCn, ...emotionHk]);
    } finally { setLoading(false); }
  }, []);

  useEffect(() => { void load(); }, [load]);
  if (loading) return <WorkspacePage><LoadingState label="正在读取模拟验证账本…" /></WorkspacePage>;

  if (section === "signals") return <WorkspacePage><PageHeader eyebrow="PAPER VALIDATION" title="信号日志" description="同时保留确定性原始信号与委员会审批状态，避免只看最终成交样本。" />{signals.length ? <div className="space-y-3">{signals.map((signal) => <article key={signal.id} className="rounded-xl border bg-card p-5"><div className="flex items-center justify-between gap-4"><div><div className="font-semibold">{signal.symbol} · {signal.strategy_line} / {signal.horizon}</div><div className="mt-1 text-xs text-muted-foreground">{signal.data_as_of} · {signal.status} · 有效至 {signal.valid_until}</div></div><ScoreBar value={signal.base_score} compact /></div><div className="mt-3 grid gap-2 text-sm sm:grid-cols-4"><span>入场：{signal.entry_low ?? "—"}—{signal.entry_high ?? "—"}</span><span>止损：{signal.stop_price ?? "—"}</span><span>仓位：{(signal.position_cap * 100).toFixed(0)}%</span><span>覆盖率：{(signal.coverage * 100).toFixed(0)}%</span></div></article>)}</div> : <EmptyState title="暂无策略信号" body="运行价值或情绪引擎后，原始信号会出现在这里。" />}</WorkspacePage>;

  if (section === "decay") return <WorkspacePage><PageHeader eyebrow="STRATEGY GOVERNANCE" title="策略衰减" description="策略版本继续由现有 strategy store 管理；只有完成样本外验证的版本才能进入监控。" /><section className="grid gap-3 sm:grid-cols-3"><MetricCard label="已注册策略线" value="2" icon={<FlaskConical className="h-4 w-4" />} /><MetricCard label="验证口径" value="Walk Forward" /><MetricCard label="模拟账户" value={accounts.length} icon={<ShieldCheck className="h-4 w-4" />} /></section><EmptyState title="等待形成衰减基线" body="需要完整历史回放和连续模拟样本后，才会生成滚动IC、IR和收益衰减告警。" /></WorkspacePage>;

  return <WorkspacePage><PageHeader eyebrow="INTERNAL PAPER LEDGER" title="模拟账户" description="四个账户相互隔离，不连接券商，也不会调用实盘下单工具。" /><section className="grid gap-4 md:grid-cols-2">{accounts.map((account) => { const nav = navs[account.id]; return <article key={account.id} className="rounded-xl border bg-card p-5 shadow-sm"><div className="flex items-start justify-between"><div><div className="text-xs font-semibold text-primary">{account.strategy_line.toUpperCase()} / {account.horizon.toUpperCase()}</div><h2 className="mt-1 text-lg font-semibold">{account.name}</h2></div><CircleDollarSign className="h-5 w-5 text-muted-foreground" /></div><div className="mt-5 grid grid-cols-3 gap-3"><div><div className="text-xs text-muted-foreground">净值</div><div className="mt-1 text-xl font-semibold">{formatNumber(nav?.nav)}</div></div><div><div className="text-xs text-muted-foreground">现金</div><div className="mt-1 text-xl font-semibold">{formatNumber(nav?.cash)}</div></div><div><div className="text-xs text-muted-foreground">持仓</div><div className="mt-1 text-xl font-semibold">{nav?.positions.length || 0}</div></div></div><div className="mt-4 flex items-center gap-2 text-xs text-muted-foreground"><Activity className="h-3.5 w-3.5" />{account.market} · {account.currency} · {account.status}</div></article>; })}</section></WorkspacePage>;
}
