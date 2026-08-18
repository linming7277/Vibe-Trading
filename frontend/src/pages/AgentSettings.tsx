import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Check, Loader2, Save, Users } from "lucide-react";
import { api, type ResearchAgentConnectionSetting, type ResearchAgentRole } from "@/lib/api";

const ROLE_LABELS: Record<ResearchAgentRole, string> = {
  research_lead: "投研主管",
  macro_policy: "宏观政策研究员",
  valuation: "估值研究员",
  risk: "风险研究员",
  financial_analyst: "财报研究员",
};

const ROLE_DESCRIPTIONS: Record<ResearchAgentRole, string> = {
  research_lead: "汇总各研究结论，组织分歧复核和最终决策链。",
  macro_policy: "分析宏观周期、政策方向和市场流动性。",
  valuation: "计算估值区间、安全边际和情景假设。",
  risk: "独立检查财务、市场和投资逻辑风险。",
  financial_analyst: "解读财报历史事实，并生成可追溯的预测情景。",
};

const CORE_ROLES: ResearchAgentRole[] = [
  "financial_analyst", "research_lead", "macro_policy", "valuation", "risk",
];
const fieldClass = "w-full rounded-lg border border-border bg-background px-3 py-2.5 text-sm outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/20 disabled:cursor-not-allowed disabled:opacity-60";

interface ConnectionDraft extends ResearchAgentConnectionSetting {
  api_key: string;
  clear_api_key: boolean;
}

