import { ArrowLeft, ArrowRight, CheckCircle2, CircleHelp, Database, Filter, Layers3, Scale, ShieldAlert, Trophy } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router";

const dimensions = [
  { weight: "25%", title: "规模与经营地位", description: "看市值、营业收入和净利润。它衡量的是公司在这个细分行业里的体量，不等同于市场份额。" },
  { weight: "20%", title: "盈利能力", description: "看 ROE、毛利率和净利率。简单说，就是公司能不能把收入稳定地变成利润。" },
  { weight: "15%", title: "成长稳定性", description: "看营收和利润的多年增长、增长是否连续、波动是否过大。增长快但忽上忽下，不会只看速度。" },
  { weight: "15%", title: "现金流质量", description: "看经营现金流是否能跟上利润，以及多年是否保持正现金流。账面盈利不等于钱真正收回来。" },
  { weight: "15%", title: "同行相对估值", description: "看 PE、PB、股息率在同行中的位置。这里只比较“相对是否更便宜”，不直接给出合理股价。" },
  { weight: "10%", title: "财务稳健与波动", description: "看负债、股东人数变化和 Beta。它是风险代理指标，不替代完整的公司治理尽调。" },
];

function Step({ number, title, children, icon: Icon }: { number: string; title: string; children: ReactNode; icon: LucideIcon }) {
  return <section className="relative grid gap-3 border-l-2 border-primary/20 pl-5 sm:grid-cols-[44px_1fr] sm:gap-5 sm:pl-0">
    <div className="absolute -left-[17px] top-0 flex h-8 w-8 items-center justify-center rounded-full bg-primary text-xs font-bold text-primary-foreground sm:static">{number}</div>
    <div className="pb-7"><div className="flex items-center gap-2"><Icon className="h-4 w-4 text-primary" /><h2 className="font-semibold">{title}</h2></div><div className="mt-2 text-sm leading-7 text-muted-foreground">{children}</div></div>
  </section>;
}

