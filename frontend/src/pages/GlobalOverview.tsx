import { Globe, LineChart } from "lucide-react";
import { PageHeader, WorkspacePage } from "@/components/workspace/WorkspaceUI";

const ROADMAP = [
  { title: "全球指数跟踪", desc: "美/欧/日/港等主要市场指数与宏观环境监控" },
  { title: "港美股标的", desc: "结合 Yahoo 行情与基本面数据的全球标的池" },
  { title: "全球资金流向", desc: "跨境资金、ETF 申赎与市场广度分析" },
];

export function GlobalOverview() {
  return (
    <WorkspacePage>
      <PageHeader
        eyebrow="GLOBAL / STRATEGY"
        title="全球策略"
        description="全球市场研究与策略占位页，当前为规划阶段，功能陆续接入中。"
        actions={
          <div className="flex items-center gap-1.5 rounded-full border bg-muted/40 px-3 py-1 text-xs text-muted-foreground">
            <span className="h-1.5 w-1.5 rounded-full bg-amber-500" />
            建设中
          </div>
        }
      />
      <section className="rounded-xl border bg-card p-6 shadow-sm">
        <div className="mb-4 flex items-center gap-2 text-sm font-medium text-muted-foreground">
          <Globe className="h-4 w-4" />
          规划路线
        </div>
        <div className="grid gap-3 md:grid-cols-3">
          {ROADMAP.map((item) => (
            <div key={item.title} className="flex items-start gap-3 rounded-lg border bg-muted/30 p-4">
              <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-background text-primary shadow-sm">
                <LineChart className="h-4 w-4" />
              </div>
              <div>
                <div className="text-sm font-medium">{item.title}</div>
                <div className="mt-1 text-xs leading-relaxed text-muted-foreground">{item.desc}</div>
              </div>
            </div>
          ))}
        </div>
      </section>
    </WorkspacePage>
  );
}
