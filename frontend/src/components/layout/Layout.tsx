import { useEffect, useState } from "react";
import { Link, Outlet, useLocation, useSearchParams } from "react-router";
import {
  Moon, PanelLeftClose, PanelLeftOpen, Sun, X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { safeGet, safeSet } from "@/lib/storage";
import { api, type SessionItem } from "@/lib/api";
import { useDarkMode } from "@/hooks/useDarkMode";
import { useWorkspaceMarket } from "@/hooks/useWorkspaceMarket";
import { useAgentStore } from "@/stores/agent";
import { BrandMark } from "@/components/common/BrandMark";
import { ConnectionBanner } from "@/components/layout/ConnectionBanner";
import {
  PRIMARY_NAVIGATION,
  UTILITY_NAVIGATION,
  getActivePrimaryNavigation,
  getSecondaryNavigation,
  isNavigationItemActive,
} from "@/components/layout/navigation";

export function Layout() {
  const { pathname } = useLocation();
  const [params] = useSearchParams();
  const { market, setMarket } = useWorkspaceMarket();
  const { dark, toggle } = useDarkMode();
  const [collapsed, setCollapsed] = useState(() => safeGet("hz-sidebar") === "collapsed");
  const [mobileOpen, setMobileOpen] = useState(false);
  const [sessions, setSessions] = useState<SessionItem[]>([]);
  const sseStatus = useAgentStore((s) => s.sseStatus);
  const sseRetryAttempt = useAgentStore((s) => s.sseRetryAttempt);
  const activeSession = params.get("session");
  const inAgent = pathname.startsWith("/ai/agent") || pathname.startsWith("/lab/agent") || pathname === "/agent";
  const inValueWorkspace = pathname.startsWith("/value");
  const activePrimary = getActivePrimaryNavigation(pathname);
  const secondaryNavigation = getSecondaryNavigation(pathname);

  useEffect(() => safeSet("hz-sidebar", collapsed ? "collapsed" : "expanded"), [collapsed]);
  useEffect(() => setMobileOpen(false), [pathname]);
  useEffect(() => {
    if (!inAgent) return;
    api.listSessions().then((items) => setSessions(Array.isArray(items) ? items : [])).catch(() => setSessions([]));
  }, [inAgent, activeSession]);
  useEffect(() => {
    if (inValueWorkspace && market !== "CN") setMarket("CN");
  }, [inValueWorkspace, market, setMarket]);

  return (
    <div className="flex h-screen overflow-hidden bg-background text-foreground">
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-[80] focus:rounded-md focus:bg-background focus:px-4 focus:py-2">跳到主要内容</a>
      {mobileOpen ? <button className="fixed inset-0 z-40 bg-black/45 md:hidden" onClick={() => setMobileOpen(false)} aria-label="关闭导航遮罩" /> : null}
      <aside className={cn(
        "fixed inset-y-0 left-0 z-50 flex shrink-0 flex-col border-r bg-card transition-[transform,width] duration-200 md:static md:z-auto md:translate-x-0",
        mobileOpen ? "translate-x-0" : "-translate-x-full",
        collapsed ? (mobileOpen ? "w-[248px] md:w-[68px]" : "w-[68px]") : "w-[248px]",
      )} aria-label="恒值投资侧边栏">
        <div className="flex h-16 items-center gap-2 border-b px-4">
          <Link to="/value" className="flex min-w-0 items-center gap-2" aria-label="恒值投资">
            <BrandMark className="h-8 w-8 shrink-0" />
            {(!collapsed || mobileOpen) ? <div className="min-w-0"><div className="truncate text-sm font-semibold">恒值投资</div><div className="text-[10px] tracking-wider text-muted-foreground">RESEARCH DESK</div></div> : null}
          </Link>
          <button onClick={() => setMobileOpen(false)} className="ml-auto rounded-md p-2 text-muted-foreground hover:bg-muted md:hidden" aria-label="关闭主导航"><X className="h-4 w-4" /></button>
        </div>
        <nav className="flex-1 overflow-y-auto px-2 py-3" aria-label="主导航">
          <div className="space-y-1">
            {PRIMARY_NAVIGATION.map(({ id, to, label, icon: Icon }) => {
              const active = activePrimary?.id === id;
              return <Link key={id} to={to} aria-label={label} aria-current={active ? "page" : undefined} title={collapsed && !mobileOpen ? label : undefined} className={cn("flex items-center rounded-lg px-3 py-2.5 text-sm transition", collapsed && !mobileOpen ? "justify-center" : "gap-3", active ? "bg-primary/10 font-medium text-primary" : "text-muted-foreground hover:bg-muted/70 hover:text-foreground")}><Icon aria-hidden="true" className="h-4 w-4 shrink-0" />{(!collapsed || mobileOpen) ? <span>{label}</span> : null}</Link>;
            })}
          </div>
        </nav>
        {inAgent && (!collapsed || mobileOpen) ? (
          <div className="max-h-52 overflow-y-auto border-t p-2">
            <div className="px-2 py-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">最近会话</div>
            {sessions.slice(0, 8).map((session) => <Link key={session.session_id} to={`/ai/agent?session=${session.session_id}`} className={cn("block truncate rounded-md px-2 py-1.5 text-xs", activeSession === session.session_id ? "bg-primary/10 text-primary" : "text-muted-foreground hover:bg-muted")}>{session.title || session.session_id.slice(0, 14)}</Link>)}
          </div>
        ) : null}
        <nav className="space-y-1 border-t px-2 py-2" aria-label="系统导航">
          {UTILITY_NAVIGATION.map(({ to, label, icon: Icon, matches }) => {
            const active = isNavigationItemActive(pathname, { to, matches });
            return <Link key={to} to={to} aria-label={label} aria-current={active ? "page" : undefined} title={collapsed && !mobileOpen ? label : undefined} className={cn("flex items-center rounded-lg px-3 py-2 text-sm transition", collapsed && !mobileOpen ? "justify-center" : "gap-3", active ? "bg-primary/10 font-medium text-primary" : "text-muted-foreground hover:bg-muted/70 hover:text-foreground")}><Icon aria-hidden="true" className="h-4 w-4 shrink-0" />{(!collapsed || mobileOpen) ? <span>{label}</span> : null}</Link>;
          })}
        </nav>
        <div className="flex items-center justify-between border-t p-2">
          <button onClick={toggle} className="rounded-md p-2 text-muted-foreground hover:bg-muted hover:text-foreground" title={dark ? "浅色模式" : "深色模式"}>{dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}</button>
          <button onClick={() => setCollapsed((v) => !v)} className="hidden rounded-md p-2 text-muted-foreground hover:bg-muted hover:text-foreground md:block" title={collapsed ? "展开侧栏" : "收起侧栏"}>{collapsed ? <PanelLeftOpen className="h-4 w-4" /> : <PanelLeftClose className="h-4 w-4" />}</button>
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <ConnectionBanner status={sseStatus} retryAttempt={sseRetryAttempt} />
        {secondaryNavigation ? (
          <div className="shrink-0 overflow-x-auto border-b bg-card/60 px-4 md:px-6">
            <nav className="flex min-w-max items-center gap-1 py-2" aria-label={`${secondaryNavigation.label}二级导航`}>
              {secondaryNavigation.secondary?.map((item) => {
                const active = isNavigationItemActive(pathname, item);
                return <Link key={item.to} to={item.to} aria-current={active ? "page" : undefined} className={cn("rounded-md px-3 py-1.5 text-sm transition", active ? "bg-foreground font-medium text-background" : "text-muted-foreground hover:bg-muted hover:text-foreground")}>{item.label}</Link>;
              })}
            </nav>
          </div>
        ) : null}
        <main id="main" className="min-h-0 flex-1 overflow-y-auto"><Outlet /></main>
      </div>
    </div>
  );
}
