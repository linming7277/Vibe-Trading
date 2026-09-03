import { useEffect, useState } from "react";
import { api, type ValueWatchpoint, type ValueWatchpointProjection } from "@/lib/api";

const CATEGORY_LABEL: Record<string, string> = {
  THESIS: "核心逻辑",
  RISK: "风险观察",
  FINANCIAL: "财务下一期",
  BUSINESS: "经营下一期",
  VALUATION: "估值资料",
  MOAT: "竞争优势",
  CAPITAL: "资本配置",
};

function WatchpointCard({ item, compact }: { item: ValueWatchpoint; compact?: boolean }) {
  return (
    <article className="rounded-lg border bg-background/60 p-4">
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <span>{CATEGORY_LABEL[item.category] || item.category}</span>
        <span>{item.importance_tier}</span>
        <span>{item.source_module_label || item.source_module}</span>
      </div>
      <h3 className="mt-1 text-sm font-semibold leading-6">{item.title}</h3>
      <p className="mt-1 text-sm leading-6 text-muted-foreground">当前：{item.current_state}</p>
      {compact ? null : (
        <div className="mt-2 space-y-1 text-sm leading-6">
          <p>有利：{item.positive_condition}</p>
          <p>不利：{item.negative_condition}</p>
          <p>下次核验：{item.next_review_label || item.next_review_anchor || "人工复核"}</p>
        </div>
      )}
      {item.cautions?.length ? (
        <p className="mt-2 text-xs leading-5 text-amber-800 dark:text-amber-200">{item.cautions.filter(Boolean).join("；")}</p>
      ) : null}
    </article>
  );
}

export function CompanyWatchpointsPanel({ stockCode, compact = false }: { stockCode: string; compact?: boolean }) {
  const [data, setData] = useState<ValueWatchpointProjection | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setError("");
    api.getValueWatchpoints(stockCode).then((payload) => {
      if (!cancelled) setData(payload);
    }).catch((exc: Error) => {
      if (!cancelled) setError(exc.message || "验证点读取失败");
    });
    return () => { cancelled = true; };
  }, [stockCode]);

  const items = compact ? (data?.top_watchpoints || []) : (data?.watchpoints || data?.top_watchpoints || []);
  const shown = compact ? items.slice(0, 3) : items;
  const gaps = data?.data_gaps || [];

  return (
    <section className="rounded-xl border border-primary/25 bg-card p-5">
      <div className="text-xs font-medium text-primary">接下来重点验证</div>
      <h2 className="mt-1 text-lg font-semibold">{compact ? "当前最需要核对的事项" : "研究验证点"}</h2>
      <p className="mt-2 text-sm leading-6 text-muted-foreground">这是已有研究的核验清单，不是买卖信号，也不创建研究任务。</p>
      {error ? <p className="mt-3 text-sm text-destructive">{error}</p> : null}
      {!data && !error ? <p className="mt-3 text-sm text-muted-foreground">读取验证点…</p> : null}
      {data && shown.length === 0 ? (
        <p className="mt-3 text-sm leading-6 text-muted-foreground">当前没有足够结构化验证条件。</p>
      ) : null}
      {shown.length ? (
        <div className={`mt-3 grid gap-3 ${compact ? "" : "lg:grid-cols-2"}`}>
          {shown.map((item, index) => (
            <WatchpointCard key={`${item.category}-${item.title}-${index}`} item={item} compact={compact} />
          ))}
        </div>
      ) : null}
      {gaps.length ? (
        <div className="mt-4 rounded-lg border border-dashed p-3 text-sm leading-6 text-muted-foreground">
          <div className="text-xs font-medium">资料缺口</div>
          <ul className="mt-1 list-disc pl-5">
            {gaps.slice(0, compact ? 3 : 8).map((gap, index) => (
              <li key={`${gap.category}-${index}`}>{gap.description}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}
