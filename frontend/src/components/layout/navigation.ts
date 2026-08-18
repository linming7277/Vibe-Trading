import type { LucideIcon } from "lucide-react";
import { Activity, BarChart3, Bot, Database, FlaskConical, Globe, Scale, Settings, Users } from "lucide-react";

export interface SecondaryNavigationItem {
  to: string;
  label: string;
  matches?: string[];
}

export interface PrimaryNavigationItem {
  id: "market" | "value" | "emotion" | "global" | "simulation" | "ai";
  to: string;
  label: string;
  icon: LucideIcon;
  matches: string[];
  secondary?: SecondaryNavigationItem[];
}

export interface UtilityNavigationItem {
  to: string;
  label: string;
  icon: LucideIcon;
  matches?: string[];
}

export const PRIMARY_NAVIGATION: PrimaryNavigationItem[] = [
  {
    id: "market", to: "/market/overview", label: "市场行情", icon: BarChart3,
    matches: ["/market"],
    secondary: [
      { to: "/market/overview", label: "市场全景" },
      { to: "/market/ranks", label: "行情榜单" },
      { to: "/market/sectors", label: "板块行情" },
    ],
  },
  {
    id: "value", to: "/value", label: "价值投资", icon: Scale,
    matches: ["/value", "/company"],
    secondary: [
      { to: "/value", label: "行业龙头" },
      { to: "/value/research", label: "公司研究" },
      { to: "/value/valuation", label: "估值与买卖点" },
      { to: "/value/plans", label: "投委与计划" },
      { to: "/value/monitor", label: "监控事件" },
    ],
  },
  {
    id: "emotion", to: "/emotion/temperature", label: "情绪交易", icon: Activity,
    matches: ["/emotion"],
    secondary: [
      { to: "/emotion/temperature", label: "情绪温度" },
      { to: "/emotion/cycle", label: "情绪周期" },
      { to: "/emotion/sectors", label: "板块热度" },
      { to: "/emotion/short", label: "短线候选" },
      { to: "/emotion/swing", label: "波段候选" },
      { to: "/emotion/plans", label: "情绪计划" },
    ],
  },
  {
    id: "global", to: "/global", label: "全球策略", icon: Globe,
    matches: ["/global"],
    secondary: [
      { to: "/global", label: "全球全景" },
    ],
  },
  {
    id: "simulation", to: "/simulation/accounts", label: "模拟验证", icon: FlaskConical,
    matches: ["/simulation", "/backtests", "/runs", "/compare", "/portfolio", "/signals"],
    secondary: [
      { to: "/simulation/accounts", label: "模拟账户" },
      { to: "/simulation/signals", label: "信号日志" },
      { to: "/simulation/backtests", label: "策略回测" },
      { to: "/simulation/compare", label: "归因对比" },
      { to: "/simulation/decay", label: "策略衰减" },
    ],
  },
  {
    id: "ai", to: "/ai/agent", label: "AI 研究", icon: Bot,
    matches: ["/ai", "/committee", "/reports", "/lab/agent", "/lab/scheduled", "/agent", "/scheduled"],
    secondary: [
      { to: "/ai/agent", label: "AI 对话" },
      { to: "/ai/value-committee", label: "价值委员会" },
      { to: "/ai/emotion-committee", label: "情绪委员会" },
      { to: "/ai/reports", label: "研究报告" },
      { to: "/ai/scheduled", label: "定时研究" },
    ],
  },
];

export const UTILITY_NAVIGATION: UtilityNavigationItem[] = [
  { to: "/models/data", label: "数据与模型", icon: Database, matches: ["/models", "/data", "/lab/alpha", "/lab/correlation", "/formula", "/funds"] },
  { to: "/settings/researchers", label: "研究员设置", icon: Users, matches: ["/settings/researchers", "/ai/agents/settings"] },
  { to: "/settings", label: "设置", icon: Settings, matches: ["=/settings"] },
];

const MODELS_NAVIGATION = {
  id: "models" as const,
  to: "/models/data",
  label: "数据与模型",
  icon: Database,
  matches: ["/models"],
  secondary: [
    { to: "/models/data", label: "数据中心" },
    { to: "/models/factors", label: "因子库" },
    { to: "/models/formulas", label: "公式选股" },
    { to: "/models/strategies", label: "策略版本" },
    { to: "/models/correlation", label: "相关性分析" },
    { to: "/models/evidence", label: "证据库" },
  ],
};

const DETAIL_ROUTES = [
  (pathname: string) => pathname.startsWith("/company/"),
  (pathname: string) => pathname.startsWith("/ai/reports/"),
  (pathname: string) => pathname.startsWith("/ai/committees/"),
  (pathname: string) => pathname.startsWith("/simulation/runs/"),
  (pathname: string) => pathname.startsWith("/market/sectors/"),
  (pathname: string) => pathname.startsWith("/reports/"),
  (pathname: string) => pathname.startsWith("/committee/"),
  (pathname: string) => pathname.startsWith("/runs/"),
];

function matchesPath(pathname: string, target: string) {
  if (target.startsWith("=")) return pathname === target.slice(1);
  return pathname === target || pathname.startsWith(`${target}/`);
}

export function isNavigationItemActive(pathname: string, item: { to: string; matches?: string[] }) {
  return (item.matches ?? [item.to]).some((target) => matchesPath(pathname, target));
}

export function getActivePrimaryNavigation(pathname: string) {
  return PRIMARY_NAVIGATION.find((item) => isNavigationItemActive(pathname, item));
}

export function getSecondaryNavigation(pathname: string) {
  if (pathname.startsWith("/models")) return MODELS_NAVIGATION;
  const primary = getActivePrimaryNavigation(pathname);
  if (!primary?.secondary || DETAIL_ROUTES.some((matches) => matches(pathname))) return undefined;
  return primary;
}