export function AgentSettings() {
  const [rows, setRows] = useState<ConnectionDraft[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState<ResearchAgentRole | null>(null);
  const [saved, setSaved] = useState<ResearchAgentRole | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api.getResearchAgentConnections()
      .then((result) => setRows(result.items.map((item) => ({ ...item, api_key: "", clear_api_key: false }))))
      .catch((reason) => setError(reason instanceof Error ? reason.message : String(reason)))
      .finally(() => setLoading(false));
  }, []);

  const summary = useMemo(() => ({
    total: rows.length,
    enabled: rows.filter((row) => row.enabled).length,
    needsAttention: rows.filter((row) => row.enabled && !row.ready).length,
  }), [rows]);

  function patchRow(role: ResearchAgentRole, value: Partial<ConnectionDraft>) {
    setRows((items) => items.map((item) => item.role === role ? { ...item, ...value } : item));
    setSaved(null);
  }

  async function save(row: ConnectionDraft) {
    setSaving(row.role);
    setSaved(null);
    setError("");
    try {
      const next = await api.updateResearchAgentConnection(row.role, {
        base_url: row.base_url.trim(),
        model: row.model.trim(),
        api_key: row.api_key.trim() || undefined,
        clear_api_key: row.clear_api_key,
        enabled: row.enabled,
      });
      setRows((items) => items.map((item) => item.role === row.role
        ? { ...next, api_key: "", clear_api_key: false }
        : item));
      setSaved(row.role);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSaving(null);
    }
  }

  function renderRow(row: ConnectionDraft) {
    const draftReady = Boolean(row.enabled && row.base_url.trim() && row.model.trim());
    return <tr key={row.role} className="border-t border-border/70 align-middle transition hover:bg-muted/20">
      <td className="px-3 py-4">
        <div className="font-medium">{ROLE_LABELS[row.role]}</div>
        <p className="mt-1 max-w-md text-xs leading-5 text-muted-foreground">{ROLE_DESCRIPTIONS[row.role]}</p>
        <div className="mt-2 flex items-center gap-2 text-xs text-muted-foreground">
          <span className={`h-2 w-2 shrink-0 rounded-full ${draftReady ? "bg-success" : row.enabled ? "bg-warning" : "bg-muted-foreground/50"}`} />
          {!row.enabled ? "当前停用" : draftReady ? "连接信息完整" : "缺少 URL 或模型"}
        </div>
      </td>
      <td className="px-3 py-4">
        <input
          aria-label={`${ROLE_LABELS[row.role]} URL`}
          className={`${fieldClass} min-w-[210px]`}
          value={row.base_url}
          onChange={(event) => patchRow(row.role, { base_url: event.target.value })}
          placeholder="https://example.com/v1"
        />
      </td>
      <td className="px-3 py-4">
        <input
          aria-label={`${ROLE_LABELS[row.role]}模型`}
          className={`${fieldClass} min-w-[145px]`}
          value={row.model}
          onChange={(event) => patchRow(row.role, { model: event.target.value })}
          placeholder="模型名称"
        />
      </td>
      <td className="px-3 py-4">
        <input
          aria-label={`${ROLE_LABELS[row.role]} API Key`}
          type="password"
          autoComplete="new-password"
          className={`${fieldClass} min-w-[175px]`}
          value={row.api_key}
          onChange={(event) => patchRow(row.role, { api_key: event.target.value, clear_api_key: false })}
          placeholder={row.api_key_configured ? "已配置，留空保持不变" : "输入 API Key（可留空）"}
        />
        {row.api_key_configured && <label className="mt-2 inline-flex cursor-pointer items-center gap-1.5 text-xs text-muted-foreground">
          <input
            aria-label={`${ROLE_LABELS[row.role]}清除 API Key`}
            type="checkbox"
            checked={row.clear_api_key}
            onChange={(event) => patchRow(row.role, { clear_api_key: event.target.checked, api_key: "" })}
          />
          清除已保存密钥
        </label>}
      </td>
      <td className="px-3 py-4">
        <label className="inline-flex min-w-[86px] cursor-pointer items-center gap-2 text-xs">
          <input
            aria-label={`${ROLE_LABELS[row.role]}启用状态`}
            type="checkbox"
            checked={row.enabled}
            onChange={(event) => patchRow(row.role, { enabled: event.target.checked })}
          />
          {row.enabled ? "已启用" : "已停用"}
        </label>
      </td>
      <td className="px-3 py-4 text-right">
        <button
          type="button"
          aria-label={`保存${ROLE_LABELS[row.role]}`}
          onClick={() => void save(row)}
          disabled={saving === row.role || (row.enabled && (!row.base_url.trim() || !row.model.trim()))}
          className="inline-flex min-w-[76px] shrink-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"
        >
          {saving === row.role ? <Loader2 className="h-4 w-4 animate-spin" /> : saved === row.role ? <Check className="h-4 w-4" /> : <Save className="h-4 w-4" />}
          {saved === row.role ? "已保存" : "保存"}
        </button>
      </td>
    </tr>;
  }

  if (loading) {
    return <div className="flex h-[50vh] items-center justify-center text-muted-foreground"><Loader2 className="mr-2 h-4 w-4 animate-spin" />加载研究员设置…</div>;
  }

  return <div className="mx-auto max-w-6xl space-y-6 p-6">
    <header className="flex flex-wrap items-start justify-between gap-4">
      <div>
        <div className="flex items-center gap-2"><Users className="h-5 w-5 text-primary" /><h1 className="text-xl font-semibold">研究员设置</h1></div>
      </div>
      <div className="flex flex-wrap gap-2 text-xs">
        <span className="rounded-full border border-border bg-card px-3 py-1.5">研究员 {summary.total}</span>
        <span className="rounded-full border border-success/30 bg-success/10 px-3 py-1.5 text-success">已启用 {summary.enabled}</span>
        {summary.needsAttention > 0 && <span className="rounded-full border border-warning/30 bg-warning/10 px-3 py-1.5 text-warning">待处理 {summary.needsAttention}</span>}
      </div>
    </header>

    {error && <div className="flex items-start gap-2 rounded-lg border border-danger/30 bg-danger/10 px-4 py-3 text-sm text-danger"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />{error}</div>}

    <section className="overflow-hidden rounded-lg border border-border bg-card">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[1000px] text-left">
          <thead className="bg-muted/40 text-xs text-muted-foreground">
            <tr>
              <th className="w-[22%] px-3 py-3 font-medium">研究员与职责</th>
              <th className="px-3 py-3 font-medium">模型 URL</th>
              <th className="px-3 py-3 font-medium">模型名称</th>
              <th className="px-3 py-3 font-medium">API Key</th>
              <th className="px-3 py-3 font-medium">启用</th>
              <th className="px-3 py-3 text-right font-medium">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr className="border-t border-border bg-primary/[0.035]">
              <td colSpan={6} className="px-3 py-2.5">
                <span className="text-sm font-medium">核心研究团队</span>
                <span className="ml-3 text-xs text-muted-foreground">财报、宏观、估值与风险研究链</span>
              </td>
            </tr>
            {rows.filter((row) => CORE_ROLES.includes(row.role)).sort((a, b) => CORE_ROLES.indexOf(a.role) - CORE_ROLES.indexOf(b.role)).map(renderRow)}
          </tbody>
        </table>
      </div>
    </section>

    <div className="rounded-lg border border-border bg-muted/30 px-4 py-3 text-xs leading-5 text-muted-foreground">
      API Key 只写入本机研究数据库，页面和查询接口只显示是否已配置，不会返回密钥原文。留空保存会保留已有密钥。
    </div>
  </div>;
}
