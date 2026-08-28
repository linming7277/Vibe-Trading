import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, CheckCircle2, Database, FileText, Landmark, Loader2, RefreshCw } from "lucide-react";
import { Link } from "react-router";
import { api, type TdxStatus, type ValueDataStatus } from "@/lib/api";
import { PageHeader, WorkspacePage } from "@/components/workspace/WorkspaceUI";

type Requirement = {
  group: "必需基座" | "宏观与政策" | "深度研究补充";
  name: string;
  required: "必须" | "建议";
  purpose: string;
  source: string;
  datasets: string[];
  missing: string;
};

const REQUIREMENTS: Requirement[] = [
  { group: "必需基座", name: "A股证券目录与 L1/L2/L3 行业归属", required: "必须", purpose: "确定每家公司属于哪个可比行业，并限定龙头排名只能在同一三级行业内比较。", source: "通达信行业目录、板块与成分股缓存", datasets: ["sectors"], missing: "不能建立可比同行池，不生成正式行业龙头排名。" },
  { group: "必需基座", name: "三级行业成分与历史快照", required: "必须", purpose: "保存某一日期的行业成员，避免把今天的成分股误用于过去的行业研究。", source: "通达信三级行业成分；首次成功运行后逐日保存", datasets: [], missing: "早于首次快照的日期只标记为“历史成分不可用”，不伪造历史回放。" },
  { group: "必需基座", name: "收盘行情与前复权日线", required: "必须", purpose: "计算行业和公司收益、波动、回撤、成交活跃度、技术趋势与历史估值位置。", source: "通达信优先；缺失日线由 AKShare 增量补齐", datasets: ["quote", "history"], missing: "行情过期时只能提示数据待更新，暂停买卖点判断。" },
  { group: "必需基座", name: "市场基准与主要指数", required: "必须", purpose: "计算相对收益，并作为宏观风险偏好和行业动量的比较基准。", source: "通达信指数与历史日线缓存", datasets: ["index", "indices"], missing: "相关收益与市场宽度只能标记资料不足，不以零收益替代。" },
  { group: "必需基座", name: "专业财务历史（按公告日）", required: "必须", purpose: "计算营收、利润、ROE、毛利率、净利率、现金流、负债、资本开支和长期趋势。", source: "通达信专业财务包 `vipdoc/cw/gpcw*.dat`", datasets: ["financial_history"], missing: "不使用基础财务快照伪造历史财报；龙头、行业和公司结论会明确资料不足。" },
  { group: "必需基座", name: "最新估值、股本与分红", required: "必须", purpose: "提供 PE、PB、股息率、市值、股本变化和分红事实，用于价值位置与股东回报研究。", source: "通达信实时快照、扩展财务、分红与股本详情", datasets: ["fundamental"], missing: "估值比较和合理价值区间会降低覆盖；缺失值不会当作 0。" },
  { group: "宏观与政策", name: "中国宏观指标与发布时间", required: "必须", purpose: "判断增长、通胀、流动性、信用和金融条件，并匹配行业宏观暴露。", source: "国家统计局、人民银行等官方发布；AKShare 仅作适配", datasets: ["macro"], missing: "行业正式分数会标记“宏观资料待补”，不生成看似完整的赛道排名。" },
  { group: "宏观与政策", name: "官方政策文件与行业影响", required: "建议", purpose: "记录政策方向、强度、期限和行业敏感度，作为赛道研究的可追溯背景。", source: "国务院政策文件库，补充发改委、工信部官方页面", datasets: ["policy"], missing: "政策适配项为空；不会由模型凭常识补造政策利好或利空。" },
  { group: "深度研究补充", name: "公司定期报告与经营资料", required: "建议", purpose: "支持主营业务、竞争优势证据、经营变化、资本配置和核心逻辑的深入核验。", source: "已保存的 CNINFO 定期报告、公司经营研究和证据库", datasets: [], missing: "只保留财务事实和数据缺口，不能确认品牌、技术、渠道或护城河。" },
  { group: "深度研究补充", name: "研究版本、来源与数据日期", required: "必须", purpose: "让每项结论可追溯到输入快照、公告日期、公式版本和来源。", source: "价值线研究库与已发布缓存", datasets: [], missing: "页面只显示可验证事实；不能把没有来源的分析写成确定结论。" },
];

