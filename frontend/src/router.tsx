import { Suspense, lazy, type ComponentType } from "react";
import { Navigate, createBrowserRouter } from "react-router";
import { Layout } from "@/components/layout/Layout";

const Today = lazy(() => import("@/pages/Today").then((m) => ({ default: m.Today })));
const ValueStrategy = lazy(() => import("@/pages/ValueStrategy").then((m) => ({ default: m.ValueStrategy })));
const ValueOverview = lazy(() => import("@/pages/ValueResearchWorkspace").then((m) => ({ default: m.ValueOverview })));
const ValueProfiles = lazy(() => import("@/pages/ValueResearchWorkspace").then((m) => ({ default: m.ValueProfiles })));
const ValueResearchQueue = lazy(() => import("@/pages/ValueResearchWorkspace").then((m) => ({ default: m.ValueResearchQueue })));
const ValueValuationCenter = lazy(() => import("@/pages/ValueResearchWorkspace").then((m) => ({ default: m.ValueValuationCenter })));
const ValueMonitorCenter = lazy(() => import("@/pages/ValueResearchWorkspace").then((m) => ({ default: m.ValueMonitorCenter })));
const ValuePlans = lazy(() => import("@/pages/ValueResearchWorkspace").then((m) => ({ default: m.ValuePlans })));
const EmotionStrategy = lazy(() => import("@/pages/EmotionStrategy").then((m) => ({ default: m.EmotionStrategy })));
const SimulationHub = lazy(() => import("@/pages/SimulationHub").then((m) => ({ default: m.SimulationHub })));
const ModelsHub = lazy(() => import("@/pages/ModelsHub").then((m) => ({ default: m.ModelsHub })));
const DataCenter = lazy(() => import("@/pages/DataCenter").then((m) => ({ default: m.DataCenter })));
const CompanyResearch = lazy(() => import("@/pages/CompanyResearch").then((m) => ({ default: m.CompanyResearch })));
const Committee = lazy(() => import("@/pages/Committee").then((m) => ({ default: m.Committee })));
const ResearchReports = lazy(() => import("@/pages/ResearchReports").then((m) => ({ default: m.ResearchReports })));
const ReportDetail = lazy(() => import("@/pages/ReportDetail").then((m) => ({ default: m.ReportDetail })));
const Agent = lazy(() => import("@/pages/Agent").then((m) => ({ default: m.Agent })));
const Scheduled = lazy(() => import("@/pages/Scheduled").then((m) => ({ default: m.Scheduled })));
const Backtests = lazy(() => import("@/pages/Reports").then((m) => ({ default: m.Reports })));
const RunDetail = lazy(() => import("@/pages/RunDetail").then((m) => ({ default: m.RunDetail })));
const Compare = lazy(() => import("@/pages/Compare").then((m) => ({ default: m.Compare })));
const Settings = lazy(() => import("@/pages/Settings").then((m) => ({ default: m.Settings })));
const Correlation = lazy(() => import("@/pages/Correlation").then((m) => ({ default: m.Correlation })));
const AlphaZoo = lazy(() => import("@/pages/AlphaZoo").then((m) => ({ default: m.AlphaZoo })));
const FormulaScanner = lazy(() => import("@/pages/FormulaScanner").then((m) => ({ default: m.FormulaScanner })));
const Funds = lazy(() => import("@/pages/Funds").then((m) => ({ default: m.Funds })));
const SectorDetail = lazy(() => import("@/pages/SectorDetail").then((m) => ({ default: m.SectorDetail })));
const MarketOverview = lazy(() => import("@/pages/MarketOverview").then((m) => ({ default: m.MarketOverview })));
const MarketRanks = lazy(() => import("@/pages/MarketRanks").then((m) => ({ default: m.MarketRanks })));
const SectorRanking = lazy(() => import("@/pages/SectorRanking").then((m) => ({ default: m.SectorRanking })));
const Screener = lazy(() => import("@/pages/Screener").then((m) => ({ default: m.Screener })));

function PageLoader() { return <div className="flex h-[60vh] items-center justify-center text-sm text-muted-foreground">正在加载工作台…</div>; }
function wrap(Component: ComponentType) { return <Suspense fallback={<PageLoader />}><Component /></Suspense>; }
const redirect = (to: string) => <Navigate to={to} replace />;

