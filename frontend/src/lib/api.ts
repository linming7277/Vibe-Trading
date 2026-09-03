import i18n from "@/i18n";
import { authHeaders, withAuthTicket } from "@/lib/apiAuth";

const BASE = "";

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

const AUTH_REQUIRED_MESSAGE_KEY = "agent.authRequired";

function getAuthRequiredMessage(): string {
  return i18n.t(AUTH_REQUIRED_MESSAGE_KEY as never);
}

// Keep the existing string export compatible with consumers while updating its
// live ES-module binding whenever the active locale changes.
export let AUTH_REQUIRED_MESSAGE = getAuthRequiredMessage();
i18n.on("languageChanged", () => {
  AUTH_REQUIRED_MESSAGE = getAuthRequiredMessage();
});

export function isAuthRequiredError(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 401 || error.status === 403);
}

export interface CorrelationResponse {
  labels: string[];
  matrix: number[][];
}

export interface FinancialAgentProgress {
  stage: string;
  message: string;
  details?: Record<string, unknown>;
}

export interface FinancialAgentReply {
  answer: string;
  scope: "workspace" | "company" | "capability" | "data_boundary" | "general_method" | "company_not_loaded" | "context_required";
  provider?: string;
  model?: string;
  stock_code?: string;
  stock_name?: string;
  data_context?: { company_count: number; industry_count: number; data_dates: string[] };
}

export type ResearchAgentRole =
  | "research_lead" | "macro_policy" | "industry" | "company" | "valuation" | "risk"
  | "financial_analyst";

export interface ResearchAgentConfig {
  role: ResearchAgentRole;
  provider: string;
  model: string;
  enabled: boolean;
  updated_at: string;
}

export interface ResearchAgentProvider {
  provider: string;
  label: string;
  models: string[];
  default_model: string;
  configured: boolean;
}

export interface ResearchAgentModelOption {
  id: string;
  name: string;
  ready: boolean;
}

export interface ResearchAgentModelSetting {
  role: ResearchAgentRole;
  model_id: string;
  model_name: string;
  enabled: boolean;
  ready: boolean;
  updated_at: string;
  models: ResearchAgentModelOption[];
}

export interface ResearchAgentConnectionSetting {
  role: ResearchAgentRole;
  base_url: string;
  model: string;
  api_key_configured: boolean;
  enabled: boolean;
  ready: boolean;
  updated_at: string;
}

export interface ValueIndustry {
  industry_code: string;
  industry_name: string;
  industry_level: string;
  raw_industry_level: string;
  level: 2 | 3;
  is_terminal: boolean;
  tdx_class_code: string;
  level1_code: string;
  level1_name: string;
  level2_code: string;
  level2_name: string;
  level3_code: string;
  level3_name: string;
  terminal_level: 2 | 3;
  member_count: number;
  as_of: string | null;
  source: Record<string, unknown> | string;
}

export interface Level3Leader {
  level1_code: string;
  level1_name: string;
  level2_code: string;
  level2_name: string;
  level3_code: string;
  level3_name: string;
  stock_code: string;
  stock_name: string;
  leader_rank: number;
  leader_score: number;
  leader_formula_version: string;
  component_scores: Record<string, number | null>;
  coverage: number;
  eligibility_status: "eligible" | "ineligible";
  eligibility_reasons: string[];
  metric_applicability_notes: string[];
  as_of: string;
  raw_features?: Record<string, number | null>;
  normalized_features?: Record<string, number | null>;
  raw_metric_coverage?: number;
  raw_metric_available?: number;
  raw_metric_total?: number;
  confidence?: "LOW" | "MEDIUM" | "HIGH";
  components?: LeaderComponentExplanation[];
  explanation?: LeaderExplanation;
}

export interface LeaderMetricExplanation {
  key: string; label: string; description: string; unit: string;
  higher_is_better: boolean; weight: number; raw_value: number | null;
  percentile: number | null; status: "available" | "missing";
}

export interface LeaderComponentExplanation {
  key: string; label: string; weight: number; score: number | null;
  coverage: number; status: string; contribution: number | null;
  reweighted: boolean; metrics: LeaderMetricExplanation[];
}

export interface LeaderExplanation {
  summary: string; selected: boolean; comparison_scope: string;
  member_count: number; eligible_count: number; excluded_count: number;
  rank: number; top_percent: number | null;
  strongest: Array<{ key: string; label: string; score: number }>;
  weakest: Array<{ key: string; label: string; score: number }>;
  sample_warning?: string | null; overall_reweighted?: boolean;
  missing_dimensions?: Array<{ key: string; label: string }>;
  score_interpretation: string;
}

export interface LeaderFormulaContract {
  version: string; comparison_scope: string; leader_limit: number;
  minimum_overall_coverage: number; minimum_dimension_coverage: number;
  normalization: string; product_label: string; disclaimer: string;
  eligibility_rules: Array<{ key: string; label: string }>;
  dimensions: Array<{
    key: string; label: string; weight: number;
    metrics: Array<{ key: string; label: string; weight: number; unit: string; higher_is_better: boolean; description: string }>;
  }>;
}

export interface LeaderIndustrySummary {
  member_count: number; eligible_count: number; excluded_count: number;
  selected_count: number; sample_warning?: string | null;
}

export interface ExcludedLevel3Leader extends Level3Leader {
  eligibility_reason_labels?: string[];
}

export type LeaderLifecycleStatus = "NEW" | "ACTIVE" | "OUT_OF_TOP2" | "REENTERED";

export interface ValueLeaderPoolMember extends Level3Leader {
  pool_id: string;
  lifecycle_status: LeaderLifecycleStatus;
  first_entered_at: string;
  last_seen_at: string;
  exited_at?: string | null;
  previous_pool_id?: string | null;
}

