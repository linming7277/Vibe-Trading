import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { AlertTriangle, CircleDot, Filter, Plus, Save } from "lucide-react";
import { toast } from "sonner";
import { api, type TradePlan, type TradePlanStatus } from "@/lib/api";
import { useDecisionFlow } from "@/hooks/useDecisionFlow";
import { DecisionFlow } from "@/components/workspace/DecisionFlow";
import { EmptyState, LoadingState, PageHeader, WorkspacePage, formatNumber } from "@/components/workspace/WorkspaceUI";

const statuses: TradePlanStatus[] = ["draft", "active", "triggered", "closed", "cancelled"];
const labels: Record<TradePlanStatus, string> = { draft: "草稿", active: "生效", triggered: "已触发", closed: "已关闭", cancelled: "已取消" };
type FormState = Record<"entry_low" | "entry_high" | "target_low" | "target_high" | "stop_price" | "position_cap" | "triggers" | "notes", string>;
const emptyForm: FormState = { entry_low: "", entry_high: "", target_low: "", target_high: "", stop_price: "", position_cap: "", triggers: "", notes: "" };

export function TradePlans() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const { flow, update: updateFlow } = useDecisionFlow();
  const market = params.get("market") || "CN";
  const symbol = params.get("symbol") || flow.symbol || "";
  const name = params.get("name") || flow.company_name || symbol;
  const creating = params.get("new") === "1" && Boolean(symbol);
  const [items, setItems] = useState<TradePlan[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [filter, setFilter] = useState<TradePlanStatus | "all">("all");
  const [form, setForm] = useState<FormState>(emptyForm);
  const [currentPrice, setCurrentPrice] = useState<number | null>(null);
  const load = () => api.listTradePlans().then(setItems).finally(() => setLoading(false));
  useEffect(() => { void load(); }, []);
  useEffect(() => {
    if (!creating || market !== "CN") return;
    api.getTdxSecurityOverview(symbol).then((value) => {
      const price = Number(value.quote?.price);
      setCurrentPrice(Number.isFinite(price) ? price : null);
    }).catch(() => setCurrentPrice(null));
  }, [creating, market, symbol]);
  const shown = useMemo(() => filter === "all" ? items : items.filter((item) => item.status === filter), [items, filter]);
  const update = async (item: TradePlan, status: TradePlanStatus) => { try { const next = await api.updateTradePlan(item.id, { status }); setItems((rows) => rows.map((row) => row.id === item.id ? next : row)); toast.success("买卖点计划状态已更新"); } catch (e) { toast.error(e instanceof Error ? e.message : "更新失败"); } };
  const save = async () => {
    const values = Object.fromEntries(["entry_low", "entry_high", "target_low", "target_high", "stop_price", "position_cap"].map((key) => [key, Number(form[key as keyof FormState])])) as Record<string, number>;
    if (Object.values(values).some((value) => !Number.isFinite(value))) { toast.error("请完整填写入场、目标、止损和仓位上限"); return; }
    if (!(values.stop_price < values.entry_low && values.entry_low <= values.entry_high && values.entry_high < values.target_low && values.target_low <= values.target_high)) { toast.error("做多计划需满足：止损 < 入场下限 ≤ 入场上限 < 目标下限 ≤ 目标上限"); return; }
    if (values.position_cap <= 0 || values.position_cap > 100) { toast.error("仓位上限应在 0–100% 之间"); return; }
    const triggers = form.triggers.split("\n").map((value) => value.trim()).filter(Boolean);
    if (!triggers.length) { toast.error("至少填写一条复审或失效条件"); return; }
    setSaving(true);
    try {
      const plan = await api.createTradePlan({
        market: market as "CN" | "HK" | "US", symbol, name, status: "draft", direction: "buy",
        ...values, triggers,
        notes: [
          flow.macro_headline ? `宏观判断：${flow.macro_headline}` : "",
          flow.sector_name ? `所选行业：${flow.sector_name}` : "",
          form.notes.trim(),
        ].filter(Boolean).join("\n"),
      });
      updateFlow({ trade_plan_id: plan.id });
      setItems((rows) => [plan, ...rows]);
      setForm(emptyForm);
      toast.success("买卖点计划已保存为草稿，不会自动下单");
      navigate(`/signals?saved=${encodeURIComponent(plan.id)}&flow=1`, { replace: true });
    } catch (error) { toast.error(error instanceof Error ? error.message : "保存失败"); }
    finally { setSaving(false); }
  };
  return <WorkspacePage><DecisionFlow current={5} /><PageHeader eyebrow="ENTRY / EXIT" title="买卖点" description="把研究结论转成入场区间、止盈区间、止损、仓位上限和失效条件；仅保存人工计划，不发送实盘委托。" actions={!creating ? <Link to={flow.symbol ? `/signals?new=1&market=CN&symbol=${encodeURIComponent(flow.symbol)}&name=${encodeURIComponent(flow.company_name || flow.symbol)}&flow=1` : "/screener?flow=1"} className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground"><Plus className="h-4 w-4" />新建买卖点</Link> : undefined} />
    {creating ? <PlanEditor symbol={symbol} name={name} currentPrice={currentPrice} form={form} setForm={setForm} saving={saving} save={save} /> : null}
    <div className="flex flex-wrap items-center gap-2"><Filter className="h-4 w-4 text-muted-foreground" /><button onClick={() => setFilter("all")} className={`rounded-full px-3 py-1 text-xs ${filter === "all" ? "bg-foreground text-background" : "bg-muted"}`}>全部</button>{statuses.map((status) => <button key={status} onClick={() => setFilter(status)} className={`rounded-full px-3 py-1 text-xs ${filter === status ? "bg-foreground text-background" : "bg-muted"}`}>{labels[status]}</button>)}</div>
    {loading ? <LoadingState /> : !shown.length ? <EmptyState title="暂无买卖点计划" body="从宏观开始完成五步决策链，或先选择一只股票新建计划。" /> : <section className="grid gap-4 lg:grid-cols-2">{shown.map((item) => <article key={item.id} className={`rounded-xl border bg-card p-5 ${params.get("saved") === item.id ? "border-success ring-1 ring-success/30" : ""}`}><div className="flex items-start justify-between"><div><div className="font-semibold">{item.name}</div><Link to={`/company/${item.market}/${item.symbol}?tab=${encodeURIComponent("研究结论")}`} className="mt-1 block text-xs text-primary">{item.market} · {item.symbol}</Link></div><select value={item.status} onChange={(e) => void update(item, e.target.value as TradePlanStatus)} className="rounded-lg border bg-background px-2 py-1 text-xs">{statuses.map((status) => <option key={status} value={status}>{labels[status]}</option>)}</select></div><div className="mt-5 grid grid-cols-2 gap-3 text-sm"><PlanMetric label="方向" value={item.direction === "buy" ? "买入 / 持有" : item.direction} /><PlanMetric label="仓位上限" value={item.position_cap != null ? `${item.position_cap}%` : "待复核"} /><PlanMetric label="买入区间" value={range(item.entry_low, item.entry_high)} /><PlanMetric label="止盈区间" value={range(item.target_low, item.target_high)} /><PlanMetric label="止损价" value={formatNumber(item.stop_price)} /><PlanMetric label="更新时间" value={new Date(item.updated_at).toLocaleDateString("zh-CN")} /></div><div className="mt-4 space-y-2">{item.triggers.map((trigger) => <div key={trigger} className="flex items-start gap-2 rounded-lg bg-muted/50 p-3 text-xs"><CircleDot className="mt-0.5 h-3.5 w-3.5 text-primary" />{trigger}</div>)}</div>{item.notes ? <p className="mt-4 whitespace-pre-line text-xs leading-5 text-muted-foreground">{item.notes}</p> : null}</article>)}</section>}
  </WorkspacePage>;
}

function PlanEditor({ symbol, name, currentPrice, form, setForm, saving, save }: { symbol: string; name: string; currentPrice: number | null; form: FormState; setForm: React.Dispatch<React.SetStateAction<FormState>>; saving: boolean; save: () => void }) {
  const field = (key: keyof FormState, value: string) => setForm((current) => ({ ...current, [key]: value }));
  return <section className="rounded-xl border border-primary/30 bg-card p-5 shadow-sm"><div className="flex flex-col justify-between gap-3 border-b pb-4 sm:flex-row sm:items-center"><div><h2 className="font-semibold">{name} <span className="font-mono text-xs text-muted-foreground">{symbol}</span></h2><p className="mt-1 text-sm text-muted-foreground">当前价仅作填写参考：<span className="font-mono font-medium text-foreground">{currentPrice == null ? "—" : `${formatNumber(currentPrice)} 元`}</span>，系统不会据此自动推导点位。</p></div><div className="flex items-center gap-2 rounded-lg border border-warning/30 bg-warning/5 px-3 py-2 text-xs text-warning"><AlertTriangle className="h-4 w-4" />保存草稿，不下单</div></div><div className="mt-5 grid gap-5 lg:grid-cols-3"><div><h3 className="text-sm font-semibold">买入与仓位</h3><div className="mt-3 grid grid-cols-2 gap-2"><NumberInput label="买入下限" value={form.entry_low} onChange={(v) => field("entry_low", v)} /><NumberInput label="买入上限" value={form.entry_high} onChange={(v) => field("entry_high", v)} /><NumberInput label="仓位上限 %" value={form.position_cap} onChange={(v) => field("position_cap", v)} /></div></div><div><h3 className="text-sm font-semibold">卖出与风控</h3><div className="mt-3 grid grid-cols-2 gap-2"><NumberInput label="止盈下限" value={form.target_low} onChange={(v) => field("target_low", v)} /><NumberInput label="止盈上限" value={form.target_high} onChange={(v) => field("target_high", v)} /><NumberInput label="止损价" value={form.stop_price} onChange={(v) => field("stop_price", v)} /></div></div><div><h3 className="text-sm font-semibold">复审 / 失效条件</h3><textarea value={form.triggers} onChange={(e) => field("triggers", e.target.value)} rows={4} placeholder={"每行一条，例如：\n季度净利润低于预期\n行业相对强度跌出前50%"} className="mt-3 w-full rounded-lg border bg-background px-3 py-2 text-xs outline-none focus:border-primary" /></div></div><textarea value={form.notes} onChange={(e) => field("notes", e.target.value)} rows={2} placeholder="补充研究依据、观察周期或执行备注（可选）" className="mt-4 w-full rounded-lg border bg-background px-3 py-2 text-sm outline-none focus:border-primary" /><div className="mt-4 flex justify-end"><button onClick={save} disabled={saving} className="inline-flex items-center gap-2 rounded-lg bg-primary px-5 py-2.5 text-sm font-medium text-primary-foreground disabled:opacity-50"><Save className="h-4 w-4" />{saving ? "保存中…" : "保存买卖点草稿"}</button></div></section>;
}
function NumberInput({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) { return <label className="text-xs text-muted-foreground"><span>{label}</span><input type="number" min="0" step="0.01" value={value} onChange={(e) => onChange(e.target.value)} className="mt-1 w-full rounded-lg border bg-background px-3 py-2 font-mono text-sm text-foreground outline-none focus:border-primary" /></label>; }
function PlanMetric({ label, value }: { label: string; value: string }) { return <div className="rounded-lg border p-3"><div className="text-xs text-muted-foreground">{label}</div><div className="mt-1 font-medium">{value}</div></div>; }
function range(low?: number | null, high?: number | null) { return low == null && high == null ? "待复核" : `${formatNumber(low)} – ${formatNumber(high)}`; }