export const router = createBrowserRouter([{
  element: <Layout />,
  children: [
    { path: "/", element: redirect("/value") },
    { path: "/today", element: wrap(Today) },

    { path: "/value", element: wrap(ValueStrategy), children: [
      { index: true, element: wrap(ValueOverview) },
      { path: "profiles", element: wrap(ValueProfiles) },
      { path: "research", element: wrap(ValueResearchQueue) },
      { path: "valuation", element: wrap(ValueValuationCenter) },
      { path: "monitor", element: wrap(ValueMonitorCenter) },
      { path: "plans", element: wrap(ValuePlans) },
    ] },
    { path: "/value/macro", element: redirect("/value?focus=macro") },
    { path: "/value/sectors", element: redirect("/value?focus=tracks") },
    { path: "/value/leaders", element: redirect("/value?focus=leaders") },
    { path: "/value/company", element: redirect("/value/research") },
    { path: "/value/timing", element: redirect("/value/monitor") },
    { path: "/company", element: redirect("/value/research") },
    { path: "/company/:market/:symbol", element: wrap(CompanyResearch) },

    { path: "/emotion/temperature", element: wrap(EmotionStrategy) },
    { path: "/emotion/cycle", element: wrap(EmotionStrategy) },
    { path: "/emotion/sectors", element: wrap(EmotionStrategy) },
    { path: "/emotion/short", element: wrap(EmotionStrategy) },
    { path: "/emotion/swing", element: wrap(EmotionStrategy) },
    { path: "/emotion/plans", element: wrap(EmotionStrategy) },

    { path: "/simulation/accounts", element: wrap(SimulationHub) },
    { path: "/simulation/signals", element: wrap(SimulationHub) },
    { path: "/simulation/backtests", element: wrap(Backtests) },
    { path: "/simulation/runs/:runId", element: wrap(RunDetail) },
    { path: "/simulation/compare", element: wrap(Compare) },
    { path: "/simulation/decay", element: wrap(SimulationHub) },

    { path: "/ai/agent", element: wrap(Agent) },
    { path: "/ai/value-committee", element: wrap(Committee) },
    { path: "/ai/emotion-committee", element: wrap(Committee) },
    { path: "/ai/committees/:committeeId", element: wrap(Committee) },
    { path: "/ai/reports", element: wrap(ResearchReports) },
    { path: "/ai/reports/:reportId", element: wrap(ReportDetail) },
    { path: "/reports/:reportId", element: wrap(ReportDetail) },
    { path: "/ai/scheduled", element: wrap(Scheduled) },

    { path: "/models/data", element: wrap(DataCenter) },
    { path: "/models/factors", element: wrap(AlphaZoo) },
    { path: "/models/factors/bench", element: wrap(AlphaZoo) },
    { path: "/models/factors/compare", element: wrap(AlphaZoo) },
    { path: "/models/factors/:alphaId", element: wrap(AlphaZoo) },
    { path: "/models/formulas", element: wrap(FormulaScanner) },
    { path: "/models/funds", element: wrap(Funds) },
    { path: "/models/correlation", element: wrap(Correlation) },
    { path: "/models/strategies", element: wrap(ModelsHub) },
    { path: "/models/evidence", element: wrap(ModelsHub) },
    { path: "/settings", element: wrap(Settings) },

    // Preserved legacy pages and deep links.
    { path: "/market/overview", element: wrap(MarketOverview) },
    { path: "/market/ranks", element: wrap(MarketRanks) },
    { path: "/market/sectors/:code", element: wrap(SectorDetail) },
    { path: "/market/macro", element: redirect("/value") },
    { path: "/market/sectors", element: wrap(SectorRanking) },
    { path: "/screener", element: wrap(Screener) },
    { path: "/funds", element: redirect("/models/funds") },
    { path: "/formula", element: redirect("/models/formulas") },
    { path: "/committee", element: redirect("/ai/value-committee") },
    { path: "/committee/:committeeId", element: wrap(Committee) },
    { path: "/signals", element: redirect("/simulation/signals") },
    { path: "/portfolio", element: redirect("/simulation/accounts") },
    { path: "/backtests", element: redirect("/simulation/backtests") },
    { path: "/reports", element: redirect("/ai/reports") },
    { path: "/reports/:reportId", element: wrap(ReportDetail) },
    { path: "/runs/:runId", element: wrap(RunDetail) },
    { path: "/compare", element: redirect("/simulation/compare") },
    { path: "/lab/agent", element: redirect("/ai/agent") },
    { path: "/lab/scheduled", element: redirect("/ai/scheduled") },
    { path: "/lab/alpha", element: redirect("/models/factors") },
    { path: "/lab/alpha/bench", element: redirect("/models/factors/bench") },
    { path: "/lab/alpha/compare", element: redirect("/models/factors/compare") },
    { path: "/lab/alpha/:alphaId", element: wrap(AlphaZoo) },
    { path: "/lab/correlation", element: redirect("/models/correlation") },
    { path: "/data", element: redirect("/models/data") },
    { path: "/runtime", element: redirect("/models/data") },
    { path: "/agent", element: redirect("/ai/agent") },
    { path: "/scheduled", element: redirect("/ai/scheduled") },
    { path: "/alpha-zoo", element: redirect("/models/factors") },
    { path: "/alpha-zoo/bench", element: redirect("/models/factors/bench") },
    { path: "/alpha-zoo/compare", element: redirect("/models/factors/compare") },
    { path: "/alpha-zoo/:alphaId", element: wrap(AlphaZoo) },
    { path: "/correlation", element: redirect("/models/correlation") },
    { path: "*", element: redirect("/value") },
  ],
}]);