export interface ValueCompanyResearchState {
  id: string;
  pool_id: string;
  stock_code: string;
  stock_name: string;
  lifecycle_status: "ACTIVE" | "OUT_OF_TOP2";
  research_status: "PENDING" | "READY" | "PARTIAL" | "INACTIVE" | string;
  is_priority: boolean | number;
  last_financial_snapshot_id?: string | null;
  last_researched_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface ValueLeaderPoolSummary {
  id: string;
  source_leader_run_id: string;
  as_of: string;
  status: "COMPLETED" | string;
  formula_version: string;
  catalog_as_of: string;
  terminal_industry_count: number;
  current_membership_count: number;
  company_count: number;
  new_count: number;
  active_count: number;
  out_count: number;
  reentered_count: number;
  diff: { previous_pool_id?: string | null; entered: number; stayed: number; left: number; reentered: number };
  created_at: string;
  completed_at?: string | null;
}

export interface ValueLeaderPool extends ValueLeaderPoolSummary {
  members: ValueLeaderPoolMember[];
  research_states: ValueCompanyResearchState[];
  formula?: LeaderFormulaContract;
  industry_summaries?: Record<string, LeaderIndustrySummary>;
  valuation_snapshot?: {
    status: "READY" | "MISSING" | string;
    total: number;
    expected: number;
    data_as_of: string | null;
    is_complete?: boolean;
    items: Record<string, {
      presentation_status: "DEEPLY_UNDERVALUED" | "UNDERVALUED" | "FAIR" | "OVERVALUED" | "DEEPLY_OVERVALUED" | "INSUFFICIENT_DATA";
      historical_valuation_status?: string;
      coverage_status?: string;
      data_as_of?: string | null;
    }>;
  };
}

export interface LowValueLeaderPoolItem {
  id: string;
  market: string;
  stock_code: string;
  company_name: string;
  industry_code: string;
  industry_name: string;
  leader_rank: number;
  leader_score: number;
  current_price: number | null;
  fair_value_low: number | null;
  fair_value_mid: number | null;
  fair_value_high: number | null;
  valuation_status: "UNDERVALUED" | "DEEPLY_UNDERVALUED" | string;
  historical_valuation_status: string | null;
  support_status: string | null;
  support_zone_low: number | null;
  support_zone_high: number | null;
  entry_level: string | null;
  pool_status: "ACTIVE" | "REMOVED";
  source_pool_id: string;
  source_as_of: string;
  entered_at: string;
  removed_at: string | null;
  updated_at: string;
  enter_reason: string;
  remove_reason: string | null;
  metadata: Record<string, unknown>;
  risk_overall: "LOW" | "MEDIUM" | "HIGH" | "UNKNOWN";
  value_trap_risk: "LOW_TRAP_RISK" | "MEDIUM_TRAP_RISK" | "HIGH_TRAP_RISK" | "UNKNOWN" | "NOT_APPLICABLE";
  material_risk_count: number;
  top_risk_types: string[];
  risk_summary: string;
  risk_as_of: string | null;
  risk_snapshot_status: "READY" | "UNKNOWN";
}

export interface LowValueLeaderPoolResponse {
  items: LowValueLeaderPoolItem[];
  total: number;
  data_as_of: string | null;
  last_evaluated_at: string | null;
}

export interface LowValueLeaderEvent {
  id: string;
  market: string;
  stock_code: string;
  company_name: string;
  industry_code: string | null;
  industry_name: string | null;
  event_type: "ENTER_LOW_VALUE" | "EXIT_LOW_VALUE";
  before_status: string | null;
  after_status: string;
  current_price: number | null;
  fair_value_mid: number | null;
  valuation_status: string | null;
  event_date: string;
  source_as_of: string;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface LowValueLeaderEventsResponse {
  event_date: string | null;
  entered: number;
  exited: number;
  items: LowValueLeaderEvent[];
  total: number;
}

export interface FocusSelectionItem {
  tier: "A" | "B" | "C";
  stock_code: string;
  company_name: string;
  industry_code: string;
  industry_name: string;
  leader_rank: number;
  leader_score: number | null;
  valuation_status: string;
  current_price: number | null;
  fair_value_mid: number | null;
  discount_to_mid: number | null;
  historical_valuation_status: string | null;
  support_status: string | null;
  entry_level: string | null;
  risk_status: "LOW" | "MEDIUM" | "HIGH" | "UNKNOWN" | string;
  value_trap_risk: string;
  thesis_status: string;
  thesis_authority: string | null;
  financial_status: string;
  business_profile_status: string;
  peer_count: number | null;
  focus_reasons: string[];
  focus_cautions: string[];
  primary_demotion_reason: string | null;
  source_dates: Record<string, string | null>;
}

export interface FocusSelectionResponse {
  research_as_of: string;
  total_low_value: number;
  hard_c_count: number;
  soft_demote_count: number;
  A_count: number;
  B_count: number;
  C_count: number;
  A: FocusSelectionItem[];
  B: FocusSelectionItem[];
  C: FocusSelectionItem[];
  selection_boundary: string;
  read_only: boolean;
}

/** Read-only persisted daily brief. Loading this endpoint never rebuilds research. */
export interface InvestmentResearchDailyBrief {
  research_as_of: string;
  status?: string;
  low_value_active_count: number;
  enter_count: number;
  exit_count: number;
  priority_companies: Array<{ stock_code?: string; company_name?: string; industry_name?: string; why_today?: string; [key: string]: unknown }>;
  risk_summary?: { summary?: string; important_companies?: Array<{ company_name?: string; risk_summary?: string; [key: string]: unknown }>; [key: string]: unknown };
  data_gaps?: string[];
  brief_payload?: { executive?: string; executive_situations?: Array<{ company_name?: string; basis?: string; impact?: string; [key: string]: unknown }>; executive_watchlist?: Array<{ company_name?: string; stock_code?: string; research_priority_reason?: string; [key: string]: unknown }>; [key: string]: unknown };
  formula_version?: string;
}

export interface FinancialPoint {
  report_date: string;
  announcement_date: string;
  period_type: string;
  value: number | null;
}

export interface FinancialForecastScenario {
  scenario: "BEAR" | "BASE" | "BULL";
  label: string;
  revenue_growth_assumptions: number[];
  margin_assumptions: Array<number | null>;
  forecast: Array<{ year: string; revenue: number | null; net_profit: number | null }>;
  assumption_notes: string[];
}

export interface FinancialClaimCitation {
  source_key: string;
  status: "RESOLVED" | "UNRESOLVED";
  source_type?: "FINANCIAL_HISTORY" | "FINANCIAL_FEATURE" | "DETERMINISTIC_FORECAST" | "UNKNOWN";
  source?: string;
  metric?: string | null;
  period?: string | null;
  value?: number | string | null;
  unit?: string | null;
  data_as_of?: string | null;
  source_snapshot_id?: string | null;
  source_hash?: string | null;
  scenario?: string | null;
  forecast_year?: string | null;
  forecast_version?: string | null;
}

export interface FinancialClaimCitationStats {
  claims_total: number;
  claims_with_citations: number;
  resolved_source_keys: number;
  unresolved_source_keys: number;
}

export interface FinancialClaim {
  type: "FACT" | "INFERENCE" | "FORECAST" | "UNKNOWN";
  statement: string;
  evidence_keys: string[];
  source_keys?: string[];
  citations?: FinancialClaimCitation[];
}

export interface FinancialAnalysisSnapshot {
  id: string;
  stock_code: string;
  stock_name: string;
  as_of: string;
  historical_cutoff: string;
  financial_feature_version: string;
  forecast_version: string;
  feature_status: "READY" | "PARTIAL" | "INSUFFICIENT_DATA";
  forecast_status: "READY" | "PARTIAL" | "LIMITED" | "INSUFFICIENT_DATA";
  analysis_status: "NOT_RUN" | "CONFIGURATION_REQUIRED" | "COMPLETED" | "FAILED";
  agent_provider: string | null;
  agent_model: string | null;
  agent_error: string;
  identity: {
    stock_code: string; stock_name: string;
    level1_name: string | null; level2_name: string | null; level3_name: string | null;
    leader_rank: number | null; leader_score: number | null; leader_formula_version: string | null;
    metric_applicability_notes: string[];
    market_valuation?: {
      as_of?: string | null; pe?: number | null; pb?: number | null;
      dividend_yield?: number | null; market_cap?: number | null;
      source?: string; limitations?: string[];
    };
    market_quote?: {
      as_of?: string | null; price?: number | null; previous_close?: number | null; source?: string;
    };
    data_dates?: {
      analysis_as_of?: string | null; quote_as_of?: string | null; valuation_as_of?: string | null;
      financial_report_date?: string | null; financial_announcement_date?: string | null;
      fundamental_report_date?: string | null; leader_as_of?: string | null;
    };
  };
  history: Array<Record<string, number | string | null>>;
  feature: {
    status: string;
    growth: Record<string, FinancialPoint[] | { value: number | null; years: number; status: string }>;
    profitability: { roe: FinancialPoint[]; gross_margin: { status: string; items: FinancialPoint[] }; net_margin: FinancialPoint[] };
    cash_flow: { operating_cash_flow: FinancialPoint[]; cash_conversion: FinancialPoint[]; ocf_to_revenue: FinancialPoint[] };
    balance_sheet: { total_assets: FinancialPoint[]; equity: FinancialPoint[]; debt_ratio: FinancialPoint[] };
    capital_expenditure: { capex: FinancialPoint[]; capex_to_revenue: FinancialPoint[] };
    trends: Record<string, string>;
    latest_changes: Array<Record<string, number | string | boolean | null>>;
    data_quality: { coverage: number; missing_fields: string[]; annual_period_count: number; cautions: string[] };
  };
  forecast: {
    status: string;
    disclaimer: string;
    scenarios: Partial<Record<"BEAR" | "BASE" | "BULL", FinancialForecastScenario>>;
    assumption_notes: string[];
  };
  analysis: null | {
    stock_code: string; stock_name: string; executive_summary: string;
    historical_performance: Record<string, string>;
    latest_changes: string[]; financial_strengths: string[]; financial_risks: string[];
    forecast_analysis: { bear: string; base: string; bull: string; key_assumptions: string[] };
    key_metrics_to_monitor: string[]; confidence: string; data_gaps: string[];
    claims: FinancialClaim[];
    analysis_metadata?: {
      traceability_status?: "COMPLETE" | "PARTIAL" | "UNRESOLVED" | "NOT_APPLICABLE";
      citation_stats?: FinancialClaimCitationStats;
    };
  };
  data_gaps: string[];
  source_hash: string;
  traceability_status?: "COMPLETE" | "PARTIAL" | "UNRESOLVED" | "NOT_APPLICABLE";
  citation_stats?: FinancialClaimCitationStats;
  idempotent_reuse?: boolean;
}

export interface FinancialChatArchiveEntry {
  id: string;
  stock_code: string;
  stock_name: string;
  role: "user" | "assistant";
  content: string;
  source_snapshot_id: string | null;
  source_hash: string | null;
  created_at: string;
}

export interface FinancialDossier {
  snapshot: FinancialAnalysisSnapshot;
  chat_entries: FinancialChatArchiveEntry[];
  archive_summary: { chat_entry_count: number; latest_chat_at: string | null; analysis_status: FinancialAnalysisSnapshot["analysis_status"]; source_hash: string };
}

export interface BusinessClaimCitation {
  source_key: string;
  status: "RESOLVED" | "UNRESOLVED";
  source_type?: string;
  source_id?: string;
  data_as_of?: string;
  field?: string;
  value?: string;
  source_hash?: string;
  profile_role?: "CURRENT" | "PREVIOUS" | string;
}

export interface BusinessClaim {
  type: "FACT" | "INFERENCE" | "UNKNOWN";
  topic: "MAIN_BUSINESS" | "PRODUCT" | "BUSINESS_MODEL" | "BUSINESS_CHANGE";
  text: string;
  source_keys: string[];
  confidence: "LOW" | "MEDIUM" | "HIGH";
  citations?: BusinessClaimCitation[];
}

export interface BusinessResearchSnapshot {
  id: string;
  stock_code: string;
  company_name: string;
  data_as_of: string;
  main_business: string;
  products: string[];
  product_note: string;
  business_model: string;
  business_changes: string[];
  source_hash: string;
  data_quality: {
    status: "READY" | "PARTIAL" | "MISSING" | string;
    field_statuses: Record<string, "READY" | "PARTIAL" | "MISSING" | string>;
    missing_fields: string[];
    limitations: string[];
  };
  module_version: string;
  analysis_status: "NOT_RUN" | "CONFIGURATION_REQUIRED" | "COMPLETED" | "FAILED" | string;
  analysis: null | { summary: string; claims: BusinessClaim[]; analysis_metadata?: Record<string, unknown> };
  traceability_status: "COMPLETE" | "PARTIAL" | "UNRESOLVED" | "NOT_APPLICABLE";
  citation_stats: { claims_total: number; required_claims: number; resolved_required_claims: number; unresolved: number };
  agent_error?: string;
}

export interface CompanyThesis {
  thesis_id: string;
  market: string;
  stock_code: string;
  title: string;
  core_thesis: string;
  status: string;
  confidence: string;
  version: number;
  authority_status?: "AI_PROVISIONAL" | "HUMAN_CONFIRMED" | "LEGACY_UNVERIFIED" | string;
  source_draft_id?: string | null;
  created_by?: string;
  invalid_conditions?: Array<{ condition?: string; text?: string; status?: string }>;
  supporting_conditions?: Array<{ condition?: string; text?: string; status?: string; role?: string }>;
  key_metrics_to_monitor?: Array<{ condition?: string; text?: string; metric?: string; type?: string } | string>;
  created_at?: string;
  updated_at?: string;
}

export interface CompanyThesisDraft {
  draft_id: string;
  market: string;
  stock_code: string;
  company_name: string;
  title: string;
  core_thesis: string;
  status: "FORMING" | "STRENGTHENING" | "UNCHANGED" | "WEAKENING" | "FALSIFIED";
  confidence: "LOW" | "MEDIUM" | "HIGH";
  invalid_conditions: Array<{ condition: string; status?: string }>;
  key_assumptions?: Array<{ condition?: string; text?: string; type?: string }>;
  key_metrics_to_monitor?: Array<{ condition?: string; text?: string; metric?: string; type?: string } | string>;
  source_data_as_of?: string | null;
  source_snapshots: Array<{ domain: string; snapshot_id?: string; data_as_of?: string }>;
  source_refs: Array<{ domain: string; type: string; text: string; source_keys: string[]; confidence: string }>;
  competitive_advantages?: Array<{ moat_dimension?: string; dimension?: string; assessment?: "SUPPORTED" | "PARTIAL" | "UNKNOWN"; type: string; text: string; data_gaps?: string[]; counter_evidence_ids?: string[] }>;
  metadata?: { moat_research?: { research_as_of?: string | null; status?: string; formula_version?: string }; [key: string]: unknown };
  draft_status: "DRAFT" | "CONFIRMED" | "REJECTED" | "SUPERSEDED";
  created_at: string;
  updated_at: string;
}

export interface CompanyThesisReview {
  review_id: string;
  thesis_id: string;
  thesis_version: number;
  review_status: "PENDING" | "REVIEWED" | "APPLIED" | "DISMISSED" | string;
  is_stale: boolean;
  recommended_status: string;
  recommended_confidence: string;
  support_count: number;
  challenge_count: number;
  neutral_count: number;
  review_reason: string;
  review_summary: string;
  evidence_set_hash: string;
  metadata: { evidence_source_summary?: Record<string, number>; [key: string]: unknown };
  created_at: string;
}

export interface CompanyThesisReviewResponse {
  status: "CREATED" | "EXISTING" | "THESIS_NOT_CREATED" | "NO_ACTIVE_EVIDENCE" | "NOT_REVIEWED" | string;
  review: CompanyThesisReview | null;
  created?: boolean;
}

export interface CompanyThesisEvidence {
  evidence_id: string; thesis_id: string; market: string; stock_code: string; evidence_type: string;
  effect: "SUPPORT" | "CHALLENGE" | "NEUTRAL" | string; claim: string; summary: string;
  source_type: string; source_title?: string | null; source_date?: string | null; data_as_of?: string | null;
  confidence: string; is_active: boolean; created_at: string; created_by: string;
  metadata?: { research_domain?: string; resolved_citations?: CompanyResearchOverviewCitation[]; [key: string]: unknown };
}

export interface CompanyThesisEvidenceResponse {
  status: "OK" | "THESIS_NOT_CREATED" | string;
  current_thesis: CompanyThesis | null;
  evidence: CompanyThesisEvidence[];
  summary: { total: number; active: number; support: number; challenge: number; neutral: number; by_type?: Record<string, number> } | null;
}

export interface CompanyThesisHistoryItem {
  history_id: string; market: string; stock_code: string; from_thesis_id: string; to_thesis_id: string;
  from_version: number; to_version: number; old_status: string; new_status: string;
  old_confidence: string; new_confidence: string; change_type: string; change_reason: string;
  trigger_type: string; trigger_ref?: string | null; evidence_ids_json?: string[]; created_by: string; created_at: string;
  metadata_json?: Record<string, unknown>;
}

export interface CompanyResearchOverviewCitation {
  source_key?: string;
  status: "RESOLVED" | "UNRESOLVED" | string;
  source_type?: string;
  source_id?: string;
  source?: string;
  data_as_of?: string;
  field?: string;
  value?: string | number | null;
  metric?: string | null;
  period?: string | null;
}

export interface ValueWatchpoint {
  category: string;
  title: string;
  current_state: string;
  positive_condition: string;
  negative_condition: string;
  next_review_anchor?: string | null;
  next_review_label?: string;
  source_module: string;
  source_module_label?: string;
  source_refs: Array<Record<string, unknown>>;
  research_as_of?: string | null;
  formula_version: string;
  importance_tier: "CRITICAL" | "HIGH" | "NORMAL" | "LOW" | string;
  cautions: string[];
  data_status?: string;
}

export interface ValueWatchpointProjection {
  stock_code: string;
  stock_name: string;
  research_as_of?: string | null;
  primary_action?: string | null;
  focus_tier?: string | null;
  watchpoints: ValueWatchpoint[];
  top_watchpoints: ValueWatchpoint[];
  data_gaps: Array<{ category: string; description: string; source_module: string; research_as_of?: string | null }>;
  suggested_research_need?: string | null;
  source_freshness?: Record<string, string | null | undefined>;
  formula_version: string;
  read_only?: boolean;
}

export interface CompanyResearchOverview {
  company: { market: string; stock_code: string; stock_name: string };
  business_summary: { status: string; snapshot_id: string | null; data_as_of?: string | null; main_business?: string; description?: string; products?: string[]; product_note?: string; business_model?: string; changes: string[]; claims: BusinessClaim[]; traceability_status?: string };
  financial_summary: { status: string; snapshot_id: string | null; as_of?: string | null; latest_announcement_date?: string | null; latest_report_date?: string | null; analysis_status: string; message?: string; items: Array<{ category: string; text: string; source_keys: string[]; citations: CompanyResearchOverviewCitation[] }>; claims: FinancialClaim[]; key_metrics_to_monitor?: string[] };
  supporting_evidence: Array<{ evidence_id: string; evidence_type: string; effect: string; claim: string; summary: string; confidence: string; created_by: string; created_at: string; source_type: string; citations: CompanyResearchOverviewCitation[]; research_domain: string }>;
  challenging_evidence: Array<{ evidence_id: string; evidence_type: string; effect: string; claim: string; summary: string; confidence: string; created_by: string; created_at: string; source_type: string; citations: CompanyResearchOverviewCitation[]; research_domain: string }>;
  neutral_evidence_count: number;
  thesis: null | { thesis_id: string; title: string; core_thesis: string; status: string; status_label: string; confidence: string; version: number; updated_at?: string; invalid_conditions: Array<{ condition: string; status: string }>; history_count: number };
  review: null | { review_id: string; review_status: string; is_stale: boolean; support_count: number; challenge_count: number; neutral_count: number; recommended_status: string; recommended_confidence: string; review_reason: string; created_at: string };
  watch_items: Array<{ source: string; text: string; title?: string; current_state?: string }>;
  top_watchpoints?: ValueWatchpoint[];
  data_gaps?: Array<{ category: string; description: string; source_module: string; research_as_of?: string | null }>;
  data_status: { financial: string; business: string; thesis: "CREATED" | "NOT_CREATED"; review: "CURRENT" | "STALE" | "NOT_CREATED" };
}

export interface CapitalAllocationSourceRef {
  source_type: string;
  source_record_id: string;
  source?: string | null;
  report_date?: string | null;
  announcement_date?: string | null;
  event_date?: string | null;
  data_as_of?: string | null;
  source_hash?: string | null;
  raw_version?: string | null;
  pit_status: "STRICT" | "PIT_LIMITED" | "UNKNOWN" | string;
}

export interface CapitalAllocationRatio { value: number | null; status: "READY" | "PARTIAL" | "UNKNOWN" | string; }
export interface CapitalAllocationGap { item: string; status: "MISSING" | "RAW_NOT_STRUCTURED" | "NOT_COLLECTED" | string; reason: string; }
export interface CapitalAllocationTimelineItem {
  year: string;
  report_date: string;
  announcement_date: string;
  data_as_of: string;
  flow_basis: string;
  operating_cash_flow: number | null;
  capex: number | null;
  cash_and_equivalents: number | null;
  revenue: number | null;
  net_profit: number | null;
  liabilities: number | null;
  debt_ratio: number | null;
  interest_bearing_debt_ratio: number | null;
  total_shares: number | null;
  roe: number | null;
  capex_to_ocf: CapitalAllocationRatio;
  capex_to_revenue: CapitalAllocationRatio;
  cash_change: CapitalAllocationRatio;
  debt_ratio_change: CapitalAllocationRatio;
  share_count_change: CapitalAllocationRatio;
  debt_context: {
    liabilities: number | null;
    current_liabilities: number | null;
    non_current_liabilities: number | null;
    debt_ratio: number | null;
    interest_bearing_debt_ratio: number | null;
    debt_ratio_change: CapitalAllocationRatio;
    status: string;
    source_refs: CapitalAllocationSourceRef[];
  };
  source_refs: CapitalAllocationSourceRef[];
}
export interface CapitalAllocationDividendEvent {
  event_date: string;
  cash_dividend_per_ten_shares: number;
  cash_dividend_per_share: number;
  cash_dividend_total: number | null;
  bonus_share: number | null;
  rights_issue_ratio: number | null;
  rights_issue_price: number | null;
  normalization_status: string;
  pit_status: string;
  linked_annual_report_date: string | null;
  linked_annual_announcement_date: string | null;
  dividend_to_net_profit: CapitalAllocationRatio;
  dividend_to_ocf: CapitalAllocationRatio;
  source_refs: CapitalAllocationSourceRef[];
}
export interface CapitalAllocationFacts {
  company: { market: string; stock_code: string };
  as_of: string | null;
  formula_version: string;
  read_only: boolean;
  financial_timeline: { status: string; pit_status: string; items: CapitalAllocationTimelineItem[] };
  dividend_history: { status: string; pit_status: string; events: CapitalAllocationDividendEvent[]; raw_unknown_fields: Array<Record<string, unknown>>; source?: string | null; detail_updated_at?: string | null };
  share_capital_history: { status: string; pit_status: string; events: Array<{ event_date: string; total_shares_before: number; total_shares_after: number; change_pct: CapitalAllocationRatio; change_reason: string; status: string; pit_status: string; source_refs: CapitalAllocationSourceRef[] }>; raw_unknown_fields: Array<Record<string, unknown>>; source?: string | null; detail_updated_at?: string | null };
  allocation_completeness: "READY" | "PARTIAL" | "UNKNOWN" | string;
  pit_status: string;
  data_gaps: CapitalAllocationGap[];
  source_traceability: Record<string, { source_type: string; pit_status: string }>;
}

export interface CapitalAllocationResearchDimension {
  status: "SUPPORTED" | "PARTIAL" | "UNKNOWN" | string;
  direction: "POSITIVE" | "NEUTRAL" | "CAUTION" | "UNKNOWN" | string;
  observation: string;
  signal?: string;
  fact_refs: CapitalAllocationSourceRef[];
  comparison_window: Record<string, unknown>;
  data_gaps: string[];
  pit_status: string;
  observations?: Array<Record<string, unknown>>;
  continuity?: { calendar_years: string[]; longest_consecutive_years: number; event_count: number };
  matching?: { matched_event_count: number; above_profit_or_ocf_count: number };
  trend?: Record<string, unknown>;
  context?: Record<string, unknown>;
}
export interface CapitalAllocationResearch {
  company: { market: string; stock_code: string };
  research_as_of: string | null;
  fact_layer_as_of: string | null;
  status: string;
  dimensions: Record<"reinvestment" | "dividend" | "debt_management" | "equity_dilution" | "cash_management" | "buyback" | "m_and_a", CapitalAllocationResearchDimension>;
  strengths: Array<{ dimension: string; observation: string; direction: string }>;
  cautions: Array<{ dimension: string; observation: string; direction: string }>;
  data_gaps: CapitalAllocationGap[];
  capital_allocation_summary: string;
  formula_version: string;
  fact_layer_formula_version: string;
  read_only: boolean;
  pit_status: string;
}

export interface CompanyActionEvent {
  id: string;
  event_type: string;
  event_status: string;
  event_stage: string;
  announcement_date: string | null;
  event_date: string | null;
  effective_date: string | null;
  research_visible_from: string | null;
  title: string;
  summary: string;
  cash_amount: number | null;
  share_count: number | null;
  share_ratio: number | null;
  price: number | null;
  currency: string | null;
  shares_before: number | null;
  shares_after: number | null;
  reason: string | null;
  reason_source_event_id: string | null;
  pit_status: string;
  confidence: string;
  data_quality: string;
  payload: Record<string, unknown>;
  source_refs: Array<{ source_type: string; source_id: string; source_url?: string; announcement_date?: string | null; event_date?: string | null; pit_status: string }>;
}
export interface CompanyActionResponse {
  company: { market: string; stock_code: string };
  as_of: string | null;
  event_type: string | null;
  events: CompanyActionEvent[];
  event_count: number;
  read_only: boolean;
  extractor_version: string;
  capabilities: Record<string, { status: string; source: string }>;
}
export interface CompanyActionPrepareResponse {
  company: { market: string; stock_code: string };
  status: string;
  created: number;
  events: CompanyActionEvent[];
  unknown_raw_fields: Array<Record<string, unknown>>;
  capabilities: Record<string, { status: string; source: string }>;
  message: string;
}

export interface ValuePriceZone {
  low: number | null; high: number | null; strength?: string; score?: number; reasons?: string[]; name?: string; kind?: string;
}
export interface ValuePriceValuationMethod {
  name: string;
  status: string;
  peer_count?: number;
  multiple_low?: number;
  multiple_mid?: number;
  multiple_high?: number;
  forecast_profit?: number;
  fair_values?: number[];
  cheapness_percentile?: number | null;
  metrics?: Record<string, HistoricalValuationMetric>;
  message?: string;
}
export interface ValuePriceZones {
  stock_code: string; as_of: string | null; current_price: number | null; formula_version: string;
  valuation: { status: string; fair_value_low: number | null; fair_value_mid: number | null; fair_value_high: number | null; methods: ValuePriceValuationMethod[]; message: string; limitations: string[] };
  valuation_zones: ValuePriceZone[]; support_zones: ValuePriceZone[]; resistance_zones: ValuePriceZone[];
  confluence_zones: Array<ValuePriceZone & { valuation_status: string; support_strength?: string }>;
  upper_review_zones: Array<ValuePriceZone & { valuation_status: string; support_strength?: string }>;
  thesis_status: string | null; plain_summary: string;
  historical_valuation?: HistoricalValuationHistory;
  data_quality: { daily_history: { status: string; message?: string; bars?: number }; historical_valuation: { status: string; message: string; coverage?: HistoricalValuationCoverage; historical_valuation_status?: string }; current_fundamentals: string; peer_comparables: { status: string; peer_count: number; message?: string }; forecast: string; financial_snapshot: string };
}

export interface HistoricalValuationCoverage { first_date: string | null; last_date: string | null; pe_count: number; pb_count: number; dividend_yield_count: number; coverage_status: string; minimum_reliable_observations: number; }
export interface HistoricalValuationMetric { status: string; count: number; current: number | null; percentile: number | null; cheapness_percentile: number | null; state?: string; direction: string; plain: string; winsorized?: { low_quantile: number; high_quantile: number; low: number; high: number }; }
export interface HistoricalValuationHistory { stock_code: string; as_of: string | null; current: Record<string, unknown> | null; historical_percentiles: { pe_ttm: HistoricalValuationMetric; pb_mrq: HistoricalValuationMetric; dividend_yield: HistoricalValuationMetric }; historical_valuation_status: string; cheapness_percentile: number | null; coverage: HistoricalValuationCoverage; series_summary: { observations: number; source_type: string; price_source_id: string; outlier_rule: string }; }
export interface EntryResearchZone { label: string; low: number | null; high: number | null; kind: string; strength?: string | null; }
export interface EntryResearch { stock_code: string; as_of: string | null; current_price: number | null; entry_score: number; entry_level: "HIGH_ATTENTION" | "ATTENTION" | "WATCH" | "WAIT" | "BLOCKED"; entry_level_label: string; confidence: "HIGH" | "MEDIUM" | "LOW"; valuation_score: number; historical_valuation_score: number; support_score: number; thesis_score: number; thesis_status: string | null; thesis_confidence: string | null; safety_gate: string | null; focus_zones: Record<string, EntryResearchZone | null>; reason_codes: string[]; data_gaps: string[]; plain_explanation: string; formula_version: string; weights: Record<string, number>; }
export interface ExitResearch { stock_code: string; as_of: string | null; current_price: number | null; exit_score: number; exit_level: "CRITICAL_REVIEW" | "REVIEW" | "WATCH" | "NORMAL"; exit_level_label: string; confidence: "HIGH" | "MEDIUM" | "LOW"; valuation_pressure: number; historical_valuation_pressure: number; resistance_pressure: number; thesis_risk: number; thesis_status: string | null; thesis_confidence: string | null; challenge_count: number; challenge_evidence: Array<{ evidence_id: string; confidence: string; text: string; created_at: string; source_title?: string | null; source_date?: string | null }>; latest_review: { review_id: string; review_status: string; recommended_status: string; is_stale: boolean; challenge_count?: number | null } | null; upper_review_zones: ValuePriceZone[]; focus_zones: Record<string, EntryResearchZone | null>; reason_codes: string[]; data_gaps: string[]; safety_gate: string | null; plain_explanation: string; formula_version: string; weights: Record<string, number>; }
export interface ValueStrategyState {
  stock_code: string; stock_name: string; market: "CN"; research_as_of: string | null;
  eligibility: { status: "IN_VALUE_SCOPE" | "OUTSIDE_VALUE_SCOPE"; label: string; reason: string };
  priority: { tier: "A" | "B" | "C" | "NOT_APPLICABLE"; label: string; reasons: string[] };
  price_attention: { primary: boolean; raw_level: string; raw_label: string; effective_status: string; effective_label: string; score: number | null; valuation_reliability: { status: "RELIABLE" | "LIMITED" | "WEAK" | "INSUFFICIENT"; label: string; peer_sample_count: number; peer_sample_counts: number[]; flags: string[]; reasons: string[] }; reasons: string[]; cautions: string[] };
  review_pressure: { primary: boolean; raw_level: string; effective_status: string; effective_label: string; score: number | null; reasons: string[]; cautions: string[] };
  risk: { overall: string; trap: string | null; summary: string | null };
  thesis: { status: string; authority: string; strategy_role: "AUTHORITATIVE" | "EXPLANATORY_ONLY" | "REJECTED"; caution: string | null };
  leader: { rank: number | null; state: string; industry_name: string | null; as_of: string | null };
  freshness: { market_price_as_of: string | null; low_value_as_of: string | null; focus_as_of: string | null; historical_valuation_as_of: string | null; price_structure_as_of: string | null; risk_as_of: string | null; thesis_as_of: string | null; notice: string | null; price_structure: { status: string; label: string; last_bar_date: string | null; current_quote_date: string | null; gap_calendar_days: number | null } };
  summary: string; primary_action: { status: string; label: string }; reasons: string[]; cautions: string[]; formula_version: string; read_only: boolean;
}
export interface ValueStrategyEventBatch { transition_batch_id: string; stock_code: string; stock_name: string; severity: "INFO" | "MEDIUM" | "HIGH" | "CRITICAL"; delivery_mode: "IMMEDIATE" | "DAILY_DIGEST" | "HISTORY_ONLY"; title: string; summary: string; primary_reason: string; simultaneous_changes: string[]; research_as_of: string | null; status: "OPEN" | "ACKNOWLEDGED" | "CLOSED"; event_ids: string[]; events: Array<{ id: string; event_type: string; before_value: string; after_value: string; primary_reason: string }>; delivery?: { delivery_status: string }; }
export interface RiskResearchItem { risk_type: string; severity: "LOW" | "MEDIUM" | "HIGH"; status: "CONFIRMED" | "WATCH" | "UNKNOWN"; text: string; why_it_matters: string; source_keys: string[]; evidence_ids: string[]; watch_item: string; }
export interface RiskResearch { stock_code: string; market: string; as_of: string | null; status: "READY" | "PARTIAL" | "UNKNOWN" | "NOT_APPLICABLE"; overall_risk: "LOW" | "MEDIUM" | "HIGH" | "UNKNOWN"; summary: string; value_trap_risk: "LOW_TRAP_RISK" | "MEDIUM_TRAP_RISK" | "HIGH_TRAP_RISK" | "UNKNOWN" | "NOT_APPLICABLE"; is_current_l3_leader: boolean; valuation_status: string; data_quality: { financial: string; business: string; forecast: string; thesis: string; review_stale: boolean | null; missing: string[] }; risks: RiskResearchItem[]; formula_version: string; }
export interface CompanyResearchConclusion { company: { market: string; stock_code: string; stock_name: string }; thesis: { status: string; label: string; confidence: string | null } | null; entry: { available: boolean; level: string | null; label: string; confidence: string | null; data_gaps: string[] }; exit: { available: boolean; level: string | null; label: string; confidence: string | null; data_gaps: string[] }; fair_value_range: EntryResearchZone | null; focus_zone: EntryResearchZone | null; evidence_counts: { support: number; challenge: number }; research_conclusion: string; data_status: Record<string, string>; formula_version: string; }
export interface LeaderQualityPeerMetric {
  dimension: string; dimension_label: string; metric: string; label: string; unit: string;
  company_value: number | null; peer_median: number | null; peer_percentile: number | null;
  valid_peer_count: number; total_peer_count: number; status: "STRONG" | "ABOVE_AVERAGE" | "NORMAL" | "BELOW_AVERAGE" | "UNKNOWN" | "NOT_SCORED";
  comparison_direction: string; data_quality: string; reporting_period?: string | null; announcement_cutoff?: string | null;
}
export interface LeaderQualityProfile {
  company: { market: string; stock_code: string; stock_name: string };
  research_as_of: string | null;
  leader_position: {
    status: string; level1?: { code: string; name: string }; level2?: { code: string; name: string }; level3?: { code: string; name: string };
    rank?: number | null; leader_score?: number | null; valid_peer_count?: number; total_peer_count?: number;
    score_coverage?: number | null; score_components?: Record<string, number | null>; formula_version?: string; run_id?: string; as_of?: string;
    gap_to_next?: number | null; next_company?: { stock_code: string; stock_name: string; rank: number; leader_score: number } | null; plain_explanation?: string;
  };
  leader_stability: { status: string; observation_window?: string | null; run_count?: number; top1_count?: number; top2_count?: number; rank_path: Array<{ as_of: string; leader_rank: number | null; leader_score?: number | null }>; disclaimer?: string };
  peer_advantages: LeaderQualityPeerMetric[];
  peer_advantage_categories: Array<{ dimension: string; label: string; status: string; metrics: string[]; valid_metric_count: number; total_metric_count: number }>;
  profitability_quality: { status: string; cash_quality_status?: string; positive_ocf_years?: number; annual_observation_count?: number; disclaimer?: string };
  pricing_power_proxy: { status: string; peer_margin_percentile?: number | null; valid_peer_count?: number; disclaimer?: string };
  strengths: Array<{ dimension: string; label: string; status: string }>;
  weaknesses: Array<{ dimension: string; label: string; status: string }>;
  moat_data_gaps: string[];
  data_quality: { status: string; peer_sample?: string; small_peer_sample?: boolean; pit_financial_cutoff?: string | null; missing_fields?: string[]; disclaimer?: string };
  source_traceability?: { l3_run_id?: string; l3_run_as_of?: string; financial?: { report_date?: string | null; announcement_date?: string | null } };
  formula_version: string;
}
export interface MoatResearchDimension {
  dimension: string; label: string; applicability: "APPLICABLE" | "NOT_APPLICABLE" | "UNKNOWN_APPLICABILITY";
  status: "SUPPORTED" | "PARTIAL" | "UNKNOWN"; evidence_balance: "SUPPORTING" | "MIXED" | "CHALLENGED" | "NO_SIGNAL";
  confidence: "HIGH" | "MEDIUM" | "LOW"; persistence: "REPEATED" | "SINGLE_PERIOD" | "DETERIORATING";
  summary: string; supporting_evidence_ids: string[]; counter_evidence_ids: string[]; management_claim_ids: string[]; data_gaps: string[];
  evidence_counts: Record<string, number>;
}
export interface MoatResearch {
  company: { market: string; stock_code: string; stock_name: string }; research_as_of: string | null; status: "READY" | "PARTIAL" | "UNKNOWN";
  industry_context: { level3_name: string | null; applicability_status: string; business_profile_status: string };
  dimensions: MoatResearchDimension[]; supported_advantages: Array<MoatResearchDimension & { why_it_may_matter: string; key_evidence_ids: string[] }>;
  moat_challenges: Array<{ dimension: string; label: string; summary: string; evidence_balance: string; counter_evidence_ids: string[] }>;
  moat_data_gaps: string[]; moat_summary: string; leader_quality_context: Record<string, unknown>; source_status: Record<string, string>; formula_version: string;
}

export interface RegimeEpisode {
  start: string;
  end: string | null;
}

export interface CorrelationRegimeResponse {
  labels: string[];
  dates: string[];
  density: (number | null)[];
  smoothed: (number | null)[];
  fused: number[];
  episodes: RegimeEpisode[];
  params: {
    days: number;
    corr_window: number;
    edge_threshold: number;
    smooth_window: number;
    enter_threshold: number;
    exit_threshold: number;
  };
}

async function errorFromResponse(res: Response): Promise<ApiError> {
  let detail = `HTTP ${res.status}`;
  try {
    const body = await res.json();
    detail = body.detail || body.message || detail;
  } catch { /* ignore */ }
  if (res.status === 401 || res.status === 403) {
    detail = getAuthRequiredMessage();
  }
  return new ApiError(detail, res.status);
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const { headers, ...rest } = options ?? {};
  const mergedHeaders: Record<string, string> = { "Content-Type": "application/json", ...authHeaders() };
  if (headers) {
    new Headers(headers).forEach((value, key) => {
      mergedHeaders[key] = value;
    });
  }
  const res = await fetch(`${BASE}${path}`, {
    ...rest,
    headers: mergedHeaders,
  });
  if (!res.ok) {
    throw await errorFromResponse(res);
  }
  const text = await res.text();
  if (!text) return {} as T;

  const contentType = res.headers.get("content-type") || "";
  if (!contentType.includes("application/json")) {
    const preview = text.slice(0, 80).replace(/\s+/g, " ");
    throw new ApiError(
      `Expected JSON from ${path}, got ${contentType || "unknown content type"}: ${preview}`,
      res.status,
    );
  }

  return JSON.parse(text) as T;
}

async function streamFinancialChat<T extends FinancialAgentReply>(
  path: string,
  body: unknown,
  onProgress?: (progress: FinancialAgentProgress) => void,
): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders(), Accept: "text/event-stream" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw await errorFromResponse(res);
  if (!res.body) throw new ApiError("财报研究过程未返回可读取的事件流", res.status);

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let result: T | null = null;
  const consume = (block: string) => {
    let event = "message";
    const data: string[] = [];
    for (const line of block.split(/\r?\n/)) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
    }
    if (!data.length) return;
    const payload = JSON.parse(data.join("\n")) as Record<string, unknown>;
    if (event === "progress") {
      onProgress?.({
        stage: String(payload.stage || "progress"),
        message: String(payload.message || "正在处理…"),
        details: (payload.details as Record<string, unknown> | undefined) || {},
      });
    } else if (event === "result") {
      result = payload as T;
    } else if (event === "error") {
      throw new ApiError(String(payload.message || "财报研究失败"), Number(payload.status || 500));
    }
  };