function statusText(status?: string | null) {
  if (status === "ready") return "已就绪";
  if (status === "running") return "更新中";
  if (status === "partial") return "部分可用";
  if (status === "failed") return "更新失败";
  if (status === "needs_professional_finance") return "需要专业财务包";
  return "尚未读取";
}

function statusTone(status?: string | null) {
  if (status === "ready") return "border-emerald-500/30 bg-emerald-500/5 text-emerald-800";
  if (status === "running") return "border-primary/30 bg-primary/5 text-primary";
  if (status === "partial" || status === "needs_professional_finance") return "border-amber-500/30 bg-amber-500/5 text-amber-800";
  if (status === "failed") return "border-red-500/30 bg-red-500/5 text-red-800";
  return "border-border bg-muted/40 text-muted-foreground";
}

function formatTime(value?: string | null) {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString("zh-CN", { hour12: false });
}

function moduleFor(requirement: Requirement, tdx: TdxStatus | null, value: ValueDataStatus | null) {
  if (requirement.datasets.includes("financial_history")) {
    const module = value?.modules.find((item) => item.code === "financial_history");
    const packageStatus = value?.professional_finance.status;
    return { status: module?.status || packageStatus, date: module?.last_success_at || module?.updated_at, detail: value?.professional_finance.file_count ? `专业财务包 ${value.professional_finance.file_count} 个报告期` : "未检测到可用专业财务包" };
  }
  const valueModule = value?.modules.find((item) => requirement.datasets.includes(item.code));
  if (valueModule) return { status: valueModule.status, date: valueModule.last_success_at || valueModule.updated_at, detail: valueModule.message || `${valueModule.item_count.toLocaleString("zh-CN")} 条已缓存记录` };
  const tdxModule = tdx?.modules.find((item) => requirement.datasets.includes(item.code));
  if (tdxModule) return { status: tdxModule.status, date: tdxModule.updated_at, detail: tdxModule.message || `${tdxModule.item_count.toLocaleString("zh-CN")} 条已缓存记录` };
  if (requirement.name === "三级行业成分与历史快照") return { status: "partial", date: null, detail: "从首次成功行业更新开始留存；当前无独立覆盖统计。" };
  if (requirement.name === "公司定期报告与经营资料") return { status: "partial", date: null, detail: "仅部分公司已保存材料；尚无全市场统一覆盖统计。" };
  return { status: undefined, date: null, detail: "当前状态未单独汇总；请在对应研究页面查看资料边界。" };
}

function RequirementRow({ item, tdx, value }: { item: Requirement; tdx: TdxStatus | null; value: ValueDataStatus | null }) {
  const current = moduleFor(item, tdx, value);
  return <article className="grid gap-3 border-b px-5 py-4 last:border-b-0 lg:grid-cols-[minmax(220px,.95fr)_minmax(260px,1.25fr)_minmax(220px,.9fr)_minmax(190px,.75fr)]">
    <div><div className="flex flex-wrap items-center gap-2"><h3 className="font-medium">{item.name}</h3><span className={item.required === "必须" ? "rounded-full bg-primary/10 px-2 py-0.5 text-[11px] text-primary" : "rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground"}>{item.required}</span></div><p className="mt-2 text-xs leading-5 text-muted-foreground">来源：{item.source}</p></div>
    <div><div className="text-xs font-medium text-muted-foreground">用于什么</div><p className="mt-1 text-sm leading-6">{item.purpose}</p><p className="mt-2 text-xs leading-5 text-amber-800">缺失时：{item.missing}</p></div>
    <div className={`rounded-lg border p-3 text-sm ${statusTone(current.status)}`}><div className="flex items-center gap-2 font-medium">{current.status === "ready" ? <CheckCircle2 className="h-4 w-4" /> : <AlertTriangle className="h-4 w-4" />}{statusText(current.status)}</div><p className="mt-2 text-xs leading-5 opacity-90">{current.detail}</p></div>
    <div><div className="text-xs font-medium text-muted-foreground">最近成功 / 资料日期</div><p className="mt-1 text-sm">{formatTime(current.date)}</p><p className="mt-2 text-xs leading-5 text-muted-foreground">实际计算仍以每条数据自己的公告日、交易日或发布日期为准。</p></div>
  </article>;
}

