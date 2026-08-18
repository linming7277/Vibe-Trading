import { Check, ChevronRight, RotateCcw } from "lucide-react";
import { Link } from "react-router";
import { cn } from "@/lib/utils";
import { useDecisionFlow } from "@/hooks/useDecisionFlow";

const labels = ["宏观", "行业", "龙头", "深度研究", "买卖点"] as const;

export function DecisionFlow({ current }: { current: 1 | 2 | 3 | 4 | 5 }) {
  const { flow, reset } = useDecisionFlow();
  const completed = [Boolean(flow.macro_headline), Boolean(flow.sector_code), Boolean(flow.symbol), Boolean(flow.research_completed_at), Boolean(flow.trade_plan_id)];
  const links = [
    "/market/macro?flow=1",
    "/market/sectors?view=research&flow=1",
    flow.sector_code ? `/screener?sector=${encodeURIComponent(flow.sector_code)}&flow=1` : "/screener?flow=1",
    flow.symbol ? `/company/CN/${encodeURIComponent(flow.symbol)}?tab=research&flow=1&from=${encodeURIComponent("/value")}&from_label=${encodeURIComponent("价值龙头")}` : "/screener?flow=1",
    flow.symbol ? `/signals?new=1&market=CN&symbol=${encodeURIComponent(flow.symbol)}&name=${encodeURIComponent(flow.company_name || flow.symbol)}&flow=1` : "/signals",
  ];
  const values = [flow.macro_stance, flow.sector_name, flow.company_name || flow.symbol, flow.research_completed_at ? "底稿已生成" : undefined, flow.trade_plan_id ? "计划已保存" : undefined];
  return <section className="rounded-xl border border-primary/20 bg-gradient-to-r from-primary/5 via-card to-card p-4 shadow-sm">
    <div className="mb-3 flex items-center justify-between gap-4">
      <div><div className="text-xs font-semibold uppercase tracking-[0.16em] text-primary">Decision Path</div><div className="mt-1 text-sm font-medium">宏观 → 行业 → 龙头 → 深度研究 → 买卖点</div></div>
      <button onClick={reset} className="inline-flex shrink-0 items-center gap-1 text-xs text-muted-foreground hover:text-foreground"><RotateCcw className="h-3.5 w-3.5" />重新开始</button>
    </div>
    <div className="overflow-x-auto pb-1"><div className="flex min-w-[720px] items-center">
      {labels.map((label, index) => <div key={label} className="contents">
        <Link to={links[index]} className={cn("group flex min-w-28 flex-1 items-center gap-2 rounded-lg border px-3 py-2.5 transition", current === index + 1 ? "border-primary bg-primary text-primary-foreground" : completed[index] ? "border-success/30 bg-success/5" : "bg-background hover:border-primary/40")}>
          <span className={cn("flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-semibold", current === index + 1 ? "bg-primary-foreground/20" : completed[index] ? "bg-success text-white" : "bg-muted text-muted-foreground")}>{completed[index] && current !== index + 1 ? <Check className="h-3.5 w-3.5" /> : index + 1}</span>
          <span className="min-w-0"><span className="block text-xs font-medium">{label}</span>{values[index] ? <span className={cn("block truncate text-[10px]", current === index + 1 ? "text-primary-foreground/75" : "text-muted-foreground")}>{values[index]}</span> : null}</span>
        </Link>
        {index < labels.length - 1 ? <ChevronRight className="mx-1 h-4 w-4 shrink-0 text-muted-foreground" /> : null}
      </div>)}
    </div></div>
  </section>;
}
