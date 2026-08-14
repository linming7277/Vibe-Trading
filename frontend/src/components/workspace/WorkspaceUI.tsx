import type { ReactNode } from "react";
import { AlertCircle, Clock3, Database, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import type { MarketCode, SourceStatus } from "@/lib/api";
import { useWorkspaceMarket } from "@/hooks/useWorkspaceMarket";

export const MARKET_LABELS: Record<MarketCode, string> = { CN: "A股", HK: "港股", US: "美股" };

export function WorkspacePage({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn("mx-auto w-full max-w-[1480px] space-y-6 p-5 md:p-8", className)}>{children}</div>;
}

export function PageHeader({ eyebrow, title, description, actions }: { eyebrow?: string; title: string; description?: string; actions?: ReactNode }) {
  return (
    <header className="flex flex-col gap-4 border-b border-border/70 pb-5 lg:flex-row lg:items-end lg:justify-between">
      <div>
        {eyebrow ? <div className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-primary">{eyebrow}</div> : null}
        <h1 className="text-2xl font-semibold tracking-tight md:text-3xl">{title}</h1>
        {description ? <p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">{description}</p> : null}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </header>
  );
}

export function MarketTabs() {
  const { market, setMarket } = useWorkspaceMarket();
  return (
    <div className="inline-flex rounded-lg border bg-card p-1" aria-label="市场切换">
      {(Object.keys(MARKET_LABELS) as MarketCode[]).map((code) => (
        <button key={code} onClick={() => setMarket(code)} className={cn("rounded-md px-3 py-1.5 text-sm transition", market === code ? "bg-foreground text-background shadow-sm" : "text-muted-foreground hover:text-foreground")}>{MARKET_LABELS[code]}</button>
      ))}
    </div>
  );
}

export function SourceBadge({ status, asOf }: { status?: SourceStatus; asOf?: string }) {
  const sample = status === "sample";
  return (
    <span className={cn("inline-flex items-center gap-1.5 rounded-full border px-2 py-1 text-[11px] font-medium", sample ? "border-warning/30 bg-warning/10 text-warning" : "border-success/30 bg-success/10 text-success")}>
      {sample ? <Database className="h-3 w-3" /> : <Clock3 className="h-3 w-3" />}
      {sample ? "示例数据" : "已验证"}{asOf ? ` · ${asOf}` : ""}
    </span>
  );
}

export function MetricCard({ label, value, hint, icon, tone = "default" }: { label: string; value: ReactNode; hint?: string; icon?: ReactNode; tone?: "default" | "positive" | "negative" }) {
  return (
    <article className="rounded-xl border bg-card p-4 shadow-sm">
      <div className="flex items-center justify-between text-xs font-medium text-muted-foreground"><span>{label}</span>{icon}</div>
      <div className={cn("mt-3 text-2xl font-semibold tracking-tight", tone === "positive" && "text-market-up", tone === "negative" && "text-market-down")}>{value}</div>
      {hint ? <p className="mt-1 text-xs text-muted-foreground">{hint}</p> : null}
    </article>
  );
}

export function ScoreBar({ value, compact = false }: { value: number; compact?: boolean }) {
  const color = value >= 80 ? "bg-success" : value >= 65 ? "bg-primary" : value >= 50 ? "bg-warning" : "bg-danger";
  return (
    <div className="flex items-center gap-2">
      <div className={cn("overflow-hidden rounded-full bg-muted", compact ? "h-1.5 w-16" : "h-2 flex-1")}><div className={cn("h-full rounded-full", color)} style={{ width: `${Math.max(0, Math.min(100, value))}%` }} /></div>
      <span className="w-10 text-right font-mono text-xs font-semibold">{value.toFixed(1)}</span>
    </div>
  );
}

export function LoadingState({ label = "正在加载工作台…" }: { label?: string }) {
  return <div className="flex min-h-64 items-center justify-center gap-2 rounded-xl border border-dashed text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />{label}</div>;
}

export function EmptyState({ title, body }: { title: string; body?: string }) {
  return <div className="flex min-h-52 flex-col items-center justify-center rounded-xl border border-dashed p-8 text-center"><AlertCircle className="h-7 w-7 text-muted-foreground" /><h3 className="mt-3 font-medium">{title}</h3>{body ? <p className="mt-1 max-w-lg text-sm text-muted-foreground">{body}</p> : null}</div>;
}

export function formatNumber(value: unknown, digits = 2) {
  return typeof value === "number" ? new Intl.NumberFormat("zh-CN", { maximumFractionDigits: digits }).format(value) : String(value ?? "—");
}
