/**
 * 宏观环境页（独立路由 /macro）：只看环境，不选行业，不进龙头名单。
 * 数据源：getMacroSectorProjection（后端 macro_sector_projection.py 同一份快照）。
 */
import { useEffect, useState } from "react";
import { Globe2, Loader2 } from "lucide-react";
import { api, type MacroSectorProjection } from "@/lib/api";
import { cn } from "@/lib/utils";
import { PageHeader, WorkspacePage } from "@/components/workspace/WorkspaceUI";

export function Macro() {
  const [data, setData] = useState<MacroSectorProjection | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    setLoading(true); setError(""); setData(null);
    api.getMacroSectorProjection()
      .then(setData)
      .catch((reason) => setError(reason instanceof Error ? reason.message : String(reason)))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <WorkspacePage><PageHeader eyebrow="MACRO" title="宏观环境" description="当前经济和资金面的松紧，不是选行业，更不是交易指令。" /><div className="flex h-40 items-center justify-center text-sm text-muted-foreground"><Loader2 className="mr-2 h-4 w-4 animate-spin" />读取宏观快照…</div></WorkspacePage>;
  if (error || !data) return <WorkspacePage><PageHeader eyebrow="MACRO" title="宏观环境" description="当前经济和资金面的松紧，不是选行业，更不是交易指令。" /><div className="rounded-xl border border-dashed bg-card p-8 text-center"><p className="text-sm text-muted-foreground">宏观资料暂不可用：{error || "无快照"}</p></div></WorkspacePage>;

  const macro = data.macro;
  const regime = macro?.regime ?? "资料不足";
  const regimeLabel = macro?.regime_label ?? "资料不足，先不据此改变研究节奏。";
  const axes = macro?.axes ?? [];
  const missingAxes = axes.filter((axis) => axis.state === "资料不足");
  const presentAxes = axes.filter((axis) => axis.state !== "资料不足");
  const dataQuality = data.data_quality;
  const tight = regime === "收缩" || regime === "滞胀";

  const axisTone = (state: string) =>
    state === "偏暖" ? "text-emerald-600" : state === "偏冷" ? "text-blue-600" : "text-muted-foreground";

  return <WorkspacePage>
    <PageHeader eyebrow="MACRO" title="宏观环境" description="当前经济和资金面的松紧，不是选行业，更不是交易指令。" />
    <section className="rounded-xl border border-primary/25 bg-card p-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-1.5 text-xs font-medium text-primary"><Globe2 className="h-3.5 w-3.5" />当前环境</div>
          <h2 className="mt-2 text-2xl font-semibold">{regime}</h2>
          <p className="mt-1 max-w-2xl text-sm leading-6 text-muted-foreground">{regimeLabel}</p>
          {tight ? <p className="mt-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-800">环境偏紧，重点观察更要看风险。</p> : null}
        </div>
        {data.as_of ? <span className="rounded bg-muted px-2 py-1 text-xs text-muted-foreground">数据截至 {data.as_of}</span> : null}
      </div>

      <div className="mt-5 grid gap-3 sm:grid-cols-5">
        {axes.map((axis) => (
          <div key={axis.key} className="rounded-lg border border-border p-3">
            <div className="text-xs text-muted-foreground">{axis.label}</div>
            <strong className={cn("mt-1 block text-lg", axisTone(axis.state))}>{axis.state}</strong>
            <div className="mt-1 text-[11px] text-muted-foreground">
              {axis.direction !== "数据不足" ? axis.direction : ""}
            </div>
          </div>
        ))}
      </div>

      {missingAxes.length > 0 ? (
        <div className="mt-4 rounded-lg border border-amber-500/25 bg-amber-500/[0.04] p-3">
          <div className="text-sm font-medium text-amber-800">缺 {missingAxes.length} 个轴的资料</div>
          <p className="mt-1 text-sm leading-5 text-muted-foreground">
            {missingAxes.map((axis) => axis.label).join("、")}当前资料不足，不影响其他轴的判断，但整体结论需要更谨慎。
            {presentAxes.length < 3 ? "可用轴不足三个，整体判断以「资料不足」为准。" : ""}
          </p>
        </div>
      ) : (
        <p className="mt-4 text-xs text-muted-foreground">五个轴资料完整。</p>
      )}

      {dataQuality && dataQuality.status !== "READY" ? (
        <div className="mt-4 rounded-lg border border-border bg-muted/30 p-3 text-sm text-muted-foreground">
          <div className="font-medium text-foreground">数据覆盖：{dataQuality.status}</div>
          <p className="mt-1 leading-5">{dataQuality.reason}</p>
          {dataQuality.missing_series_labels?.length ? (
            <p className="mt-1 text-xs">缺序列：{dataQuality.missing_series_labels.join("、")}</p>
          ) : null}
        </div>
      ) : null}
    </section>

    <p className="rounded-lg bg-muted/40 p-3 text-xs leading-5 text-muted-foreground">
      宏观环境只说明当前经济和资金面的松紧，不预测明天涨跌，也不改变哪些公司进入研究名单。名单由龙头资格和低估条件决定，与环境无关。
    </p>
  </WorkspacePage>;
}