export function ValueLineDataRequirements() {
  const [tdx, setTdx] = useState<TdxStatus | null>(null);
  const [value, setValue] = useState<ValueDataStatus | null>(null);
  const [loading, setLoading] = useState(true);

  const load = async () => {
    setLoading(true);
    const [tdxResult, valueResult] = await Promise.allSettled([api.getTdxStatus(), api.getValueDataStatus()]);
    setTdx(tdxResult.status === "fulfilled" ? tdxResult.value : null);
    setValue(valueResult.status === "fulfilled" ? valueResult.value : null);
    setLoading(false);
  };

  useEffect(() => { void load(); }, []);
  const groups = useMemo(() => ["必需基座", "宏观与政策", "深度研究补充"] as const, []);

  return <WorkspacePage>
    <PageHeader
      eyebrow="VALUE LINE / DATA REQUIREMENTS"
      title="价值线资料清单"
      description="逐项说明价值线依赖什么资料、来自哪里、用来做什么，以及资料缺失时系统会如何处理。这里不触发更新或重算。"
      actions={<><Link to="/models/data" className="inline-flex items-center gap-2 rounded-lg border px-3 py-2 text-sm hover:bg-muted"><Database className="h-4 w-4" />打开数据中心</Link><button type="button" onClick={() => void load()} disabled={loading} className="inline-flex items-center gap-2 rounded-lg bg-primary px-3 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"><RefreshCw className={loading ? "h-4 w-4 animate-spin" : "h-4 w-4"} />刷新状态</button></>}
    />

    <section className="rounded-xl border border-primary/25 bg-primary/[0.03] p-5"><div className="flex gap-3"><Landmark className="mt-0.5 h-5 w-5 shrink-0 text-primary" /><div><h2 className="font-semibold">先看资料是否完整，再看研究结论</h2><p className="mt-2 max-w-4xl text-sm leading-6 text-muted-foreground">价值线只使用已保存、可追溯的数据。专业财务按公告日使用；历史行业成分从首次留存日开始可严格回放；行情、宏观或政策缺失时，会显示资料不足或暂停对应判断，不会用零值、旧快照或模型常识补全。</p></div></div></section>

    {loading && !tdx && !value ? <div className="flex min-h-48 items-center justify-center gap-2 rounded-xl border border-dashed text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />正在读取价值线资料状态…</div> : null}

    {groups.map((group) => <section key={group} className="overflow-hidden rounded-xl border bg-card shadow-sm"><div className="border-b bg-muted/30 px-5 py-4"><h2 className="font-semibold">{group}</h2><p className="mt-1 text-sm text-muted-foreground">{group === "必需基座" ? "没有这些数据，不能形成可信的行业与公司基础判断。" : group === "宏观与政策" ? "用于从宏观状态走向赛道选择；政策资料不足不会被臆测补齐。" : "用于把量化候选变成可核验的公司研究；不是所有公司都已完整覆盖。"}</p></div><div className="hidden grid-cols-[minmax(220px,.95fr)_minmax(260px,1.25fr)_minmax(220px,.9fr)_minmax(190px,.75fr)] gap-3 border-b bg-muted/20 px-5 py-2 text-xs font-medium text-muted-foreground lg:grid"><span>资料</span><span>用途与缺失边界</span><span>当前缓存状态</span><span>日期说明</span></div>{REQUIREMENTS.filter((item) => item.group === group).map((item) => <RequirementRow key={item.name} item={item} tdx={tdx} value={value} />)}</section>)}

    <section className="rounded-xl border bg-card p-5"><div className="flex items-start gap-3"><FileText className="mt-0.5 h-5 w-5 text-primary" /><div><h2 className="font-semibold">范围边界</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">本页列的是当前价值线所需或可增强的资料，并不表示每一项都已经覆盖全市场。公司公告、市场份额、专利质量、客户认证、管理层与治理等深度资料目前仍是部分覆盖或未结构化资料；页面会如实显示，不会把龙头排名直接解释为护城河、管理质量或交易建议。</p></div></div></section>
  </WorkspacePage>;
}