export function ValueLeaderMethodology() {
  return <div className="mx-auto w-full max-w-5xl space-y-5 p-3 md:p-5">
    <nav aria-label="筛选说明路径" className="flex items-center gap-2 text-sm text-muted-foreground"><Link to="/value/leaders" className="inline-flex items-center gap-1 hover:text-primary"><ArrowLeft className="h-4 w-4" />行业候选列表</Link><ArrowRight className="h-3.5 w-3.5" /><span className="text-foreground">筛选说明</span></nav>

    <header className="rounded-2xl border border-primary/20 bg-primary/[0.035] px-5 py-6 md:px-7 md:py-8">
      <div className="flex items-start gap-3"><CircleHelp className="mt-1 h-6 w-6 shrink-0 text-primary" /><div><p className="text-xs font-semibold tracking-wider text-primary">价值投资 · 使用说明</p><h1 className="mt-1 text-2xl font-semibold tracking-tight">量化候选公司是怎样选出来的？</h1><p className="mt-3 max-w-3xl text-sm leading-7 text-muted-foreground">这套流程的目的，是从每个细分行业中找出值得优先研究的公司，而不是直接给出买入结论。它先保证比较对象可用，再做同行比较，最后只保留每个行业前两名进入研究池。</p></div></div>
      <div className="mt-5 grid gap-2 text-xs sm:grid-cols-4"><div className="rounded-lg bg-background/80 px-3 py-2"><strong className="block text-foreground">同业比较</strong><span className="text-muted-foreground">不跨行业拼分</span></div><div className="rounded-lg bg-background/80 px-3 py-2"><strong className="block text-foreground">20项原始指标</strong><span className="text-muted-foreground">财务、估值与风险</span></div><div className="rounded-lg bg-background/80 px-3 py-2"><strong className="block text-foreground">六类能力</strong><span className="text-muted-foreground">按权重综合排序</span></div><div className="rounded-lg bg-background/80 px-3 py-2"><strong className="block text-foreground">每行业前2</strong><span className="text-muted-foreground">进入后续研究</span></div></div>
    </header>

    <section className="rounded-xl border border-border bg-card p-5 md:p-6"><h2 className="text-lg font-semibold">先看全流程</h2><div className="mt-4 grid gap-2 md:grid-cols-4">{[["1", "检查是否可比", "剔除数据不完整或风险明显的公司"], ["2", "只和同行比较", "在同一个通达信三级/末级行业中排名"], ["3", "综合六类能力", "把20项指标归成六个容易理解的维度"], ["4", "保留前两名", "进入研究池，继续做公司研究和估值"]].map(([number, title, description]) => <div key={number} className="rounded-lg border border-border bg-background p-3"><span className="text-xs font-bold text-primary">第 {number} 步</span><h3 className="mt-1 text-sm font-semibold">{title}</h3><p className="mt-1 text-xs leading-5 text-muted-foreground">{description}</p></div>)}</div></section>

    <section className="rounded-xl border border-border bg-card p-5 md:p-7"><h2 className="mb-6 text-lg font-semibold">逐步说明</h2><div className="space-y-1">
      <Step number="1" title="先确认这家公司适合参与比较" icon={Filter}>不是所有股票都会直接被放进排名。系统会先排除 <strong className="text-foreground">ST 或退市风险</strong>、上市不足 20 个交易日、行情超过 5 个交易日没有更新，以及缺少年度专业财务历史的公司。某家公司没有参与评分，不等于它一定不好，通常表示现阶段没有足够可靠的数据进行公平比较。</Step>
      <Step number="2" title="只在同一个细分行业里比较" icon={Layers3}>比如“动力煤”的公司只和“动力煤”同行比较，不和银行、白酒或半导体比较。因为不同行业的利润率、估值和增长节奏本来就不同。页面上的 80 分是 <strong className="text-foreground">同行相对位置</strong>，不是“绝对好公司”的 80 分，也不能拿两个行业的分数直接对比。</Step>
      <Step number="3" title="把原始数据变成公平的同行位置" icon={Database}>系统会先处理特别高或特别低的极端值，再把每项指标转换为同行分位。通俗地说，它不只问“你的 ROE 是多少”，还会问“在这一行业里，你的 ROE 排在什么位置”。这样可以减少行业规模差异和个别极端数据对结果的影响。</Step>
      <Step number="4" title="用六类能力合成一个同行相对分" icon={Scale}>20 项指标会按下面六类能力归类并加权。每一类里也有自己的内部权重；展开公司详情里的“评分贡献与原始依据”，可以看到原始值、同行分位和每项指标的实际权重。</Step>
    </div>
      <div className="mt-1 grid gap-3 md:grid-cols-2">{dimensions.map((dimension) => <article key={dimension.title} className="rounded-lg border border-border bg-background p-4"><div className="flex items-center justify-between gap-3"><h3 className="font-medium">{dimension.title}</h3><span className="rounded bg-primary/10 px-2 py-1 text-xs font-semibold text-primary">总权重 {dimension.weight}</span></div><p className="mt-2 text-xs leading-5 text-muted-foreground">{dimension.description}</p></article>)}</div>
      <div className="mt-6 space-y-1">
        <Step number="5" title="缺数据时，不会把它当成 0 分" icon={ShieldAlert}>如果某一项指标缺失，系统不会用 0 分冒充真实表现；当剩余数据仍达到最低覆盖要求时，只会在已有指标之间重新分配权重，并在公司详情中明确标记“缺失重加权”。如果整体可用维度不足 80%，公司不会进入正式排名。</Step>
        <Step number="6" title="每个行业只取前两名进入研究池" icon={Trophy}>前两名的意思是“在这一个细分行业的当前数据快照中，最值得优先研究的两个量化候选”，不是自动买入名单。行业只有两三家可评分公司时，页面会出现小样本警告；这时排名只能作为线索，需要更多人工核对。</Step>
      </div>
    </section>

    <section className="rounded-xl border border-border bg-card p-5 md:p-6"><h2 className="text-lg font-semibold">看到结果后，建议怎样用？</h2><ol className="mt-3 space-y-3 text-sm leading-6 text-muted-foreground"><li className="flex gap-3"><CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-primary" /><span>先看候选卡片的“靠前”和“复核”原因，知道它为什么排在前面，也知道弱项在哪里。</span></li><li className="flex gap-3"><CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-primary" /><span>点击“查看全行业排名与排除原因”，确认它到底领先多少同行，以及有多少公司未参与比较。</span></li><li className="flex gap-3"><CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-primary" /><span>打开公司研究，继续看财报、经营变化、估值和风险；低估龙头池只帮助优先筛选价格进入低估区域的行业龙头。</span></li></ol><div className="mt-5 flex flex-wrap gap-2"><Link to="/value/leaders" className="inline-flex items-center gap-2 rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground">返回行业候选 <ArrowRight className="h-4 w-4" /></Link><Link to="/value" className="inline-flex items-center gap-2 rounded-md border border-border px-3 py-2 text-sm hover:bg-muted">查看低估龙头池</Link></div></section>

    <p className="px-1 text-xs leading-5 text-muted-foreground">数据来自通达信本地行情、基础数据和专业财务缓存。候选结果受数据日期、行业成分和数据覆盖率影响；它用于研究排序，不构成投资建议。</p>
  </div>;
}