  try {
    while (true) {
      const chunk = await reader.read();
      buffer += decoder.decode(chunk.value || new Uint8Array(), { stream: !chunk.done });
      const blocks = buffer.split(/\r?\n\r?\n/);
      buffer = blocks.pop() || "";
      for (const block of blocks) consume(block);
      if (chunk.done) break;
    }
    if (buffer.trim()) consume(buffer);
  } finally {
    reader.releaseLock();
  }
  if (!result) throw new ApiError("财报研究过程未返回最终结论", res.status);
  return result;
}

export interface UploadResult {
  status: string;
  file_path: string;
  filename: string;
}

async function uploadFile(file: File): Promise<UploadResult> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${BASE}/upload`, { method: "POST", headers: authHeaders(), body: form });
  if (!res.ok) {
    throw await errorFromResponse(res);
  }
  return res.json();
}

function appendQueryParam(url: string, key: string, value: string): string {
  const sep = url.includes("?") ? "&" : "?";
  return `${url}${sep}${encodeURIComponent(key)}=${encodeURIComponent(value)}`;
}

export const api = {
  uploadFile,
  getCorrelation: (codes: string, days: number, method: "pearson" | "spearman") =>
    request<CorrelationResponse>(
      `/correlation?codes=${encodeURIComponent(codes)}&days=${encodeURIComponent(String(days))}&method=${encodeURIComponent(method)}`,
    ),
  getCorrelationRegime: (codes: string, days: number) =>
    request<CorrelationRegimeResponse>(
      `/correlation/regime?codes=${encodeURIComponent(codes)}&days=${encodeURIComponent(String(days))}`,
    ),
  listRuns: (limit?: number) => request<RunListItem[]>(`/runs${limit ? `?limit=${encodeURIComponent(String(limit))}` : ""}`),
  getRun: (id: string, params: RunDetailParams = {}) => {
    const q = new URLSearchParams();
    if (params.chart_payload) q.set("chart_payload", params.chart_payload);
    if (params.chart_symbol) q.set("chart_symbol", params.chart_symbol);
    const qs = q.toString();
    return request<RunData>(`/runs/${id}${qs ? `?${qs}` : ""}`);
  },
  getRunCode: (id: string) => request<Record<string, string>>(`/runs/${id}/code`),
  getRunPine: (id: string) => request<PineScriptResult>(`/runs/${id}/pine`),
  listSessions: () => request<SessionItem[]>("/sessions"),
  createSession: (title?: string) => request<SessionItem>("/sessions", { method: "POST", body: JSON.stringify({ title: title || "" }) }),
  deleteSession: (sid: string) => request<{ status: string }>(`/sessions/${sid}`, { method: "DELETE" }),
  renameSession: (sid: string, title: string) => request<{ status: string }>(`/sessions/${sid}`, { method: "PATCH", body: JSON.stringify({ title }) }),
  // Codex-style LLM summary title from the first exchange; backend refuses to
  // overwrite a manual rename, so this is safe to fire-and-forget.
  autoTitleSession: (sid: string) => request<{ status: string; title: string }>(`/sessions/${sid}/title/auto`, { method: "POST" }),
  // Scheduled research: cadence + timezone are stored as authored (local
  // wall-clock cron + IANA key), so list rows render without any UTC math.
  listScheduledRuns: (signal?: AbortSignal) => request<ScheduledRun[]>("/scheduled-runs", { signal }),
  createScheduledRun: (body: CreateScheduledRunRequest) =>
    request<ScheduledRun>("/scheduled-runs", { method: "POST", body: JSON.stringify(body) }),
  deleteScheduledRun: (id: string) =>
    request<void>(`/scheduled-runs/${encodeURIComponent(id)}`, { method: "DELETE" }),
  sendMessage: (sid: string, content: string) => request<{ message_id: string; attempt_id: string }>(`/sessions/${sid}/messages`, { method: "POST", body: JSON.stringify({ content }) }),
  cancelSession: (sid: string) => request<{ status: string }>(`/sessions/${sid}/cancel`, { method: "POST" }),
  getSessionMessages: (sid: string) => request<MessageItem[]>(`/sessions/${sid}/messages`),
  createGoal: (sid: string, body: CreateGoalRequest) =>
    request<GoalSnapshot>(`/sessions/${sid}/goal`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  getGoal: (sid: string) => request<GoalSnapshot>(`/sessions/${sid}/goal`),
  updateGoal: (sid: string, body: UpdateGoalRequest) =>
    request<UpdateGoalResponse>(`/sessions/${sid}/goal`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  addGoalEvidence: (sid: string, body: AddGoalEvidenceRequest) =>
    request<AddGoalEvidenceResponse>(`/sessions/${sid}/goal/evidence`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updateGoalStatus: (sid: string, body: UpdateGoalStatusRequest) =>
    request<UpdateGoalStatusResponse>(`/sessions/${sid}/goal/status`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  // Returns the bare stream URL (no auth in the query string). The SSE ticket
  // is minted per connect/reconnect inside useSSE (tickets are single-use, so
  // baking one into a cached URL would break reconnection).
  sseUrl: (sid: string, options?: { replay?: "active" }) => {
    let url = `${BASE}/sessions/${sid}/events`;
    if (options?.replay) url = appendQueryParam(url, "replay", options.replay);
    return url;
  },

  // Swarm API
  listSwarmPresets: () => request<SwarmPreset[]>("/swarm/presets"),
  createSwarmRun: (preset_name: string, user_vars: Record<string, string>) =>
    request<{ id: string; status: string }>("/swarm/runs", {
      method: "POST",
      body: JSON.stringify({ preset_name, user_vars }),
    }),
  listSwarmRuns: () => request<SwarmRunSummary[]>("/swarm/runs"),
  getSwarmRun: (id: string) => request<Record<string, unknown>>(`/swarm/runs/${id}`),
  swarmSseUrl: (id: string) => withAuthTicket(`${BASE}/swarm/runs/${id}/events`),
  cancelSwarmRun: (id: string) =>
    request<{ status: string }>(`/swarm/runs/${id}/cancel`, { method: "POST" }),
  retrySwarmRun: (id: string) =>
    request<{ id: string; status: string; preset_name: string }>(`/swarm/runs/${id}/retry`, { method: "POST" }),
  getLLMSettings: () => request<LLMSettings>("/settings/llm"),
  getResearchAgentConfigs: () =>
    request<{ items: ResearchAgentConfig[] }>("/api/agents/config"),
  getResearchAgentProviders: () =>
    request<{ items: ResearchAgentProvider[] }>("/api/agents/providers"),
  updateResearchAgentConfig: (role: ResearchAgentRole, body: Pick<ResearchAgentConfig, "provider" | "model" | "enabled">) =>
    request<ResearchAgentConfig>(`/api/agents/config/${encodeURIComponent(role)}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  getResearchAgentModelSettings: () =>
    request<{ items: ResearchAgentModelSetting[] }>("/api/agents/model-settings"),
  updateResearchAgentModelSetting: (role: ResearchAgentRole, body: Pick<ResearchAgentModelSetting, "model_id" | "enabled">) =>
    request<ResearchAgentModelSetting>(`/api/agents/model-settings/${encodeURIComponent(role)}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  getResearchAgentConnections: () =>
    request<{ items: ResearchAgentConnectionSetting[] }>("/api/agents/connections"),
  updateResearchAgentConnection: (role: ResearchAgentRole, body: {
    base_url: string; model: string; api_key?: string; clear_api_key?: boolean; enabled: boolean;
  }) => request<ResearchAgentConnectionSetting>(`/api/agents/connections/${encodeURIComponent(role)}`, {
    method: "PUT",
    body: JSON.stringify(body),
  }),
  getValueIndustries: () =>
    request<{ items: ValueIndustry[]; total: number; level1_total: number; level2_total: number; level3_total: number; source: string }>("/api/value/industries"),
  getAllLevel3Leaders: (limit = 2, asOf?: string) =>
    request<{
      as_of: string | null;
      items: Record<string, Level3Leader[]>;
      total: number;
      snapshot_status: "ready" | "not_built";
    }>(`/api/value/level3-leaders?limit=${encodeURIComponent(String(limit))}${asOf ? `&as_of=${encodeURIComponent(asOf)}` : ""}`),
  getLevel3IndustryLeaders: (industryCode: string, limit = 2) =>
    request<{
      industry: ValueIndustry;
      as_of: string | null;
      formula_version: string;
      company_count: number;
      eligible_count: number;
      items: Level3Leader[];
      excluded_items: ExcludedLevel3Leader[];
      quality: LeaderIndustrySummary;
      formula: LeaderFormulaContract;
      total_ranked: number;
      snapshot_status: "ready" | "not_built";
      comparison_scope: string;
    }>(`/api/value/industries/${encodeURIComponent(industryCode)}/leaders?limit=${limit}`),
  getCurrentLeaderPool: () => request<ValueLeaderPool>("/api/value/current-leader-pool"),
  getLowValueLeaders: () => request<LowValueLeaderPoolResponse>("/api/value/low-value-leaders"),
  getLowValueLeaderEvents: () => request<LowValueLeaderEventsResponse>("/api/value/low-value-leader-events"),
  getFocusSelection: (asOf?: string) => request<FocusSelectionResponse>(`/api/value/focus-selection${asOf ? `?as_of=${encodeURIComponent(asOf)}` : ""}`),
  getInvestmentResearchDailyBrief: () => request<InvestmentResearchDailyBrief>("/api/research-supervisor/daily-brief"),
  getLeaderPools: (limit = 30) => request<{ items: ValueLeaderPoolSummary[] }>(`/api/value/leader-pools?limit=${limit}`),
  getLeaderPool: (poolId: string) => request<ValueLeaderPool>(`/api/value/leader-pools/${encodeURIComponent(poolId)}`),
  getValueResearchStates: () => request<{ pool_id: string; as_of: string; items: ValueCompanyResearchState[] }>("/strategy/value/research-states"),
  updateValueResearchState: (stockCode: string, isPriority: boolean) => request<ValueCompanyResearchState>(`/strategy/value/research-states/${encodeURIComponent(stockCode)}`, { method: "PATCH", body: JSON.stringify({ is_priority: isPriority }) }),
  refreshValueCompanyResearch: (stockCode: string) => request<FinancialAnalysisSnapshot>(`/strategy/value/research-states/${encodeURIComponent(stockCode)}/refresh`, { method: "POST" }),
  getCompanyFinancialAnalysis: (stockCode: string, asOf?: string) =>
    request<FinancialAnalysisSnapshot>(`/api/value/companies/${encodeURIComponent(stockCode)}/financial${asOf ? `?as_of=${encodeURIComponent(asOf)}` : ""}`),
  getCompanyFinancialDossier: (stockCode: string, asOf?: string) =>
    request<FinancialDossier>(`/api/value/companies/${encodeURIComponent(stockCode)}/financial/dossier${asOf ? `?as_of=${encodeURIComponent(asOf)}` : ""}`),
  getCompanyCompactDailyBars: (stockCode: string, options?: { as_of?: string; limit?: number }) =>
    request<CompactDailyBars>(`/api/value/companies/${encodeURIComponent(stockCode)}/daily-bars/compact?market=CN${options?.as_of ? `&as_of=${encodeURIComponent(options.as_of)}` : ""}${options?.limit ? `&limit=${options.limit}` : ""}`),
  analyzeCompanyFinancials: (stockCode: string, body: { as_of?: string; refresh?: boolean } = {}) =>
    request<FinancialAnalysisSnapshot>(`/api/value/companies/${encodeURIComponent(stockCode)}/financial/analyze`, {
      method: "POST", body: JSON.stringify(body),
    }),
  getCompanyBusinessResearch: (stockCode: string) =>
    request<BusinessResearchSnapshot>(`/api/value/companies/${encodeURIComponent(stockCode)}/business-research`),
  analyzeCompanyBusinessResearch: (stockCode: string, body: { force?: boolean } = {}) =>
    request<BusinessResearchSnapshot>(`/api/value/companies/${encodeURIComponent(stockCode)}/business-research/analyze`, {
      method: "POST", body: JSON.stringify(body),
    }),
  getCompanyResearchOverview: (stockCode: string) =>
    request<CompanyResearchOverview>(`/api/value/companies/${encodeURIComponent(stockCode)}/research-overview?market=CN`),
  getCompanyResearchConclusion: (stockCode: string) =>
    request<CompanyResearchConclusion>("/api/value/companies/" + encodeURIComponent(stockCode) + "/research-conclusion?market=CN"),
  getCapitalAllocationFacts: (stockCode: string, asOf?: string) =>
    request<CapitalAllocationFacts>(`/api/value/companies/${encodeURIComponent(stockCode)}/capital-allocation-facts?market=CN${asOf ? `&as_of=${encodeURIComponent(asOf)}` : ""}`),
  getCapitalAllocationResearch: (stockCode: string, asOf?: string) =>
    request<CapitalAllocationResearch>(`/api/value/companies/${encodeURIComponent(stockCode)}/capital-allocation-research?market=CN${asOf ? `&as_of=${encodeURIComponent(asOf)}` : ""}`),
  getCompanyActions: (stockCode: string, asOf?: string) =>
    request<CompanyActionResponse>(`/api/value/companies/${encodeURIComponent(stockCode)}/company-actions?market=CN${asOf ? `&as_of=${encodeURIComponent(asOf)}` : ""}`),
  prepareCompanyActions: (stockCode: string) =>
    request<CompanyActionPrepareResponse>(`/api/value/companies/${encodeURIComponent(stockCode)}/company-actions/prepare?market=CN`, { method: "POST" }),
  getLeaderQualityProfile: (stockCode: string, asOf?: string) =>
    request<LeaderQualityProfile>(`/api/value/companies/${encodeURIComponent(stockCode)}/leader-quality?market=CN${asOf ? `&as_of=${encodeURIComponent(asOf)}` : ""}`),
  getMoatResearch: (stockCode: string, asOf?: string) =>
    request<MoatResearch>(`/api/value/companies/${encodeURIComponent(stockCode)}/moat-research?market=CN${asOf ? `&as_of=${encodeURIComponent(asOf)}` : ""}`),
  getCompanyPriceZones: (stockCode: string, asOf?: string) =>
    request<ValuePriceZones>(`/api/value/companies/${encodeURIComponent(stockCode)}/price-zones?market=CN${asOf ? `&as_of=${encodeURIComponent(asOf)}` : ""}`),
  getCompanyEntryResearch: (stockCode: string, asOf?: string) =>
    request<EntryResearch>(`/api/value/companies/${encodeURIComponent(stockCode)}/entry-research?market=CN${asOf ? `&as_of=${encodeURIComponent(asOf)}` : ""}`),
  getCompanyExitResearch: (stockCode: string, asOf?: string) =>
    request<ExitResearch>("/api/value/companies/" + encodeURIComponent(stockCode) + "/exit-research?market=CN" + (asOf ? "&as_of=" + encodeURIComponent(asOf) : "")),
  getValueStrategyState: (stockCode: string, researchAsOf?: string) =>
    request<ValueStrategyState>(`/api/value/companies/${encodeURIComponent(stockCode)}/strategy-state?market=CN${researchAsOf ? `&research_as_of=${encodeURIComponent(researchAsOf)}` : ""}`),
  getValueWatchpoints: (stockCode: string, researchAsOf?: string, limit?: number) =>
    request<ValueWatchpointProjection>(`/api/value/companies/${encodeURIComponent(stockCode)}/watchpoints?market=CN${researchAsOf ? `&research_as_of=${encodeURIComponent(researchAsOf)}` : ""}${limit != null ? `&limit=${limit}` : ""}`),
  getValueStrategyEventBatches: (stockCode?: string, limit = 10) => request<{ items: ValueStrategyEventBatch[]; count: number }>(`/api/value/strategy-event-batches?limit=${limit}${stockCode ? `&stock_code=${encodeURIComponent(stockCode)}` : ""}`),
  acknowledgeValueStrategyEvent: (eventId: string) => request(`/api/value/strategy-events/${encodeURIComponent(eventId)}/acknowledge`, { method: "POST" }),
  closeValueStrategyEvent: (eventId: string) => request(`/api/value/strategy-events/${encodeURIComponent(eventId)}/close`, { method: "POST" }),
  getCompanyRiskResearch: (stockCode: string, asOf?: string) =>
    request<RiskResearch>("/api/value/companies/" + encodeURIComponent(stockCode) + "/risk-research?market=CN" + (asOf ? "&as_of=" + encodeURIComponent(asOf) : "")),
  getCompanyValuationHistory: (stockCode: string, asOf?: string) =>
    request<HistoricalValuationHistory>(`/api/value/companies/${encodeURIComponent(stockCode)}/valuation-history?market=CN${asOf ? `&as_of=${encodeURIComponent(asOf)}` : ""}`),
  rebuildCompanyPriceZones: (stockCode: string, asOf?: string) =>
    request<ValuePriceZones>(`/api/value/companies/${encodeURIComponent(stockCode)}/price-zones/rebuild?market=CN${asOf ? `&as_of=${encodeURIComponent(asOf)}` : ""}`, { method: "POST" }),
  getCompanyThesis: (stockCode: string) =>
    request<{ status: string; thesis: CompanyThesis | null }>(`/api/value/companies/${encodeURIComponent(stockCode)}/thesis?market=CN`),
  getCompanyThesisDraft: (stockCode: string) =>
    request<{ status: string; draft: CompanyThesisDraft | null }>(`/api/value/companies/${encodeURIComponent(stockCode)}/thesis/draft?market=CN`),
  generateCompanyThesisDraft: (stockCode: string) =>
    request<{ status: string; draft: CompanyThesisDraft | null; message?: string }>(`/api/value/companies/${encodeURIComponent(stockCode)}/thesis/draft?market=CN`, { method: "POST" }),
  confirmCompanyThesisDraft: (stockCode: string, draftId: string, body: {
    title: string; core_thesis: string; status: CompanyThesisDraft["status"]; confidence: CompanyThesisDraft["confidence"];
    invalid_conditions: Array<{ condition: string; status?: string }>;
    supporting_conditions?: Array<{ condition: string; status?: string }>;
    key_metrics_to_monitor?: Array<{ text: string } | string>;
  }) => request<{ status: string; draft: CompanyThesisDraft; thesis: CompanyThesis }>(
    `/api/value/companies/${encodeURIComponent(stockCode)}/thesis/draft/${encodeURIComponent(draftId)}/confirm?market=CN`, { method: "POST", body: JSON.stringify(body) },
  ),
  rejectCompanyThesisDraft: (stockCode: string, draftId: string, reason = "人工未采纳") =>
    request<{ status: string; draft: CompanyThesisDraft }>(
      `/api/value/companies/${encodeURIComponent(stockCode)}/thesis/draft/${encodeURIComponent(draftId)}/reject?market=CN`, { method: "POST", body: JSON.stringify({ reason }) },
    ),
  getCompanyThesisReview: (stockCode: string) =>
    request<CompanyThesisReviewResponse>(`/api/value/companies/${encodeURIComponent(stockCode)}/thesis/review?market=CN`),
  getCompanyThesisEvidence: (stockCode: string) =>
    request<CompanyThesisEvidenceResponse>(`/api/value/companies/${encodeURIComponent(stockCode)}/thesis/evidence?market=CN`),
  getCompanyThesisHistory: (stockCode: string) =>
    request<{ items: CompanyThesisHistoryItem[]; total: number }>(`/api/value/companies/${encodeURIComponent(stockCode)}/thesis/history?market=CN`),
  refreshCompanyThesisReview: (stockCode: string) =>
    request<CompanyThesisReviewResponse>(`/api/value/companies/${encodeURIComponent(stockCode)}/thesis/review?market=CN`, { method: "POST" }),
  chatCompanyFinancials: (stockCode: string, body: { question: string; as_of?: string; history?: Array<{ role: string; content: string }> }) =>
    request<{ stock_code: string; stock_name: string; as_of: string; answer: string; provider: string; model: string }>(
      `/api/value/companies/${encodeURIComponent(stockCode)}/financial/chat`,
      { method: "POST", body: JSON.stringify(body) },
    ),
  streamCompanyFinancials: (
    stockCode: string,
    body: { question: string; as_of?: string; history?: Array<{ role: string; content: string }> },
    onProgress?: (progress: FinancialAgentProgress) => void,
  ) => streamFinancialChat<FinancialAgentReply>(
    `/api/value/companies/${encodeURIComponent(stockCode)}/financial/chat/stream`, body, onProgress,
  ),
  chatFinancialAgent: (body: { question: string; history?: Array<{ role: string; content: string }>; candidates?: Array<Record<string, unknown>> }) =>
    request<FinancialAgentReply>(
      "/api/value/financial-agent/chat",
      { method: "POST", body: JSON.stringify(body) },
    ),
  streamFinancialAgent: (
    body: { question: string; history?: Array<{ role: string; content: string }>; candidates?: Array<Record<string, unknown>> },
    onProgress?: (progress: FinancialAgentProgress) => void,
  ) => streamFinancialChat<FinancialAgentReply>("/api/value/financial-agent/chat/stream", body, onProgress),
  updateLLMSettings: (settings: UpdateLLMSettingsRequest) =>
    request<LLMSettings>("/settings/llm", {
      method: "PUT",
      body: JSON.stringify(settings),
    }),
  listLLMModels: (settings: ListLLMModelsRequest) =>
    request<LLMModelsResponse>("/settings/llm/models", {
      method: "POST",
      body: JSON.stringify(settings),
    }),
  getDataSourceSettings: () => request<DataSourceSettings>("/settings/data-sources"),
  updateDataSourceSettings: (settings: UpdateDataSourceSettingsRequest) =>
    request<DataSourceSettings>("/settings/data-sources", {
      method: "PUT",
      body: JSON.stringify(settings),
    }),
  getChannelStatus: () => request<ChannelRuntimeStatus>("/channels/status"),
  getFeishuChannelConfig: () => request<FeishuChannelConfig>("/channels/feishu/config"),
  updateFeishuChannelConfig: (body: FeishuChannelConfigUpdate) =>
    request<FeishuChannelConfigUpdateResponse>("/channels/feishu/config", {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  startChannels: () => request<ChannelRuntimeActionResponse>("/channels/start", { method: "POST" }),
  stopChannels: () => request<ChannelRuntimeActionResponse>("/channels/stop", { method: "POST" }),
  runChannelPairingCommand: (body: ChannelPairingCommandRequest) =>
    request<ChannelPairingCommandResponse>("/channels/pairing/command", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  // Alpha Zoo API
  listAlphas: (params: AlphaListParams = {}) => {
    const q = new URLSearchParams();
    if (params.zoo) q.set("zoo", params.zoo);
    if (params.theme) q.set("theme", params.theme);
    if (params.universe) q.set("universe", params.universe);
    if (params.limit !== undefined) q.set("limit", String(params.limit));
    const qs = q.toString();
    return request<AlphaListResponse>(`/alpha/list${qs ? `?${qs}` : ""}`);
  },
  getAlpha: (alphaId: string) =>
    request<AlphaDetailResponse>(`/alpha/${encodeURIComponent(alphaId)}`),
  createAlphaBench: (body: AlphaBenchRequest) =>
    request<{ status: string; job_id: string }>("/alpha/bench", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  alphaBenchStreamUrl: (jobId: string) =>
    withAuthTicket(`${BASE}/alpha/bench/${encodeURIComponent(jobId)}/stream`),
  createAlphaCompare: (body: AlphaCompareRequest) =>
    request<{ status: string; job_id: string }>("/alpha/compare", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  alphaCompareStreamUrl: (jobId: string) =>
    withAuthTicket(`${BASE}/alpha/compare/${encodeURIComponent(jobId)}/stream`),

  // 恒值投资 structured research workspace
  getDashboard: () => request<DashboardToday>("/dashboard/today"),
  getTdxStatus: () => request<TdxStatus>("/tdx/status"),
  getTdxMarketCatalog: (market: "HK" | "US") => request<TdxMarketCatalog>(`/tdx/markets/${market}/catalog`),
  refreshTdxMarketCatalog: (market: "HK" | "US") => request<TdxJob>(`/tdx/markets/${market}/catalog-refresh`, { method: "POST" }),
  getTdxMarketCatalogQuotes: (market: "HK" | "US", limit = 12) => request<TdxMarketCatalogQuotes>(`/tdx/markets/${market}/quotes?limit=${limit}`),
  getTdxRefreshRuns: (limit = 20) => request<{ items: TdxRefreshRun[] }>(`/tdx/refresh-runs?limit=${limit}`),
  getTdxSnapshots: (snapshotId?: string) => request<{ active_close_snapshot: TdxRefreshRun | null; items: TdxDatasetSnapshot[] }>(`/tdx/snapshots${snapshotId ? `?snapshot_id=${encodeURIComponent(snapshotId)}` : ""}`),
  setTdxAutomation: (enabled: boolean) => request<TdxRefreshAutomation>("/tdx/automation", { method: "PUT", body: JSON.stringify({ enabled }) }),
  getTdxMarketOverview: () => request<TdxMarketOverview>("/tdx/market/overview"),
  getTdxMarketRanks: (options?: { category?: string; query?: string; sector?: string; sort?: string; direction?: string; limit?: number; offset?: number }) =>
    request<TdxRankResult>(`/tdx/market/ranks?${queryString(options)}`),
  getTdxSectors: (options?: { category?: string; query?: string; limit?: number; offset?: number }) =>
    request<TdxSectorResult>(`/tdx/sectors?${queryString(options)}`),
  getTdxSector: (code: string) => request<TdxSectorDetail>(`/tdx/sectors/${encodeURIComponent(code)}`),
  screenTdxSecurities: (options?: TdxScreenerFilters) =>
    request<TdxScreenerResult>(`/tdx/screener?${queryString(options)}`),
  getTdxFunds: (options?: { category?: string; query?: string; limit?: number; offset?: number }) =>
    request<TdxFundResult>(`/tdx/funds?${queryString(options)}`),
  searchTdxSecurities: (query: string, limit = 20) =>
    request<{ items: TdxSecuritySearchItem[] }>(`/tdx/securities/search?query=${encodeURIComponent(query)}&limit=${limit}`),
  getTdxSecurityOverview: (symbol: string) =>
    request<TdxSecurityOverview>(`/tdx/securities/${encodeURIComponent(symbol)}/overview`),
  getTdxFinancialHistory: (symbol: string, options?: { as_of?: string; period_type?: string }) =>
    request<TdxFinancialHistory>(`/tdx/financial-history/${encodeURIComponent(symbol)}?${queryString(options)}`),
  startTdxFormulaScan: (body: TdxFormulaScanRequest) =>
    request<TdxFormulaScan>("/tdx/formula-scans", { method: "POST", body: JSON.stringify(body) }),
  getTdxFormulaScan: (id: string) => request<TdxFormulaScan>(`/tdx/formula-scans/${encodeURIComponent(id)}`),
  startTdxUpdate: (module = "all") =>
    request<TdxJob>("/tdx/update", { method: "POST", body: JSON.stringify({ module }) }),
  getTdxJob: (id: string) => request<TdxJob>(`/tdx/jobs/${encodeURIComponent(id)}`),
  getTdxData: (dataset: string, options?: { category?: string; query?: string; limit?: number; offset?: number }) => {
    const params = new URLSearchParams();
    if (options?.category) params.set("category", options.category);
    if (options?.query) params.set("query", options.query);
    if (options?.limit) params.set("limit", String(options.limit));
    if (options?.offset) params.set("offset", String(options.offset));
    const suffix = params.size ? `?${params.toString()}` : "";
    return request<TdxDataset>(`/tdx/data/${encodeURIComponent(dataset)}${suffix}`);
  },
  refreshTdxSecurity: (symbol: string) =>
    request<Record<string, unknown>>("/tdx/securities/refresh", { method: "POST", body: JSON.stringify({ symbol }) }),
  getTdxSecurity: (symbol: string) => request<Record<string, unknown>>(`/tdx/securities/${encodeURIComponent(symbol)}`),
  getTdxKline: (body: { symbol: string; period?: string; count?: number; dividend_type?: string }) =>
    request<Record<string, unknown>>("/tdx/klines", { method: "POST", body: JSON.stringify(body) }),
  refreshDashboard: (body: { module?: string; market?: MarketCode }) =>
    request<ResearchRun>("/dashboard/refresh", { method: "POST", body: JSON.stringify(body) }),
  getResearchRun: (id: string) => request<ResearchRun>(`/research-runs/${encodeURIComponent(id)}`),
  getMacroBrief: (market: MarketCode) => request<MacroBrief>(`/macro/briefs/latest?market=${market}`),
  getSectorRankings: (market: MarketCode) => request<{ market: MarketCode; items: SectorScore[] }>(`/sectors/rankings?market=${market}`),
  screenSecurities: (market: MarketCode, query = "") => request<{ market: MarketCode; items: SecurityCandidate[] }>(`/securities/screener?market=${market}&query=${encodeURIComponent(query)}`),
  getCompanyDossier: (market: MarketCode, symbol: string) => request<CompanyDossier>(`/securities/${market}/${encodeURIComponent(symbol)}/dossier`),
  researchCompany: (market: MarketCode, symbol: string) => request<CompanyResearchResult>(`/securities/${market}/${encodeURIComponent(symbol)}/research`, { method: "POST", body: JSON.stringify({ create_report: true }) }),
  listResearchReports: (reportType?: string) => request<ResearchReport[]>(`/reports${reportType ? `?report_type=${encodeURIComponent(reportType)}` : ""}`),
  getResearchReport: (id: string) => request<ResearchReport>(`/reports/${encodeURIComponent(id)}`),
  listCommittees: () => request<Committee[]>("/committees"),
  getCommittee: (id: string) => request<Committee>(`/committees/${encodeURIComponent(id)}`),
  createCommittee: (body: { market: MarketCode; symbol: string; company_name?: string }) => request<Committee>("/committees", { method: "POST", body: JSON.stringify(body) }),
  cancelCommittee: (id: string) => request<Committee>(`/committees/${encodeURIComponent(id)}/cancel`, { method: "POST" }),
  committeeSseUrl: (id: string) => withAuthTicket(`${BASE}/committees/${encodeURIComponent(id)}/events`),
  listTradePlans: (status?: TradePlanStatus) => request<TradePlan[]>(`/trade-plans${status ? `?status=${status}` : ""}`),
  createTradePlan: (body: Partial<TradePlan>) => request<TradePlan>("/trade-plans", { method: "POST", body: JSON.stringify(body) }),
  updateTradePlan: (id: string, body: Partial<TradePlan>) => request<TradePlan>(`/trade-plans/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(body) }),
  listPortfolios: () => request<Portfolio[]>("/portfolios"),
  getPortfolio: (id: string) => request<Portfolio>(`/portfolios/${encodeURIComponent(id)}`),
  createPortfolio: (body: CreatePortfolioRequest) => request<Portfolio>("/portfolios", { method: "POST", body: JSON.stringify(body) }),
  updatePortfolio: (id: string, body: Partial<Portfolio>) => request<Portfolio>(`/portfolios/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(body) }),
  deletePortfolio: (id: string) => request<void>(`/portfolios/${encodeURIComponent(id)}`, { method: "DELETE" }),
  addPortfolioTransaction: (id: string, body: PortfolioTransactionRequest) => request<PortfolioTransaction>(`/portfolios/${encodeURIComponent(id)}/transactions`, { method: "POST", body: JSON.stringify(body) }),
  getPortfolioPositions: (id: string) => request<PortfolioPosition[]>(`/portfolios/${encodeURIComponent(id)}/positions`),
  getPortfolioAnalytics: (id: string) => request<PortfolioAnalytics>(`/portfolios/${encodeURIComponent(id)}/analytics`),
  importPortfolioCsv: async (id: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch(`${BASE}/portfolios/${encodeURIComponent(id)}/import`, { method: "POST", headers: authHeaders(), body: form });
    if (!res.ok) throw await errorFromResponse(res);
    return res.json() as Promise<{ imported: number; errors: Array<{ line: number; error: string }> }>;
  },

  // Deterministic value/emotion strategy engines
  createStrategyRun: (body: CreateStrategyRunRequest) =>
    request<EngineRun>("/strategy-runs", { method: "POST", body: JSON.stringify(body) }),
  getStrategyRun: (id: string) => request<EngineRun>(`/strategy-runs/${encodeURIComponent(id)}`),
  getStrategyFormulas: () => request<StrategyFormulaResponse>("/strategy/formulas"),
  getStrategyDataStatus: () => request<StrategyDataStatus>("/strategy/data/status"),
  getValueDashboard: (market: "CN" | "HK") => request<StrategyDashboard>(`/strategy/value/dashboard?market=${market}`),
  getValueDataStatus: () => request<ValueDataStatus>("/strategy/value/data/status"),
  startValueRefresh: (modules: ValueRefreshModule[] = ["all"], asOf?: string) =>
    request<ValueRefreshJob>("/strategy/value/refresh", { method: "POST", body: JSON.stringify({ modules, as_of: asOf }) }),
  getValueRefreshJob: (id: string) => request<ValueRefreshJob>(`/strategy/value/refresh/${encodeURIComponent(id)}`),
  getValueMacro: (asOf?: string) => request<ValueMacroSnapshot>(`/strategy/value/macro${asOf ? `?as_of=${encodeURIComponent(asOf)}` : ""}`),
  getValuePolicies: (options?: { status?: string; limit?: number }) =>
    request<{ items: ValuePolicyEvent[] }>(`/strategy/value/policies?${queryString(options)}`),
  getValueSignals: (market: "CN" | "HK") => request<StrategySignal[]>(`/strategy/value/signals?market=${market}`),
  getValueAutomation: () => request<ValueResearchAutomation>("/strategy/value/automation"),
  updateValueAutomation: (enabled: boolean) => request<ValueResearchAutomation>("/strategy/value/automation", { method: "PATCH", body: JSON.stringify({ enabled }) }),
  runValueAutomationNow: () => request<ValueResearchManualRun>("/strategy/value/automation/run-now", { method: "POST" }),
  getEmotionDashboard: (market: "CN" | "HK") => request<StrategyDashboard>(`/strategy/emotion/dashboard?market=${market}`),
  getEmotionSectors: (market: "CN" | "HK") => request<StrategyScoreList>(`/strategy/emotion/sectors?market=${market}`),
  getEmotionCandidates: (market: "CN" | "HK", horizon: "short" | "swing") =>
    request<StrategyScoreList>(`/strategy/emotion/candidates?market=${market}&horizon=${horizon}`),
  getEmotionSignals: (market: "CN" | "HK", horizon?: "short" | "swing") =>
    request<StrategySignal[]>(`/strategy/emotion/signals?market=${market}${horizon ? `&horizon=${horizon}` : ""}`),
  getDecisionChain: (runId: string) => request<DecisionChain>(`/decision-chains/${encodeURIComponent(runId)}`),
  getStrategySignal: (signalId: string) => request<StrategySignal>(`/signals/${encodeURIComponent(signalId)}`),
  createStrategyCommittee: (body: StrategyCommitteeRequest) =>
    request<Committee>("/committees", { method: "POST", body: JSON.stringify(body) }),
  publishCommitteeDecision: (committeeId: string, body: CommitteeDecisionRequest) =>
    request<StructuredCommitteeDecision>(`/committees/${encodeURIComponent(committeeId)}/decision`, { method: "POST", body: JSON.stringify(body) }),
  listPaperAccounts: () => request<PaperAccount[]>("/paper/accounts"),
  getPaperAccount: (id: string) => request<PaperAccount>(`/paper/accounts/${encodeURIComponent(id)}`),
  getPaperOrders: (id: string) => request<PaperOrder[]>(`/paper/accounts/${encodeURIComponent(id)}/orders`),
  getPaperPositions: (id: string) => request<PaperPosition[]>(`/paper/accounts/${encodeURIComponent(id)}/positions`),
  getPaperNav: (id: string) => request<PaperNav>(`/paper/accounts/${encodeURIComponent(id)}/nav`),
  submitPaperOrder: (id: string, body: PaperOrderRequest) =>
    request<PaperOrder>(`/paper/accounts/${encodeURIComponent(id)}/orders`, { method: "POST", body: JSON.stringify(body) }),
};

export type MarketCode = "CN" | "HK" | "US";
export type SourceStatus = "live" | "agent" | "sample" | "stale" | string;

export interface CreateStrategyRunRequest { strategy_line: "value" | "emotion"; market: "CN" | "HK"; as_of?: string; symbols?: string[]; force_refresh?: boolean; }
export type ValueRefreshModule = "financial_history" | "market_history" | "macro" | "policy" | "all";
export interface ValueModuleState { code: Exclude<ValueRefreshModule, "all">; label: string; status: string; progress: number; total: number; item_count: number; message: string; error: string; metadata: Record<string, unknown>; started_at?: string | null; updated_at?: string | null; last_success_at?: string | null; }
export interface ValueRefreshJob { id: string; modules: ValueRefreshModule[]; as_of: string; status: string; current_module: string; progress: number; total: number; results: Record<string, unknown>; errors: Array<{ module: string; error: string }>; created_at: string; started_at?: string | null; completed_at?: string | null; }
export interface ValueResearchManualRun { status: string; as_of?: string; pool_id?: string; stages?: Record<string, string>; error?: string; reason?: string; }
export interface ValueDataStatus { professional_finance: { status: string; file_count: number; first_period?: string | null; last_period?: string | null; raw_version?: string | null }; modules: ValueModuleState[]; recent_jobs: ValueRefreshJob[]; schedule_template?: { name: string; cron: string; timezone: string; modules: ValueRefreshModule[]; enabled: boolean }; }
export interface ValueMacroSnapshot {
  id?: string;
  as_of?: string | null;
  formula_version?: string;
  regime?: string;
  score?: number | null;
  coverage?: number;
  axis_coverage?: number;
  series_coverage?: number;
  series_count?: number;
  series_total?: number;
  release_verified_coverage?: number;
  first_observed_count?: number;
  confidence?: "LOW" | "MEDIUM" | "HIGH";
  status: string;
  axes: Record<string, number | null>;
  states?: Record<string, string>;
  missing_fields?: string[];
  missing_series?: string[];
  sources?: string[];
  provenance_key?: string;
}
export interface ValuePolicyEvent { id: string; document_number: string; title: string; normalized_url: string; source: string; published_at?: string | null; fetched_at: string; status: string; classifications: Array<{ industry_code: string; industry_name: string; direction: number; strength: number; sensitivity: number; horizon_days: number; evidence: string; confidence: number; status: string }>; }
export interface TdxFinancialHistory { symbol: string; as_of?: string | null; period_type?: string | null; total: number; items: Array<Record<string, number | string | null>>; package: Record<string, unknown>; }
export interface EngineRun { id: string; strategy_line: "value" | "emotion"; market: "CN" | "HK"; as_of: string; formula_version: string; status: string; source_status: string; message: string; started_at: string; completed_at?: string | null; created?: boolean; }
export interface ValueResearchAutomation { id: "default"; enabled: boolean; timezone: "Asia/Shanghai"; run_time: "16:45"; max_retries: number; retry_minutes: number; next_run_at?: string | null; last_run_id?: string | null; last_status?: string | null; last_error: string; updated_at: string; }
export interface StrategyFormula { id: string; strategy_line: "value" | "emotion"; name: string; version: string; weights: Record<string, number>; engine_path: string; universe: string; minimum_coverage: number | null; }
export interface StrategyFormulaResponse { items: StrategyFormula[]; strategy_store_ids: string[]; }
export interface StrategyProviderStatus { market: "CN" | "HK"; status: string; provider?: string | null; fallback_chain: string[]; error?: string; }
export interface StrategyDataCatalogItem { id: string; market: string; dataset: string; partition_path: string; provider: string; data_as_of: string; available_at: string; row_count: number; coverage: number; status: string; metadata: Record<string, unknown>; }
export interface StrategyDataStatus { providers: StrategyProviderStatus[]; partitions: number; catalog: StrategyDataCatalogItem[]; }
export interface EngineScore { id: string; engine_run_id: string; engine: string; formula_version: string; strategy_line: "value" | "emotion"; market: "CN" | "HK"; subject_type: string; subject_id: string; data_as_of: string; available_at: string; raw_features: Record<string, number | null>; normalized_features: Record<string, number | null>; component_scores: Record<string, number | null>; base_score: number | null; coverage: number; status: string; quality_flags: string[]; evidence_ids: string[]; }
export interface EngineRegime { id: string; strategy_line: "value" | "emotion"; market: "CN" | "HK"; regime: string; previous_regime?: string | null; score?: number | null; confidence: number; coverage: number; triggers: string[]; data_as_of: string; formula_version: string; }
export interface StrategySignal { id: string; engine_run_id: string; strategy_line: "value" | "emotion"; horizon: "long" | "short" | "swing"; market: "CN" | "HK"; symbol: string; data_as_of: string; valid_from: string; valid_until: string; direction: string; base_score: number; entry_low?: number | null; entry_high?: number | null; stop_price?: number | null; target_low?: number | null; target_high?: number | null; position_cap: number; coverage: number; formula_versions: string[]; evidence_ids: string[]; status: string; invalidation_rules: string[]; }
export interface StrategyDashboard { strategy_line: "value" | "emotion"; market: "CN" | "HK"; latest_run?: EngineRun | null; regime?: EngineRegime | null; scores: EngineScore[]; signals: StrategySignal[]; }
export interface StrategyScoreList { market: "CN" | "HK"; items: EngineScore[]; run?: EngineRun | null; horizon?: string; }
export interface DecisionChain { id: string; engine_run_id: string; strategy_line: string; market: string; macro_snapshot_id?: string | null; sector_score_id?: string | null; candidate_score_id?: string | null; committee_id?: string | null; timing_signal_id?: string | null; formula_versions: string[]; status: string; }
export interface StrategyCommitteeRequest { market: "CN" | "HK"; symbol: string; company_name?: string; strategy_line: "value" | "emotion"; horizon: "long" | "short" | "swing"; signal_id: string; }
export interface CommitteeDecisionRequest { signal_id: string; strategy_line: "value" | "emotion"; status: "approve" | "reject" | "wait"; direction: "buy" | "sell" | "wait"; position_cap: number; entry_low?: number | null; entry_high?: number | null; stop_price?: number | null; target_low?: number | null; target_high?: number | null; holding_period: string; confidence: number; summary: string; review_triggers: string[]; evidence_ids: string[]; engine_run_ids: string[]; }
export interface StructuredCommitteeDecision extends CommitteeDecisionRequest { id: string; committee_id: string; decision_status: "approve" | "reject" | "wait"; created_at: string; }
export interface PaperAccount { id: string; name: string; strategy_line: "value" | "emotion"; horizon: "long" | "short" | "swing"; market: "CN" | "HK"; currency: "CNY" | "HKD"; initial_cash: number; cash: number; status: string; updated_at: string; }
export interface PaperOrder { id: string; account_id: string; signal_id: string; symbol: string; side: string; order_type: string; quantity: number; limit_price?: number | null; status: string; submitted_at: string; expires_at?: string | null; rejection_reason: string; }
export interface PaperPosition { account_id: string; symbol: string; quantity: number; average_cost: number; realized_pnl: number; updated_at: string; }
export interface PaperNav { account_id: string; currency: string; cash: number; market_value: number; nav: number; positions: PaperPosition[]; }
export interface PaperOrderRequest { signal_id: string; committee_id: string; decision_id: string; quantity: number; limit_price?: number | null; board_lot?: number | null; }

export interface ResearchRun { id: string; kind: string; market?: MarketCode | null; symbol?: string | null; status: string; message: string; started_at: string; completed_at?: string | null; }
export interface MarketSnapshot { id: string; market: MarketCode; as_of: string; status: string; summary: string; metrics: Record<string, string | number>; risks: string[]; source_status: SourceStatus; }
export interface MacroBrief { id: string; market: MarketCode; as_of: string; headline: string; stance: string; summary: string; themes: string[]; risks: string[]; source_status: SourceStatus; }
export interface SectorScore { id: string; market: MarketCode; taxonomy: string; sector_code: string; sector_name: string; as_of: string; momentum: number; earnings: number; fund_flow: number; breadth: number; valuation: number; risk: number; base_score: number; agent_adjustment: number; agent_reason: string; final_score: number; rank: number; source_status: SourceStatus; }
export interface SecurityCandidate { id: string; market: MarketCode; symbol: string; name: string; currency: string; exchange: string; taxonomy: string; sector_name: string; as_of: string; industry_position: number; growth: number; quality: number; valuation: number; momentum: number; liquidity: number; base_score: number; agent_adjustment: number; agent_reason: string; final_score: number; rank: number; excluded: boolean; exclusion_reason: string; source_status: SourceStatus; }
export interface CompanyDossier { id: string; market: MarketCode; symbol: string; name: string; currency: string; exchange: string; taxonomy: string; sector_name: string; overview: string; bull_thesis: string; bear_thesis: string; metrics: Record<string, string | number>; catalysts: string[]; risks: string[]; data_as_of: string; source_status: SourceStatus; updated_at: string; }
export interface ResearchReport { id: string; report_type: string; title: string; summary: string; content_md: string; market?: MarketCode | null; symbol?: string | null; data_as_of: string; source_kind: string; source_id?: string | null; source_status: SourceStatus; created_at: string; }
export interface CompanyResearchResult { run: ResearchRun; dossier: CompanyDossier; report?: ResearchReport | null; }
export interface DashboardMarket { market: MarketCode; meta: { currency: string; taxonomy: string; label: string }; snapshot?: MarketSnapshot | null; macro?: MacroBrief | null; sectors: SectorScore[]; candidates: SecurityCandidate[]; }
export interface DashboardToday { markets: DashboardMarket[]; reports: ResearchReport[]; research_runs: ResearchRun[]; generated_at: string; }

export type TdxModuleStatus = "never" | "running" | "ready" | "failed" | string;
export interface TdxModule {
  code: string;
  label: string;
  description: string;
  status: TdxModuleStatus;
  progress: number;
  total: number;
  item_count: number;
  message: string;
  error: string;
  metadata: Record<string, unknown>;
  started_at?: string | null;
  updated_at?: string | null;
}

function queryString(values?: object): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(values ?? {})) {
    if (value === undefined || value === null || value === "") continue;
    params.set(key, String(value));
  }
  return params.toString();
}
export interface TdxJob {
  id: string;
  module: string;
  status: string;
  progress: number;
  total: number;
  message: string;
  error: string;
  created_at: string;
  started_at?: string | null;
  completed_at?: string | null;
}
export interface TdxRefreshProfile {
  code: string;
  label: string;
  description: string;
  modules: string[];
}
export interface TdxDatasetSnapshot {
  id: number;
  snapshot_id: string;
  refresh_run_id: string;
  dataset: string;
  market: string;
  market_date: string;
  available_at: string;
  source: string;
  coverage?: number | null;
  item_count: number;
  expected_count: number;
  missing_count: number;
  status: string;
  error: string;
}
export interface TdxRefreshRun {
  id: string;
  profile: string;
  market: string;
  market_date: string;
  snapshot_id: string;
  modules: string[];
  status: string;
  progress: number;
  total: number;
  message: string;
  error: string;
  retry_count: number;
  created_at: string;
  started_at?: string | null;
  completed_at?: string | null;
  datasets?: TdxDatasetSnapshot[];
}
export interface TdxRefreshAutomation {
  market: string;
  enabled: boolean;
  last_profile: string;
  last_status: string;
  last_error: string;
  next_run_at?: string | null;
  updated_at?: string;
  running?: boolean;
  profiles?: Array<Omit<TdxRefreshProfile, "modules">>;
}
export interface TdxStatus {
  available: boolean;
  tdx_home: string;
  client_process_running: boolean;
  active_job?: TdxJob | null;
  modules: TdxModule[];
  recent_jobs: TdxJob[];
  active_snapshot_id?: string | null;
  active_close_snapshot?: TdxRefreshRun | null;
  refresh_lock?: { name: string; owner: string; acquired_at: string; expires_at: string } | null;
  recent_refresh_runs?: TdxRefreshRun[];
  refresh_profiles?: TdxRefreshProfile[];
  automation?: TdxRefreshAutomation;
  market_catalogs?: TdxMarketCatalog[];
}
export interface TdxMarketCatalog {
  market: "HK" | "US";
  label: string;
  list_id: string;
  securities: number;
  quotes: number;
  latest_refresh?: TdxRefreshRun | null;
}
export interface TdxMarketCatalogQuotes {
  market: "HK" | "US";
  label: string;
  total: number;
  as_of?: string | null;
  items: TdxQuote[];
}
export interface TdxRecord {
  dataset: string;
  key: string;
  category: string;
  name: string;
  payload: Record<string, unknown>;
  updated_at: string;
  snapshot_id?: string | null;
}
export interface TdxDataset { dataset: string; total: number; items: TdxRecord[]; }

export interface TdxQuote {
  code: string; name: string; price?: number | null; last_close?: number | null;
  change_pct?: number | null; volume_lots?: number | null; amount_10k?: number | null;
  data_as_of?: string; rank?: number; sectors?: Array<{ code: string; name: string }>;
  pe_ttm?: number | null; pe_dynamic?: number | null; pb_mrq?: number | null;
  dividend_yield?: number | null; turnover_rate?: number | null; market_cap_100m?: number | null;
  revenue_10k?: number | null; net_profit_10k?: number | null; eps?: number | null;
  [key: string]: unknown;
}
export interface TdxMarketOverview {
  source: string; as_of?: string | null; client_running: boolean; indices: TdxQuote[];
  breadth: { valid: number; up: number; down: number; flat: number; up_down_ratio?: number | null; median_change_pct?: number | null };
  distribution: Array<{ label: string; count: number }>;
  activity: TdxQuote[]; index_etfs: Array<Record<string, unknown>>; limit_up_preview: TdxQuote[];
  special_counts?: Record<string, number>;
}
export interface TdxRankResult {
  category: string; categories: string[]; total: number; limit: number; offset: number;
  items: TdxQuote[]; as_of?: string | null;
  coverage: { quotes: number; fundamentals: number; fundamental_pct: number };
}
export interface TdxSector extends TdxQuote {
  category: string; member_count: number; up: number; down: number; breadth_pct?: number | null; leader?: TdxQuote | null;
}
export interface TdxSectorResult { categories: string[]; total: number; items: TdxSector[]; as_of?: string | null; }
export interface TdxSectorDetail {
  sector: TdxSector; members: TdxQuote[]; member_count: number; up: number; down: number;
  leaders: TdxQuote[]; laggards: TdxQuote[]; etfs: Array<Record<string, unknown>>; as_of?: string | null;
}
export interface TdxScreenerFilters {
  query?: string; sector?: string; min_price?: number; max_price?: number; min_change?: number; max_change?: number;
  min_turnover?: number; max_pe?: number; max_pb?: number; min_dividend_yield?: number;
  min_market_cap?: number; min_revenue?: number; min_net_profit?: number; min_eps?: number;
  include_st?: boolean; include_quit?: boolean; include_bj?: boolean; is_hs300?: boolean; is_margin?: boolean; is_connect?: boolean;
  sort?: string; direction?: string; limit?: number; offset?: number;
}
export interface TdxScreenerResult { total: number; items: TdxQuote[]; limit: number; offset: number; coverage: { quotes: number; fundamentals: number }; as_of?: string | null; }
export interface TdxFundResult { category: string; categories: string[]; total: number; items: Array<Record<string, unknown>>; as_of?: string | null; }
export interface TdxSecuritySearchItem { code: string; name: string; quote?: TdxQuote | null; updated_at: string; }
export interface TdxSecurityOverview {
  code: string; name: string; quote?: TdxQuote | null; fundamental?: Record<string, unknown> | null;
  detail?: Record<string, unknown> | null; sectors: Array<Record<string, unknown>>;
  klines: Array<Record<string, unknown>>; professional_finance_available: boolean; source: string; as_of: string;
  cache?: { quote_updated_at?: string | null; fundamental_updated_at?: string | null; detail_updated_at?: string | null; stale: boolean };
}
export interface TdxFormulaScanRequest { formula_type: number; formula_code: string; formula_args?: string; universe?: string; period?: string; }
export interface TdxFormulaScan {
  id: string; formula_type: number; formula_code: string; formula_args: string; universe: string; period: string;
  status: string; progress: number; total: number; results: Array<TdxQuote & { signals?: Array<{ line: string; value: unknown; date?: string }> }>;
  message: string; error: string; created_at: string; completed_at?: string | null;
}

export interface CommitteeDecision { committee_id: string; direction: string; position_cap?: number | null; target_low?: number | null; target_high?: number | null; stop_price?: number | null; holding_period: string; confidence: number; review_triggers: string[]; evidence_date: string; summary: string; }
export interface CommitteeTask { id?: string; task_id?: string; agent_id?: string; status?: string; output?: string; result?: string; error?: string; }
export interface Committee { id: string; market: MarketCode; symbol: string; company_name: string; status: string; swarm_run_id?: string | null; created_at: string; completed_at?: string | null; decision?: CommitteeDecision | null; tasks?: CommitteeTask[]; final_report?: string | null; warning?: string; }
export type TradePlanStatus = "draft" | "active" | "triggered" | "closed" | "cancelled";
export interface TradePlan { id: string; committee_id?: string | null; market: MarketCode; symbol: string; name: string; status: TradePlanStatus; direction: string; position_cap?: number | null; entry_low?: number | null; entry_high?: number | null; target_low?: number | null; target_high?: number | null; stop_price?: number | null; triggers: string[]; notes: string; created_at: string; updated_at: string; }

export interface Portfolio { id: string; name: string; base_currency: string; benchmark: string; initial_cash: number; cash: Record<string, number>; position_count?: number; created_at: string; updated_at: string; }
export interface CreatePortfolioRequest { name: string; base_currency: string; benchmark?: string; initial_cash?: number; cash?: Record<string, number>; }
export interface PortfolioTransactionRequest { market: MarketCode; symbol: string; name: string; side: "buy" | "sell"; trade_date: string; quantity: number; price: number; fee?: number; currency: string; notes?: string; }
export interface PortfolioTransaction extends PortfolioTransactionRequest { id: string; portfolio_id: string; created_at: string; }
export interface PortfolioPosition { market: MarketCode; symbol: string; name: string; currency: string; quantity: number; cost: number; average_cost: number; realized_pnl: number; latest_price?: number | null; market_value?: number | null; unrealized_pnl?: number | null; }
export interface PortfolioAnalytics {
  portfolio: Portfolio;
  positions: PortfolioPosition[];
  subtotals: Record<string, number>;
  as_of: string;
  base_currency_total?: number | null;
  aggregate_available: boolean;
  aggregate_warning?: string | null;
  fx_evidence?: Array<Record<string, unknown>>;
  concentration: Array<{ symbol: string; currency: string; weight: number }>;
  risk_alerts: string[];
  sector_exposure?: Array<{ market: MarketCode; sector: string; currency: string; market_value: number }>;
  performance?: { total_return?: number | null; max_drawdown?: number | null };
  correlation?: { status: string; reason?: string };
  pending_trade_plans?: TradePlan[];
}

// --- Scheduled research types ---

export interface ScheduledRun {
  id: string;
  prompt: string;
  schedule: string;
  next_run_at: number;
  status: string;
  created_at: number;
  last_run_at: number | null;
  consecutive_failures: number;
  last_error: string | null;
  failure_kind: string | null;
  config: Record<string, unknown>;
  timezone: string | null;
}

export interface CreateScheduledRunRequest {
  id?: string;
  prompt: string;
  schedule: string;
  timezone?: string | null;
  config?: Record<string, unknown>;
}

// --- Swarm types ---

export interface SwarmPreset {
  name: string;
  title: string;
  description: string;
  agent_count: number;
  variables: { name: string; description: string; required: boolean }[];
}

export interface SwarmRunSummary {
  id: string;
  preset_name: string;
  status: string;
  created_at: string;
  task_count: number;
  completed_count: number;
}

export interface LLMProviderOption {
  name: string;
  label: string;
  api_key_env?: string | null;
  base_url_env: string;
  default_model: string;
  default_base_url: string;
  base_url_options?: string[];
  api_key_required: boolean;
  auth_type?: string;
  login_command?: string | null;
}

export interface LLMSettings {
  provider: string;
  model_name: string;
  base_url: string;
  api_key_env?: string | null;
  api_key_configured: boolean;
  api_key_hint?: string | null;
  api_key_required: boolean;
  temperature: number;
  timeout_seconds: number;
  max_retries: number;
  reasoning_effort: string;
  sse_timeout_seconds: number;
  env_path: string;
  providers: LLMProviderOption[];
}

export interface UpdateLLMSettingsRequest {
  provider: string;
  model_name: string;
  base_url: string;
  api_key?: string;
  clear_api_key?: boolean;
  temperature: number;
  timeout_seconds: number;
  max_retries: number;
  reasoning_effort?: string;
}

export interface ListLLMModelsRequest {
  provider: string;
  base_url?: string;
  api_key?: string;
}

export interface LLMModelsResponse {
  provider: string;
  models: string[];
  source: "provider" | "default";
  warning_code?:
    | "oauth_discovery_unsupported"
    | "api_key_required"
    | "model_list_unavailable"
    | null;
}

export interface DataSourceSettings {
  tushare_token_configured: boolean;
  tushare_token_hint?: string | null;
  baostock_supported: boolean;
  baostock_installed: boolean;
  baostock_message: string;
  env_path: string;
}

export interface UpdateDataSourceSettingsRequest {
  tushare_token?: string;
  clear_tushare_token?: boolean;
}

export interface ChannelAdapterStatus {
  name: string;
  display_name: string;
  configured: boolean;
  enabled: boolean;
  available: boolean;
  loaded: boolean;
  running: boolean;
  error?: string;
  install_hint?: string;
}

export interface ChannelRuntimeStatus {
  running: boolean;
  inbound_queue: number;
  outbound_queue: number;
  session_count: number;
  channels: Record<string, ChannelAdapterStatus>;
}

export interface FeishuChannelConfig {
  auto_start: boolean;
  enabled: boolean;
  app_id: string;
  app_secret_configured: boolean;
  domain: "feishu" | "lark";
  group_policy: "mention" | "open";
  reply_to_message: boolean;
  streaming: boolean;
  topic_isolation: boolean;
  default_agent: "general" | "financial_analyst";
  allow_from_count: number;
  config_path: string;
}

export interface FeishuChannelConfigUpdate {
  auto_start: boolean;
  enabled: boolean;
  app_id: string;
  app_secret?: string;
  clear_app_secret?: boolean;
  domain: "feishu" | "lark";
  group_policy: "mention" | "open";
  reply_to_message: boolean;
  streaming: boolean;
  topic_isolation: boolean;
  default_agent: "general" | "financial_analyst";
}

export interface FeishuChannelConfigUpdateResponse {
  config: FeishuChannelConfig;
  bot: { app_name: string; open_id: string } | null;
  runtime: ChannelRuntimeStatus;
}

export interface ChannelRuntimeActionResponse extends ChannelRuntimeStatus {
  status: string;
}

export interface ChannelPairingCommandRequest {
  channel: string;
  command: string;
}

export interface ChannelPairingCommandResponse {
  channel: string;
  reply: string;
}

// --- Types matching backend API contracts ---

export interface RunListItem {
  run_id: string;
  status: string;
  created_at: string;
  prompt?: string;
  total_return?: number;
  sharpe?: number;
  codes?: string[];
  start_date?: string;
  end_date?: string;
}

export interface RunDetailParams {
  chart_payload?: "summary";
  chart_symbol?: string;
}

export interface PriceBar {
  time: string;
  timestamp?: string;
  code?: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface CompactDailyBars {
  market: string;
  stock_code: string;
  adjustment_type: "front";
  bars: Array<{
    date: string;
    open: number;
    high: number;
    low: number;
    close: number;
    volume: number | null;
  }>;
  bar_count: number;
  returned_bar_count: number;
  coverage_status: "READY" | "PARTIAL" | "INSUFFICIENT";
  data_as_of: string | null;
  requested_as_of: string | null;
  source: string | null;
  source_version: string | null;
  fetched_at: string | null;
}

export interface TradeMarker {
  time: string;
  timestamp?: string;
  code?: string;
  side: "BUY" | "SELL";
  price: number;
  qty?: number;
  reason?: string;
  text?: string;
}

export interface EquityPoint {
  time: string;
  equity: string | number;
  drawdown: string | number;
}

/** Monte Carlo fan-chart payload: percentile envelope + sampled paths over trade order. */
export interface MonteCarloEquityPaths {
  steps: number[];
  initial_capital: number;
  actual: number[];
  band_p5: number[];
  band_p25: number[];
  band_p50: number[];
  band_p75: number[];
  band_p95: number[];
  samples: number[][];
}

export interface ValidationData {
  monte_carlo?: {
    actual_sharpe: number;
    actual_max_dd: number;
    p_value_sharpe: number;
    p_value_max_dd: number;
    simulated_sharpe_mean: number;
    simulated_sharpe_std: number;
    simulated_sharpe_p5: number;
    simulated_sharpe_p95: number;
    n_simulations: number;
    n_trades: number;
    sharpe_samples?: number[];
    equity_paths?: MonteCarloEquityPaths;
    error?: string;
  };
  bootstrap?: {
    observed_sharpe: number;
    ci_lower: number;
    ci_upper: number;
    median_sharpe: number;
    prob_positive: number;
    confidence: number;
    n_bootstrap: number;
    sharpe_samples?: number[];
    error?: string;
  };
  walk_forward?: {
    n_windows: number;
    windows: Array<{
      window: number;
      start: string;
      end: string;
      return: number;
      sharpe: number;
      max_dd: number;
      trades: number;
      win_rate: number;
    }>;
    profitable_windows: number;
    consistency_rate: number;
    return_mean: number;
    return_std: number;
    sharpe_mean: number;
    sharpe_std: number;
    error?: string;
  };
}

export interface RiskXRayPayload {
  inputs?: {
    symbols?: string[];
    weights?: Record<string, number>;
    aligned_days?: number;
    return_observations?: number;
    first_date?: string;
    last_date?: string;
  };
  concentration?: { hhi?: number; effective_n?: number; top_weight?: number };
  volatility?: { annualized_vol?: number };
  drawdown?: { max_drawdown?: number };
  tail_risk?: Record<string, unknown>;
  diversification?: Record<string, unknown>;
  correlation?: Record<string, unknown>;
  skipped?: string[];
  warnings?: string[];
}

export interface RebalanceNotesPayload {
  rebalances?: Array<{
    date: string;
    turnover: number;
    entries?: Array<{ code: string; to: number }>;
    exits?: Array<{ code: string; from: number }>;
    top_moves?: Array<{ code: string; from: number; to: number; delta: number }>;
  }>;
  summary?: {
    rebalance_count: number;
    turnover_total: number;
    turnover_mean: number;
    turnover_max: number;
    largest_rebalance_date?: string | null;
  };
}

export interface RunData {
  status: string;
  run_id: string;
  prompt?: string;
  elapsed_seconds?: number;
  run_directory?: string;
  run_stage?: string;
  run_context?: Record<string, unknown>;

  metrics?: BacktestMetrics;
  artifacts?: ArtifactInfo[];
  run_card?: RunCard;
  risk_xray?: RiskXRayPayload;
  rebalance_notes?: RebalanceNotesPayload;
  validation?: ValidationData;

  chart_symbols?: string[];
  price_series?: Record<string, PriceBar[]>;
  indicator_series?: Record<string, Record<string, IndicatorPoint[]>>;
  trade_markers?: TradeMarker[];
  equity_curve?: EquityPoint[];
  trade_log?: Array<Record<string, string>>;
  run_logs?: Array<{ source?: string; line_number?: number; message?: string }>;
}

export interface RunCard {
  schema_version?: string;
  generated_at?: string;
  run_dir?: string;
  backtest?: Record<string, unknown>;
  reproducibility?: Record<string, unknown>;
  data_sources?: string[];
  metrics?: Record<string, unknown>;
  validation?: unknown;
  warnings?: string[];
  artifacts?: RunCardArtifact[];
  [key: string]: unknown;
}

export interface RunCardArtifact {
  path: string;
  size_bytes: number;
  sha256: string;
}

export interface BacktestMetrics {
  final_value: number;
  total_return: number;
  annual_return: number;
  max_drawdown: number;
  sharpe: number;
  win_rate: number;
  trade_count: number;
  [key: string]: number;
}


export interface IndicatorPoint {
  time: string;
  value: number;
}

export interface ArtifactInfo {
  name: string;
  path: string;
  type: string;
  size: number;
  exists: boolean;
}

export interface PineScriptResult {
  exists: boolean;
  content: string | null;
}

export interface SessionItem {
  session_id: string;
  title?: string;
  status?: string;
  created_at?: string;
  updated_at?: string;
  last_attempt_id?: string;
}

// --- Goal types ---

export type GoalStatus =
  | "active"
  | "paused"
  | "waiting_user"
  | "needs_refresh"
  | "insufficient_evidence"
  | "compliance_blocked"
  | "blocked"
  | "budget_limited"
  | "usage_limited"
  | "complete"
  | "cancelled"
  | "superseded";

export type GoalRiskTier =
  | "research_general"
  | "market_specific_short_term"
  | "personalized_advice_or_position_sizing";

export interface GoalRecord {
  goal_id: string;
  session_id: string;
  status: GoalStatus;
  objective: string;
  ui_summary: string;
  source: string;
  protocol: string;
  risk_tier: GoalRiskTier;
  token_budget?: number | null;
  tokens_used: number;
  turn_budget?: number | null;
  turns_used: number;
  time_budget_seconds?: number | null;
  time_used_seconds: number;
  budget_wrapup_sent: boolean;
  created_at: string;
  updated_at: string;
  completed_at?: string | null;
  recap?: string | null;
}

export interface GoalClaim {
  claim_id: string;
  goal_id: string;
  session_id: string;
  claim_type: string;
  text: string;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface GoalCriterion {
  criterion_id: string;
  goal_id: string;
  session_id: string;
  text: string;
  required: boolean;
  status: string;
  freshness_requirement?: string | null;
  protocol_step?: string | null;
  created_at: string;
  updated_at: string;
}

export interface GoalEvidence {
  evidence_id: string;
  goal_id: string;
  session_id: string;
  text: string;
  criterion_id?: string | null;
  claim_id?: string | null;
  evidence_type: string;
  tool_call_id?: string | null;
  run_id?: string | null;
  source_provider?: string | null;
  source_type?: string | null;
  source_uri?: string | null;
  symbol_universe: string[];
  benchmark: string[];
  timeframe?: string | null;
  method?: string | null;
  assumptions: Record<string, unknown>;
  artifact_path?: string | null;
  artifact_hash?: string | null;
  retrieved_at: string;
  data_as_of?: string | null;
  freshness_status: string;
  verification_status: string;
  confidence?: string | null;
  caveat?: string | null;
  contradicts_claim_ids: string[];
  created_at: string;
}

export interface GoalSnapshot {
  goal: GoalRecord;
  claims: GoalClaim[];
  criteria: GoalCriterion[];
  evidence: GoalEvidence[];
  evidence_count: number;
}

export interface CreateGoalRequest {
  objective: string;
  criteria?: string[];
  ui_summary?: string;
  protocol?: string;
  risk_tier?: GoalRiskTier;
  token_budget?: number;
  turn_budget?: number;
  time_budget_seconds?: number;
}

export interface AddGoalEvidenceRequest {
  goal_id: string;
  expected_goal_id: string;
  text: string;
  criterion_id?: string | null;
  claim_id?: string | null;
  evidence_type?: string;
  tool_call_id?: string | null;
  run_id?: string | null;
  source_provider?: string | null;
  source_type?: string | null;
  source_uri?: string | null;
  symbol_universe?: string[];
  benchmark?: string[];
  timeframe?: string | null;
  method?: string | null;
  assumptions?: Record<string, unknown>;
  artifact_path?: string | null;
  artifact_hash?: string | null;
  data_as_of?: string | null;
  confidence?: string | null;
  caveat?: string | null;
  contradicts_claim_ids?: string[];
}

export interface UpdateGoalRequest {
  goal_id: string;
  expected_goal_id: string;
  objective?: string;
  ui_summary?: string;
}

export interface UpdateGoalResponse {
  goal: GoalRecord;
  snapshot: GoalSnapshot;
}

export interface AddGoalEvidenceResponse {
  evidence: GoalEvidence;
  snapshot: GoalSnapshot;
}

export interface GoalAuditRowRequest {
  criterion_id: string;
  result: string;
  evidence_ids?: string[];
  notes?: string;
}

export interface UpdateGoalStatusRequest {
  goal_id: string;
  expected_goal_id: string;
  status: GoalStatus;
  audit?: GoalAuditRowRequest[];
  recap?: string | null;
}

export interface UpdateGoalStatusResponse {
  goal: GoalRecord;
  snapshot: GoalSnapshot;
}

// --- Alpha Zoo types ---

export interface AlphaListParams {
  zoo?: string;
  theme?: string;
  universe?: string;
  limit?: number;
}

export interface AlphaSummary {
  id: string;
  zoo: string;
  theme: string[];
  universe: string[];
  nickname?: string;
  decay_horizon?: number | null;
  min_warmup_bars?: number | null;
  requires_sector?: boolean;
}

export interface AlphaListResponse {
  status: string;
  alphas: AlphaSummary[];
  total: number;
  returned: number;
  truncated: boolean;
}

export interface AlphaDetail {
  id: string;
  zoo: string;
  module_path?: string;
  meta: Record<string, unknown>;
}

export interface AlphaDetailResponse {
  status: string;
  alpha: AlphaDetail;
  source_code: string;
}

export interface AlphaBenchRequest {
  zoo: string;
  universe: string;
  period: string;
  top?: number;
}

export interface AlphaBenchTopRow {
  id: string;
  ic_mean: number;
  ir: number;
  theme: string[];
  formula_latex: string;
  category: "alive" | "reversed" | "dead";
}

export interface AlphaBenchResult {
  alive: number;
  reversed: number;
  dead: number;
  skipped?: number;
  top5_by_ir: AlphaBenchTopRow[];
  dead_examples: AlphaBenchTopRow[];
  by_theme: Record<string, { alive: number; reversed: number; dead: number }>;
}

export interface AlphaCompareRequest {
  alpha_ids: string[];
  universe: string;
  period: string;
  /** One of: ir | ic_mean | ic_positive_ratio | ic_count (default ir). */
  sort?: string;
}

export interface AlphaCompareRow {
  rank: number;
  id: string;
  zoo: string;
  ic_mean: number;
  ic_std: number;
  ir: number;
  ic_positive_ratio: number;
  ic_count: number;
  /** `delta_<sort>_vs_best` — gap to the top-ranked alpha on the active metric. */
  [deltaKey: string]: number | string;
}

export interface AlphaCompareSkip {
  id: string;
  reason: string;
}

export interface AlphaCompareResult {
  universe: string;
  period: string;
  sort: string;
  n_compared: number;
  n_skipped: number;
  winner: string;
  ranking: AlphaCompareRow[];
  skipped: AlphaCompareSkip[];
}

export interface MessageItem {
  message_id: string;
  session_id: string;
  role: string;
  content: string;
  created_at: string;
  linked_attempt_id?: string;
  metadata?: Record<string, unknown>;
  tool_trail?: ToolTrailItem[];
}

export interface ToolTrailItem {
  tool: string;
  status: "running" | "ok" | "error";
  arguments?: Record<string, string>;
  elapsed_ms?: number;
  preview?: string;
  call_id?: string;
  timestamp?: number;
}
