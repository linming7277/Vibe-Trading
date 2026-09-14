import { useEffect, useState } from "react";
import { api, type CioReportSummary } from "@/lib/api";

/**
 * CIO 报告两行数据日期（2026-09-14）：
 * 「价格截至」= PRICE 块数据日（price_as_of）；「叙述截至」= 全文存档日（narrative_as_of）。
 * 字段缺失就不渲染对应行，绝不编日期；读取为只读 GET，不触发任何 LLM。
 */
export function CioReportAsOfCard({ stockCode }: { stockCode: string }) {
  const [report, setReport] = useState<CioReportSummary | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setReport(null);
    setFailed(false);
    api.getCioReport(stockCode)
      .then((result) => { if (!cancelled) setReport(result); })
      .catch(() => { if (!cancelled) setFailed(true); });
    return () => { cancelled = true; };
  }, [stockCode]);

  const priceAsOf = report?.price_as_of?.trim();
  const narrativeAsOf = report?.narrative_as_of?.trim();
  if (!priceAsOf && !narrativeAsOf) {
    return (
      <section className="rounded-xl border border-border bg-card p-5" data-testid="cio-as-of-card">
        <div className="text-xs font-medium text-primary">CIO 报告</div>
        <p className="mt-2 text-sm text-muted-foreground">
          {failed ? "CIO 报告暂时读取失败。" : "暂无 CIO 报告存档。"}
        </p>
      </section>
    );
  }

  return (
    <section className="rounded-xl border border-border bg-card p-5" data-testid="cio-as-of-card">
      <div className="text-xs font-medium text-primary">CIO 报告</div>
      <ul className="mt-2 space-y-1 text-sm">
        {priceAsOf ? <li>价格截至 {priceAsOf}</li> : null}
        {narrativeAsOf ? <li>叙述截至 {narrativeAsOf}</li> : null}
      </ul>
    </section>
  );
}
