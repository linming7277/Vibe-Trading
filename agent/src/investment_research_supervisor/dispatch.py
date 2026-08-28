"""In-process researcher dispatch for the investment research supervisor.

Feishu never delivers one bot's messages to another bot, so the supervisor
cannot literally @-mention a researcher bot and wait for its event.  Instead
the supervisor plans a dispatch inside this process, runs the researchers in
parallel, and the channel runtime presents each answer through that
researcher's own Feishu bot instance (plus @-mention visuals in the group).

All researchers stay read-only: they reuse the same services that answer
direct mentions, so no new research is started and nothing is mutated.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable

# Researcher agents the supervisor may delegate to, mapped to the Feishu
# channel (bot instance) that presents their answers in the group.
RESEARCHER_CHANNELS: dict[str, str] = {
    "financial_analyst": "feishu",
    "risk_researcher": "feishu_risk",
    "valuation_researcher": "feishu_valuation",
    "macro_policy_researcher": "feishu_macro_policy",
}

RESEARCHER_TITLES: dict[str, str] = {
    "financial_analyst": "财报研究员",
    "risk_researcher": "风险研究员",
    "valuation_researcher": "估值研究员",
    "macro_policy_researcher": "宏观政策研究员",
}

# Role-specific assignment phrasing: shown on the dispatch card and sent to
# the researcher as-is, so the group sees differentiated, duty-aligned tasks
# instead of the same raw question repeated three times.  Each template is
# prefixed with the original question (kept verbatim for company resolution).
RESEARCHER_ASSIGNMENT_TEMPLATES: dict[str, str] = {
    "financial_analyst": "梳理近五年营收与归母净利路径、毛利率趋势、最新财报期变化，并验证经营现金流对净利润的覆盖质量",
    "risk_researcher": "排查财务与经营风险、低估陷阱风险与公司核心逻辑状态，给出可观察的证伪阈值与检查频率",
    "valuation_researcher": "评估当前估值位置：PE/PB 与系统估算合理价值区间对照、历史峰值净利对应估值，并指出关键估值验证点",
    "macro_policy_researcher": "分析当前宏观环境的主要传导路径与需跟踪的政策变量",
}


def _assignment(researcher: str, question: str) -> str:
    template = RESEARCHER_ASSIGNMENT_TEMPLATES.get(researcher, "")
    if not template:
        return question
    return f"围绕「{question}」，{template}"

# Per-researcher wall clock budget; a slow model must not block the summary.
DISPATCH_TASK_TIMEOUT_S = 150.0

_MACRO_PATTERN = re.compile(
    r"宏观|货币|流动性|通胀|CPI|PPI|利率|降息|加息|社融|信贷|PMI|汇率|关税|财政|"
    r"经济数据|政策传导|行业政策|政策(?:环境|影响|变化)",
    re.I,
)
_TRADING_LANGUAGE = re.compile(r"买入|卖出|推荐|仓位|止盈|止损|加仓|减仓")

# The supervisor answers every intent from its own read-only snapshot
# registry — including single-domain FINANCIAL/VALUATION/RISK questions,
# which it resolves deterministically.  Users who want the model-backed
# explanation can @-mention the matching researcher bot directly.
# Dispatch is reserved for questions that genuinely need coordination:
# composite fan-outs and macro questions (which the local registry cannot
# answer at all).


@dataclass(frozen=True)
class DispatchTask:
    researcher: str
    question: str
    assignment: str = ""

    @property
    def full_question(self) -> str:
        """The duty-aligned assignment sent to the researcher and shown on the card."""
        return self.assignment or self.question


@dataclass(frozen=True)
class DispatchPlan:
    tasks: tuple[DispatchTask, ...]
    reason: str

    @property
    def needs_dispatch(self) -> bool:
        return bool(self.tasks)


@dataclass(frozen=True)
class DispatchOutcome:
    task: DispatchTask
    answer: str
    status: str  # READY / PARTIAL / UNKNOWN / TIMEOUT / ERROR
    error: str = ""
    stock_code: str | None = None
    stock_name: str | None = None
    research_as_of: str | None = None
    data_gaps: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status in {"READY", "PARTIAL"}

    @property
    def degraded(self) -> bool:
        """True when the answer is a deterministic fallback, not a model analysis."""
        return "MODEL_EXPLANATION_UNAVAILABLE" in self.data_gaps


def plan_dispatch(
    question: str, history: Iterable[dict[str, Any]] | None = None,
) -> DispatchPlan:
    """Decide which researchers answer *question* using deterministic rules.

    The routing reuses the supervisor's rule intents so dispatch stays
    explainable: composite questions fan out, single-domain questions go to
    the matching specialist, and supervisor-owned intents stay local.
    """
    from src.investment_research_supervisor.service import InvestmentResearchSupervisorService

    intent = InvestmentResearchSupervisorService.classify_intent(question)
    if intent == "SELF_INTRO":
        return DispatchPlan((), f"intent={intent}")
    if intent == "COMPREHENSIVE":
        researchers = ["financial_analyst", "valuation_researcher", "risk_researcher"]
        if _MACRO_PATTERN.search(question):
            researchers.append("macro_policy_researcher")
        return DispatchPlan(
            tuple(
                DispatchTask(name, question, _assignment(name, question))
                for name in researchers
            ),
            f"intent={intent}",
        )
    macro_only = _MACRO_PATTERN.search(question) and intent == "COMPANY_OVERVIEW"
    if macro_only:
        # Macro questions never resolve to a company, so the supervisor's own
        # snapshot registry cannot answer them — delegate to the macro bot.
        return DispatchPlan(
            (DispatchTask(
                "macro_policy_researcher", question,
                _assignment("macro_policy_researcher", question),
            ),),
            f"intent={intent},macro",
        )
    return DispatchPlan((), f"intent={intent}")


def _run_financial(question: str) -> DispatchOutcome:
    task = DispatchTask("financial_analyst", question)
    from src.financial_analysis.service import get_financial_analysis_service

    result = dict(
        get_financial_analysis_service().chat_current_leader_pool(
            question=question, history=[], progress=None,
        ) or {}
    )
    answer = str(result.get("answer") or "").strip()
    status = "READY" if answer else "UNKNOWN"
    return DispatchOutcome(
        task, answer, status,
        stock_code=str(result["stock_code"]) if result.get("stock_code") else None,
        stock_name=str(result["stock_name"]) if result.get("stock_name") else None,
        research_as_of=str(result.get("as_of"))[:10] if result.get("as_of") else None,
    )


def _run_specialist(agent: str, question: str) -> DispatchOutcome:
    task = DispatchTask(agent, question)
    from src.research_specialist_chat import get_research_specialist_chat_service

    brief = get_research_specialist_chat_service().handle_question(
        agent=agent, question=question, history=[],
    )
    answer = str(brief.answer or "").strip()
    status = str(brief.status or ("READY" if answer else "UNKNOWN"))
    if answer and status not in {"READY", "PARTIAL", "UNKNOWN", "TIMEOUT", "ERROR"}:
        status = "READY" if answer else "UNKNOWN"
    return DispatchOutcome(
        task, answer, status,
        stock_code=brief.stock_code, stock_name=brief.stock_name,
        research_as_of=brief.research_as_of,
        data_gaps=tuple(brief.data_gaps or ()),
    )


_RUNNERS: dict[str, Callable[[str], DispatchOutcome]] = {
    "financial_analyst": _run_financial,
}


async def run_dispatch_tasks(tasks: Iterable[DispatchTask]) -> list[DispatchOutcome]:
    """Run dispatch tasks in parallel, each bounded by DISPATCH_TASK_TIMEOUT_S."""

    async def run_one(task: DispatchTask) -> DispatchOutcome:
        runner = _RUNNERS.get(task.researcher)
        if runner is None:
            runner = lambda question, agent=task.researcher: _run_specialist(agent, question)  # noqa: E731
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(runner, task.full_question),
                timeout=DISPATCH_TASK_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            return DispatchOutcome(
                task, "", "TIMEOUT",
                error=f"研究员未在 {DISPATCH_TASK_TIMEOUT_S:.0f} 秒时限内返回，"
                      "可能是角色模型响应过慢或未正确配置",
            )
        except Exception as exc:  # noqa: BLE001 - one failed researcher must not sink the batch
            return DispatchOutcome(task, "", "ERROR", error=f"{type(exc).__name__}: {exc}")

    return list(await asyncio.gather(*(run_one(task) for task in tasks)))


def _summary_target_schema() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "supervisor_dispatch_summary",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "maxLength": 1500},
                },
                "required": ["summary"],
                "additionalProperties": False,
            },
        },
    }


def _summarize_with_model(question: str, outcomes: list[DispatchOutcome]) -> str:
    from src.research_tasks.service import ProviderModelRuntime
    from src.research_tasks.store import ResearchTaskStore

    config = ResearchTaskStore().get_runtime_config("research_lead")
    if not config.get("enabled") or not config.get("model"):
        raise RuntimeError("research_lead 模型未启用")
    instruction = (
        "你是投研主管，负责汇总多位研究员的独立回答并给出综合结论。"
        "只能基于 payload.researcher_answers 中的内容综合，不得引入新的事实或数字。"
        "按以下结构输出：先用 2-3 句给出综合结论（估值与风险的总体判断）；"
        "再分要点提炼各研究员的关键发现与相互分歧（明确指出谁与谁不一致及差异内容）；"
        "最后用 1-2 句提醒最值得后续验证的问题。使用通俗中文，控制在 300-600 字。"
        "禁止给出买入、卖出、仓位、止盈止损等交易指令。"
        # The endpoint may ignore response_format, so the JSON contract must
        # also live in the instruction or the synthesis is lost to the parser.
        "输出必须是且仅是一个 JSON 对象，不要 markdown 代码块和任何额外文字，格式为："
        '{"summary":"<汇总正文>"}。'
    )
    payload = {
        "question": question,
        "researcher_answers": [
            {
                "researcher": RESEARCHER_TITLES.get(o.task.researcher, o.task.researcher),
                "status": o.status,
                "answer": o.answer[:4000],
            }
            for o in outcomes
        ],
    }
    runtime = ProviderModelRuntime()
    kwargs: dict[str, Any] = {
        "role": "research_lead",
        "phase": "SUPERVISOR_SUMMARY",
        "model": str(config["model"]),
        "instruction": instruction,
        "payload": payload,
        "target_schema": _summary_target_schema(),
    }
    if config.get("base_url") and hasattr(runtime, "invoke_with_connection"):
        output = runtime.invoke_with_connection(
            **kwargs, base_url=str(config["base_url"]), api_key=str(config.get("api_key") or ""),
        )
    else:
        output = runtime.invoke(**kwargs, provider=str(config.get("provider") or "openai"))
    summary = str(dict(output).get("summary") or "").strip()
    if not summary or _TRADING_LANGUAGE.search(summary):
        raise ValueError("supervisor summary failed safety validation")
    return summary


def _rule_summary(outcomes: list[DispatchOutcome]) -> str:
    lines = ["主管汇总模型暂不可用，以下为各研究员回答要点：", ""]
    for outcome in outcomes:
        title = RESEARCHER_TITLES.get(outcome.task.researcher, outcome.task.researcher)
        if outcome.ok:
            text = outcome.answer.strip().replace("\n", " ")
            if len(text) > 600:
                text = text[:600] + "……"
            lines.append(f"**{title}**：{text}")
        else:
            lines.append(f"**{title}**：未能返回结果（{outcome.status}）")
        lines.append("")
    return "\n".join(lines).strip()


def summarize_dispatch(question: str, outcomes: list[DispatchOutcome]) -> str:
    """Synthesize researcher answers into one supervisor conclusion."""
    answered = [o for o in outcomes if o.ok and o.answer]
    if not answered:
        return "本次分派的研究员均未返回有效结果，请稍后重试或直接 @对应研究员提问。"
    try:
        return _summarize_with_model(question, outcomes)
    except Exception:  # noqa: BLE001 - degrade to deterministic stitching
        return _rule_summary(outcomes)
