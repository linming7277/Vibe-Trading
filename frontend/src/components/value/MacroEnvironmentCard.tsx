/**
 * 宏观环境摘要卡（/value 首页）：只显示环境一句 + 缺轴提示 + 「查看宏观」链接。
 * 不展示赛道、政策或龙头列表——宏观是环境快照，不是选股结论。
 * 变化信息在日报 macro_environment.changed/changes 中，不在本卡片。
 */
import { useEffect, useState } from "react";
import { Globe2 } from "lucide-react";
import { Link } from "react-router";
import { api, type MacroSectorProjection } from "@/lib/api";

export function MacroEnvironmentCard() {
  const [data, setData] = useState<MacroSectorProjection | null>(null);
  useEffect(() => {
    api.getMacroSectorProjection().then(setData).catch(() => setData(null));
  }, []);
  if (!data?.available || !data.macro) return null;

  const macro = data.macro;
  const tight = macro.regime === "收缩" || macro.regime === "滞胀";
  const missing = (macro.axes ?? []).filter((axis) => axis.state === "资料不足").length;

  return (
    <section className="rounded-xl border border-primary/25 bg-card p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-1.5 text-xs font-medium text-primary">
            <Globe2 className="h-3.5 w-3.5" />当前研究环境
          </div>
          <h2 className="mt-1 text-lg font-semibold">{macro.regime}</h2>
          <p className="mt-1 text-sm text-muted-foreground">{macro.regime_label}</p>
          {tight ? <p className="mt-1 text-sm text-amber-700">环境偏紧，重点观察更要看风险。</p> : null}
        </div>
        <div className="flex flex-col items-end gap-2">
          {data.as_of ? <span className="rounded bg-muted px-2 py-1 text-xs text-muted-foreground">{data.as_of}</span> : null}
          <Link to="/macro" className="text-sm text-primary underline-offset-2 hover:underline">查看宏观</Link>
        </div>
      </div>
      {missing > 0 ? (
        <p className="mt-3 text-xs text-muted-foreground">有 {missing} 个轴资料不足，整体判断需更谨慎。</p>
      ) : (
        <p className="mt-3 text-xs text-muted-foreground">五个轴资料完整。</p>
      )}
    </section>
  );
}
