"""Research Task orchestration with bounded, auditable Multi-Agent V1 routing."""

from __future__ import annotations

import json
import re
import time
from typing import Any, Callable, Protocol
from urllib.parse import urlsplit

from src.providers.chat import ChatLLM
from src.research_tasks.providers import (
    resolve_agent_model_selection,
    safe_agent_model_settings,
    safe_provider_catalog,
    validate_provider_model,
)
from src.research_tasks.store import AGENT_ROLES, ResearchTaskStore

SPECIALIST_ROLES = ("macro_policy", "industry", "company", "valuation", "risk")
FINAL_FIELDS = (
    "task_id", "research_subject", "executive_summary", "consensus", "disagreements",
    "key_findings", "key_risks", "evidence_summary", "confidence", "unresolved_questions",
    "suggested_next_action", "thesis_change_suggestion", "dossier_update_suggestion",
)


def _structured_response_format(
    phase: str, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Return a provider-native strict schema for each orchestration phase."""
    context = dict((payload or {}).get("context_snapshot") or {})
    evidence_ids = sorted({
        str(item.get("id"))
        for item in context.get("evidence", [])
        if isinstance(item, dict) and item.get("id")
    })
    evidence_item: dict[str, Any] = {"type": "string"}
    if evidence_ids:
        evidence_item["enum"] = evidence_ids
    claim = {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": ["FACT", "INFERENCE", "OPINION", "UNKNOWN"]},
            "statement": {"type": "string", "maxLength": 80},
            "evidence_ids": {"type": "array", "items": evidence_item},
        },
        "required": ["type", "statement", "evidence_ids"],
        "additionalProperties": False,
    }
    if phase == "FINANCIAL_ANALYSIS":
        raise ValueError("financial analysis must supply its own target_schema")
    if phase in {"RESEARCH", "CROSS_REVIEW"}:
        schema = {
            "type": "object",
            "properties": {
                "claims": {"type": "array", "items": claim, "maxItems": 3},
                "summary": {"type": "string", "maxLength": 120},
                "confidence": {"type": "number"},
                "unknowns": {
                    "type": "array", "items": {"type": "string", "maxLength": 80}, "maxItems": 3
                },
            },
            "required": ["claims", "summary", "confidence", "unknowns"],
            "additionalProperties": False,
        }
    elif phase == "PLANNING":
        schema = {
            "type": "object",
            "properties": {
                "selected_agents": {
                    "type": "array",
                    "items": {"type": "string", "enum": list(SPECIALIST_ROLES)},
                    "maxItems": 4,
                },
                "routing_reason": {"type": "string"},
            },
            "required": ["selected_agents", "routing_reason"],
            "additionalProperties": False,
        }
    elif phase == "REVIEW_PLANNING":
        schema = {
            "type": "object",
            "properties": {
                "reviews": {
                    "type": "array",
                    "maxItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "agent_role": {"type": "string", "enum": list(SPECIALIST_ROLES)},
                            "question": {"type": "string"},
                        },
                        "required": ["agent_role", "question"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["reviews"],
            "additionalProperties": False,
        }
    else:
        list_fields = {
            key: {"type": "array", "items": {"type": "string", "maxLength": 80}, "maxItems": 1}
            for key in (
                "consensus", "disagreements", "key_findings", "key_risks",
                "evidence_summary", "unresolved_questions", "suggested_next_action",
            )
        }
        schema = {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "maxLength": 64},
                "research_subject": {"type": "string", "maxLength": 80},
                "executive_summary": {"type": "string", "maxLength": 160},
                **list_fields,
                "confidence": {"type": "number"},
                "thesis_change_suggestion": {"type": "string", "maxLength": 120},
                "dossier_update_suggestion": {"type": "string", "maxLength": 120},
            },
            "required": list(FINAL_FIELDS),
            "additionalProperties": False,
        }
    return {
        "type": "json_schema",
        "json_schema": {"name": f"research_task_{phase.lower()}", "strict": True, "schema": schema},
    }


class ModelRuntime(Protocol):
    def invoke(self, *, role: str, phase: str, provider: str, model: str,
               instruction: str, payload: dict[str, Any],
               target_schema: dict[str, Any] | None = None) -> dict[str, Any]: ...


def _parse_json(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        # Some OpenAI-compatible endpoints ignore response_format and let
        # the model wrap the object in prose; salvage the outermost object
        # instead of discarding a complete answer.
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise
        value = json.loads(text[start:end + 1])
    if not isinstance(value, dict):
        raise ValueError("model response must be a JSON object")
    return value


class ProviderModelRuntime:
    """Production adapter; all model calls go through the existing provider layer."""

    def invoke(self, *, role: str, phase: str, provider: str, model: str,
               instruction: str, payload: dict[str, Any],
               target_schema: dict[str, Any] | None = None,
               max_tokens: int | None = None,
               extra_body: dict[str, Any] | None = None) -> dict[str, Any]:
        client = ChatLLM(model_name=model, provider_name=provider, max_tokens=max_tokens, extra_body=extra_body)
        is_ollama = provider.strip().lower() == "ollama"
        if is_ollama:
            # Qwen-family local models may otherwise spend most of their CPU
            # budget on hidden reasoning. V1 needs concise auditable JSON.
            instruction = f"/no_think\n{instruction}"
        user_content = json.dumps(payload, ensure_ascii=False, default=str)
        if is_ollama:
            user_content = f"/no_think\n{user_content}"
        response = client.chat([
            {"role": "system", "content": instruction},
            {"role": "user", "content": user_content},
        ], response_format=target_schema or _structured_response_format(phase, payload))
        if not response.content:
            raise RuntimeError("empty model response")
        return _parse_json(response.content)

    def invoke_with_connection(self, *, role: str, phase: str, model: str,
                                base_url: str, api_key: str, instruction: str,
                                payload: dict[str, Any], target_schema: dict[str, Any] | None = None,
                                max_tokens: int | None = None,
                                extra_body: dict[str, Any] | None = None) -> dict[str, Any]:
        client = ChatLLM(
            model_name=model,
            provider_name="openai",
            base_url=base_url,
            api_key=api_key,
            max_tokens=max_tokens,
            extra_body=extra_body,
        )
        response = client.chat([
            {"role": "system", "content": instruction},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
        ], response_format=target_schema or _structured_response_format(phase, payload))
        if not response.content:
            raise RuntimeError("empty model response")
        return _parse_json(response.content)


def _validated_connection_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlsplit(normalized)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None):
        raise ValueError("模型 URL 必须是有效的 HTTP(S) 地址，且不能包含账号或密码")
    return normalized


def _compact_sector(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return row
    keys = (
        "sector_code", "sector_name", "score", "rank", "coverage", "confidence", "status",
        "member_coverage", "component_scores", "raw_features", "macro_fit", "macro_stance",
        "macro_drivers", "policy_fit", "missing_fields", "formula_version", "data_as_of", "sources",
    )
    return {key: row.get(key) for key in keys if key in row}


def _compact_leader(row: dict[str, Any]) -> dict[str, Any]:
    raw = dict(row.get("raw_features") or {})
    raw_keys = (
        "market_cap", "revenue", "net_profit", "roe", "gross_margin", "net_margin",
        "revenue_cagr", "profit_cagr", "cash_conversion", "ocf_margin", "ocf_trend",
        "pe", "pb", "dividend_yield", "debt_safety", "shareholder_stability", "low_beta",
    )
    return {
        "symbol": row.get("symbol"), "name": row.get("name"),
        "sector_code": row.get("sector_code"), "sector_name": row.get("sector_name"),
        "score": row.get("score"), "rank": row.get("rank"), "candidate_sector_rank": row.get("candidate_sector_rank"),
        "coverage": row.get("coverage"), "confidence": row.get("confidence"), "status": row.get("status"),
        "raw_features": {key: raw.get(key) for key in raw_keys if key in raw},
        "component_scores": row.get("component_scores"), "missing_fields": row.get("missing_fields"),
        "formula_version": row.get("formula_version"), "data_as_of": row.get("data_as_of"),
        "sources": row.get("sources"),
    }


def default_context_loader(task: dict[str, Any]) -> dict[str, Any]:
    """Build one immutable context snapshot; agents do not fetch independently."""
    context: dict[str, Any] = {"task": {
        "scope_type": task["scope_type"], "scope_id": task["scope_id"],
        "title": task["title"], "question": task["question"],
    }, "evidence": [], "data_gaps": []}
    try:
        from src.strategy_engines.value_line import get_value_line_service
        value_line = get_value_line_service()
        context["macro"] = value_line.macro()
        if task["scope_type"] == "INDUSTRY":
            sectors = value_line.sectors().get("items", [])
            sector = next((row for row in sectors if str(row.get("sector_code")) == task["scope_id"]), None)
            context["sector"] = _compact_sector(sector)
            context["leaders"] = [
                _compact_leader(row) for row in value_line.leaders(task["scope_id"]).get("items", [])[:20]
            ]
        else:
            leaders = value_line.leaders().get("items", [])
            leader_records = [row for row in leaders if str(row.get("symbol")) == task["scope_id"]][:10]
            context["leader_records"] = [_compact_leader(row) for row in leader_records]
            if leader_records:
                sector_code = str(leader_records[0].get("sector_code") or "")
                sectors = value_line.sectors().get("items", [])
                context["sector"] = _compact_sector(next(
                    (row for row in sectors if str(row.get("sector_code")) == sector_code),
                    {"sector_code": sector_code, "sector_name": leader_records[0].get("sector_name")},
                ))
                raw = dict(leader_records[0].get("raw_features") or {})
                context["valuation_context"] = {
                    "source": "Leader Score Python result",
                    "data_as_of": leader_records[0].get("data_as_of"),
                    "pe": raw.get("pe"), "pb": raw.get("pb"),
                    "dividend_yield": raw.get("dividend_yield"),
                    "valuation_component_score": dict(leader_records[0].get("component_scores") or {}).get("valuation"),
                    "note": "用于解释已有计算结果，不是独立合理价值或 DCF 结论",
                }
    except Exception as exc:
        context["data_gaps"].append(f"value_line:{type(exc).__name__}")
    try:
        from src.tdx_data.service import get_tdx_service
        tdx = get_tdx_service()
        if task["scope_type"] == "INDUSTRY":
            context["tdx_scope"] = tdx.sector_detail(task["scope_id"])
        else:
            overview = tdx.security_overview(task["scope_id"]) or {}
            quote = {key: value for key, value in dict(overview.get("quote") or {}).items() if key != "raw"}
            fundamental = {
                key: value for key, value in dict(overview.get("fundamental") or {}).items()
                if key not in {"raw", "base_raw"}
            }
            context["tdx_scope"] = {
                "code": overview.get("code"), "name": overview.get("name"),
                "quote": quote, "fundamental": fundamental,
                "detail": overview.get("detail"),
                "sectors": [row for row in (overview.get("sectors") or []) if str(row.get("sector_code") or "").startswith("881")],
                "klines": list(overview.get("klines") or [])[-20:],
                "professional_finance_available": overview.get("professional_finance_available"),
                "source": overview.get("source"), "as_of": overview.get("as_of"),
                "cache": overview.get("cache"),
            }
    except Exception as exc:
        context["data_gaps"].append(f"tdx:{type(exc).__name__}")
    for key in ("macro", "sector", "leaders", "leader_records", "valuation_context", "tdx_scope"):
        value = context.get(key)
        if value is None or value == [] or value == {}:
            continue
        published_at = str((value.get("as_of") if isinstance(value, dict) else "") or "")
        context["evidence"].append({
            "id": f"workspace:{key}:{task['scope_id']}",
            "source": "Value Workspace" if key != "tdx_scope" else "TDX",
            "published_at": published_at,
            "excerpt": json.dumps(value, ensure_ascii=False, default=str)[:800],
            "related_object": task["scope_id"],
        })
    return context


ROLE_INSTRUCTIONS = {
    "macro_policy": "分析宏观、流动性与政策传导，不越权给公司估值。",
    "industry": "分析产业结构、竞争、景气与关键变量。",
    "company": "分析公司经营、财务质量、治理和业务驱动。",
    "valuation": "仅在有估值依据时分析估值区间和关键假设；缺数据必须 UNKNOWN。",
    "risk": "只提出有证据链的风险、反证与触发条件，不做泛化风险清单。",
}

SPECIALIST_SCHEMA = """仅返回紧凑 JSON：{"claims":[{"type":"FACT|INFERENCE|OPINION|UNKNOWN","statement":"...","evidence_ids":["..."]}],"summary":"...","confidence":0.0,"unknowns":["..."]}。claims 最多3条、每条 statement 最多80字、summary 最多120字、unknowns 最多3条。FACT 必须使用上下文中已有 evidence id；禁止给出确定性买卖指令。"""


class ResearchTaskService:
    def __init__(self, store: ResearchTaskStore | None = None, runtime: ModelRuntime | None = None,
                 context_loader: Callable[[dict[str, Any]], dict[str, Any]] | None = None) -> None:
        self.store = store or ResearchTaskStore()
        self.runtime = runtime or ProviderModelRuntime()
        self.context_loader = context_loader or default_context_loader

    def get_configs(self) -> list[dict[str, Any]]:
        return self.store.list_configs()

    def get_providers(self) -> list[dict[str, Any]]:
        return safe_provider_catalog(self.store.list_configs())

    def get_model_settings(self) -> list[dict[str, Any]]:
        return safe_agent_model_settings(self.store.list_configs())

    def get_connection_settings(self) -> list[dict[str, Any]]:
        return [{
            "role": item["role"],
            "base_url": item.get("base_url") or "",
            "model": item.get("model") or "",
            "api_key_configured": bool(item.get("api_key_configured")),
            "enabled": bool(item["enabled"]),
            "ready": bool(item["enabled"] and item.get("base_url") and item.get("model")),
            "updated_at": item["updated_at"],
        } for item in self.store.list_configs()]

    def update_connection_setting(self, role: str, *, base_url: str, model: str,
                                  api_key: str | None, clear_api_key: bool,
                                  enabled: bool) -> dict[str, Any]:
        if role not in AGENT_ROLES:
            raise ValueError("unknown agent role")
        normalized_url = _validated_connection_url(base_url) if base_url.strip() else ""
        if enabled and (not normalized_url or not model.strip()):
            raise ValueError("启用研究员前必须填写模型 URL 和模型名称")
        self.store.update_connection(
            role,
            base_url=normalized_url,
            model=model,
            api_key=api_key,
            clear_api_key=clear_api_key,
            enabled=enabled,
        )
        return next(item for item in self.get_connection_settings() if item["role"] == role)

    def update_config(self, role: str, *, provider: str, model: str, enabled: bool) -> dict[str, Any]:
        if role not in AGENT_ROLES:
            raise ValueError("unknown agent role")
        validate_provider_model(provider.strip().lower(), model.strip(), self.store.list_configs())
        return self.store.update_config(role, provider, model, enabled)

    def update_model_setting(self, role: str, *, model_id: str, enabled: bool) -> dict[str, Any]:
        if role not in AGENT_ROLES:
            raise ValueError("unknown agent role")
        configs = self.store.list_configs()
        provider, model = resolve_agent_model_selection(role, model_id, configs)
        validate_provider_model(provider, model, configs)
        self.store.update_config(role, provider, model, enabled)
        return next(item for item in self.get_model_settings() if item["role"] == role)

    def create_task(self, **payload: Any) -> dict[str, Any]:
        return self.store.create_task(**payload)

    def _invoke(self, task_id: str, *, role: str, phase: str, instruction: str,
                payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
        cfg = self.store.get_runtime_config(role)
        participant = self.store.create_participant(
            task_id, role=role, phase=phase, provider=cfg["provider"], model=cfg["model"], instruction=instruction,
        )
        if not cfg["enabled"]:
            return None, "agent disabled"
        started = time.perf_counter()
        self.store.update_participant(participant["id"], "RUNNING")
        try:
            connection_invoke = getattr(self.runtime, "invoke_with_connection", None)
            if cfg.get("base_url") and callable(connection_invoke):
                output = connection_invoke(
                    role=role, phase=phase, model=cfg["model"], base_url=cfg["base_url"],
                    api_key=cfg.get("api_key") or "", instruction=instruction, payload=payload,
                )
            else:
                output = self.runtime.invoke(
                    role=role, phase=phase, provider=cfg["provider"], model=cfg["model"],
                    instruction=instruction, payload=payload,
                )
            elapsed = int((time.perf_counter() - started) * 1000)
            self.store.update_participant(participant["id"], "COMPLETED", output=output, duration_ms=elapsed)
            return output, None
        except Exception as exc:
            elapsed = int((time.perf_counter() - started) * 1000)
            error = f"{type(exc).__name__}: {exc}"
            self.store.update_participant(participant["id"], "FAILED", error=error, duration_ms=elapsed)
            return None, error

    @staticmethod
    def _allowed_roles(task: dict[str, Any], context: dict[str, Any]) -> list[str]:
        if task["scope_type"] == "INDUSTRY":
            roles = ["industry", "risk"]
            if re.search(r"宏观|利率|流动性|政策|通胀|经济周期", task["question"]):
                roles.insert(0, "macro_policy")
        else:
            roles = ["company", "industry", "risk"]
            scope = context.get("tdx_scope") or {}
            # Keep compatibility with older compact TDX snapshots whose
            # valuation fields lived directly on tdx_scope.
            fundamental = dict(scope.get("fundamental") or scope)
            valuation = dict(context.get("valuation_context") or {})
            if any(value is not None for value in (
                fundamental.get("pe"), fundamental.get("pb"), fundamental.get("pe_ttm"),
                fundamental.get("pe_dynamic"), fundamental.get("pb_mrq"),
                valuation.get("pe"), valuation.get("pb"), valuation.get("valuation_component_score"),
            )):
                roles.insert(2, "valuation")
        return roles[:4]

    @staticmethod
    def _validate_specialist(output: dict[str, Any], evidence_ids: set[str] | None = None) -> None:
        claims = output.get("claims")
        if not isinstance(claims, list) or not isinstance(output.get("summary"), str):
            raise ValueError("invalid specialist output schema")
        for claim in claims:
            if claim.get("type") not in {"FACT", "INFERENCE", "OPINION", "UNKNOWN"}:
                raise ValueError("invalid claim type")
            if claim["type"] == "FACT" and not claim.get("evidence_ids"):
                raise ValueError("FACT requires evidence_ids")
            if claim["type"] == "FACT" and evidence_ids is not None:
                unknown = set(claim.get("evidence_ids") or []) - evidence_ids
                if unknown:
                    raise ValueError(f"FACT references unknown evidence: {sorted(unknown)}")

    def run_task(self, task_id: str) -> dict[str, Any]:
        task = self.store.get_task(task_id)
        if task["status"] != "CREATED":
            raise ValueError("only CREATED tasks can run")
        lead = self.store.get_runtime_config("research_lead")
        if not lead["enabled"]:
            return self.store.set_task_status(task_id, "BLOCKED", error="research_lead disabled")
        legacy_ready = next((p for p in self.get_providers()
                             if p["provider"] == lead["provider"] and p["configured"]), None)
        if not (lead.get("base_url") and lead.get("model")) and not legacy_ready:
            return self.store.set_task_status(task_id, "BLOCKED", error="BLOCKED_BY_CONFIGURATION")

        self.store.set_task_status(task_id, "RESEARCHING")
        context = self.context_loader(task)
        evidence_ids = {str(item.get("id")) for item in context.get("evidence", []) if item.get("id")}
        allowed = self._allowed_roles(task, context)
        enabled = [role for role in allowed if self.store.get_config(role)["enabled"]]
        planning_context = {
            "data_gaps": context.get("data_gaps", []),
            "available_sections": [
                key for key in ("macro", "sector", "leaders", "leader_records", "valuation_context", "tdx_scope")
                if context.get(key) not in (None, {}, [])
            ],
            "evidence_ids": sorted(evidence_ids),
            "sector": {
                key: (context.get("sector") or {}).get(key)
                for key in ("sector_code", "sector_name", "score", "rank", "data_as_of")
            },
            "valuation_available": bool(context.get("valuation_context")),
        }
        planning, error = self._invoke(
            task_id, role="research_lead", phase="PLANNING",
            instruction="你是研究负责人，只做任务分工。基于范围从 allowed_roles 中选择最多4个角色，不自行完成专业分析。仅返回 JSON：{\"selected_agents\":[\"role\"],\"routing_reason\":\"...\"}。",
            payload={"task": task, "context_summary": planning_context, "allowed_roles": enabled},
        )
        if error or not planning:
            return self.store.set_task_status(task_id, "BLOCKED", error=error or "lead planning failed")
        selected = [role for role in planning.get("selected_agents", []) if role in enabled][:4]
        if not selected:
            return self.store.set_task_status(task_id, "BLOCKED", error="lead selected no enabled specialists")
        self.store.set_selected_agents(task_id, selected)

        outputs: dict[str, Any] = {}
        failures: dict[str, str] = {}
        for role in selected:
            output, specialist_error = self._invoke(
                task_id, role=role, phase="RESEARCH",
                instruction=f"{ROLE_INSTRUCTIONS[role]} {SPECIALIST_SCHEMA}",
                payload={"task": task, "context_snapshot": context},
            )
            if output:
                try:
                    self._validate_specialist(output, evidence_ids)
                    outputs[role] = output
                except ValueError as exc:
                    failures[role] = str(exc)
                    latest = self.store.list_participants(task_id)[-1]
                    self.store.update_participant(
                        latest["id"], "FAILED", output=output,
                        error=str(exc), duration_ms=latest["duration_ms"],
                    )
            else:
                failures[role] = specialist_error or "unknown failure"
        if not outputs:
            return self.store.set_task_status(task_id, "FAILED", error="all specialists failed")

        self.store.set_task_status(task_id, "REVIEWING")
        review_plan, review_error = self._invoke(
            task_id, role="research_lead", phase="REVIEW_PLANNING",
            instruction="识别专业输出中的关键冲突或证据缺口。最多安排一轮复核。仅返回 JSON：{\"reviews\":[{\"agent_role\":\"...\",\"question\":\"...\"}]}；无必要则 reviews 为空。",
            payload={"task": task, "specialist_outputs": outputs, "failures": failures},
        )
        reviews: dict[str, Any] = {}
        review_count = 0
        if review_plan and not review_error:
            requested = review_plan.get("reviews", [])
            if isinstance(requested, list) and requested:
                for request in requested[:4]:
                    role = request.get("agent_role")
                    if role not in outputs:
                        continue
                    output, _ = self._invoke(
                        task_id, role=role, phase="CROSS_REVIEW",
                        instruction=f"这是唯一一轮交叉复核。回答指定冲突，不扩展新任务。{SPECIALIST_SCHEMA}",
                        payload={"question": request.get("question"), "own_initial_output": outputs[role],
                                 "peer_outputs": {k: v for k, v in outputs.items() if k != role}, "context_snapshot": context},
                    )
                    review_count = 1
                    if output:
                        try:
                            self._validate_specialist(output, evidence_ids)
                            reviews[role] = output
                        except ValueError as exc:
                            latest = self.store.list_participants(task_id)[-1]
                            self.store.update_participant(
                                latest["id"], "FAILED", output=output,
                                error=str(exc), duration_ms=latest["duration_ms"],
                            )

        final, final_error = self._invoke(
            task_id, role="research_lead", phase="FINAL",
            instruction=("综合专业输出，明确共识、分歧、证据和未知项。禁止确定性买卖指令；只能建议后续研究动作。"
                         "thesis_change_suggestion 与 dossier_update_suggestion 只能是建议，不得声称已经更新。"
                         "结果保持紧凑：各列表最多1项，每项最多80字，executive_summary最多160字。"
                         f"仅返回 JSON，必须包含字段：{', '.join(FINAL_FIELDS)}。"),
            payload={"task": task, "specialist_outputs": outputs, "cross_reviews": reviews,
                     "failed_agents": failures, "review_count": review_count},
        )
        if final_error or not final:
            return self.store.set_task_status(task_id, "FAILED", error=final_error or "lead final failed")
        missing = [field for field in FINAL_FIELDS if field not in final]
        if missing:
            return self.store.set_task_status(task_id, "FAILED", error=f"final schema missing: {','.join(missing)}")
        actions = json.dumps(final.get("suggested_next_action"), ensure_ascii=False)
        if re.search(r"买入|卖出|加仓|减仓|止损|仓位", actions):
            return self.store.set_task_status(task_id, "FAILED", error="final result contains prohibited trading action")
        final["task_id"] = task_id
        return self.store.set_result(task_id, final, review_count)

    def get_task(self, task_id: str) -> dict[str, Any]:
        return self.store.get_task(task_id)

    def list_tasks(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.store.list_tasks(limit)

    def get_participants(self, task_id: str) -> list[dict[str, Any]]:
        self.store.get_task(task_id)
        return self.store.list_participants(task_id)
