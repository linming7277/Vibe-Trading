"""Plain-language, read-only formatting for Company Research Overview."""

from __future__ import annotations

import re
from typing import Any


_TRADING_LANGUAGE = re.compile(r"买入|卖出|持有|目标价|仓位|止损|加仓|减仓|建仓|清仓")


def _safe_text(value: Any) -> str:
    """Keep existing research useful without relaying trading instructions."""
    text = str(value or "").strip()
    if not text:
        return ""
    if _TRADING_LANGUAGE.search(text):
        return "该条记录含交易措辞，未在公司研究总览中展示。"
    return text


def _join(items: list[str], *, limit: int = 2) -> str:
    cleaned = [item.rstrip("。；; ") for item in items[:limit] if item]
    return "；".join(item for item in cleaned if item)


def _citation_lines(overview: dict[str, Any], *, limit: int = 6) -> list[str]:
    """Return a bounded, human-readable subset only when the user asks."""
    entries: list[tuple[str, dict[str, Any]]] = []
    financial = dict(overview.get("financial_summary") or {})
    for item in financial.get("items") or []:
        if not isinstance(item, dict):
            continue
        for citation in item.get("citations") or []:
            if isinstance(citation, dict):
                entries.append(("财务", citation))
    for group in ("supporting_evidence", "challenging_evidence"):
        for item in overview.get(group) or []:
            if not isinstance(item, dict):
                continue
            for citation in item.get("citations") or []:
                if isinstance(citation, dict):
                    entries.append(("研究证据", citation))
    output: list[str] = []
    seen: set[tuple[str, str]] = set()
    for category, citation in entries:
        source = _safe_text(citation.get("source_title") or citation.get("source_key") or citation.get("source_type"))
        date = _safe_text(citation.get("data_as_of") or citation.get("source_date") or citation.get("period"))
        key = (category, source)
        if not source or key in seen:
            continue
        seen.add(key)
        output.append(f"{category}：{source}" + (f"（{date}）" if date else ""))
        if len(output) >= limit:
            break
    return output


def format_company_overview_for_chat(
    overview: dict[str, Any], *, max_length: int = 600, include_citations: bool = False,
) -> str:
    """Turn the shared overview projection into a concise Feishu response.

    This function is deliberately deterministic: it neither invokes a model nor
    infers missing company facts.  The prose uses only fields already returned
    by :class:`CompanyResearchOverviewService`.
    """
    company = dict(overview.get("company") or {})
    name = _safe_text(company.get("stock_name")) or _safe_text(company.get("stock_code")) or "该公司"
    code = _safe_text(company.get("stock_code"))
    business = dict(overview.get("business_summary") or {})
    financial = dict(overview.get("financial_summary") or {})
    thesis = overview.get("thesis") if isinstance(overview.get("thesis"), dict) else None
    review = overview.get("review") if isinstance(overview.get("review"), dict) else None
    support = [item for item in (overview.get("supporting_evidence") or []) if isinstance(item, dict)]
    challenge = [item for item in (overview.get("challenging_evidence") or []) if isinstance(item, dict)]
    watch_items = [item for item in (overview.get("watch_items") or []) if isinstance(item, dict)]

    paragraphs: list[str] = []
    prefix = f"{name}（{code}）" if code and code != name else name
    description = _safe_text(business.get("description"))
    if business.get("status") == "UNKNOWN":
        paragraphs.append(f"{prefix}的经营研究目前还没有生成，因此主营业务和经营变化的信息暂时不完整。")
    elif description:
        paragraphs.append(f"{prefix}{description}")

    changes = [_safe_text(item) for item in (business.get("changes") or [])]
    changes = [item for item in changes if item]
    if changes:
        paragraphs.append(f"经营方面：{_join(changes, limit=1)}")

    financial_items = [item for item in (financial.get("items") or []) if isinstance(item, dict)]
    financial_texts = [_safe_text(item.get("text")) for item in financial_items]
    financial_texts = [item for item in financial_texts if item]
    if financial_texts:
        paragraphs.append(f"财务方面：{_join(financial_texts, limit=3)}")
    elif financial.get("status") == "UNKNOWN":
        paragraphs.append("目前还没有生成财务研究快照，无法据此判断收入、利润和现金情况。")
    else:
        paragraphs.append("已有财务快照，但可直接展示的近期结论有限，需要结合后续财报继续核验。")

    support_texts = [_safe_text(item.get("claim") or item.get("summary")) for item in support]
    support_texts = [item for item in support_texts if item]
    if support_texts:
        paragraphs.append(f"支持当前逻辑的依据包括：{_join(support_texts, limit=2)}。")
    challenge_texts = [_safe_text(item.get("claim") or item.get("summary")) for item in challenge]
    challenge_texts = [item for item in challenge_texts if item]
    if challenge_texts:
        paragraphs.append(f"需要注意：{_join(challenge_texts, limit=2)}。")

    if thesis is None:
        paragraphs.append("当前尚未建立公司核心逻辑（Thesis）。")
    else:
        status = _safe_text(thesis.get("status_label")) or "状态待确认"
        core = _safe_text(thesis.get("core_thesis"))
        thesis_text = f"当前公司逻辑处于“{status}”状态"
        if core:
            thesis_text += f"：{core}"
        paragraphs.append(thesis_text + "。")
    if review and review.get("is_stale"):
        paragraphs.append("上一次逻辑复核已过期，表示后来有新证据进入；系统没有自动改动原有逻辑。")

    watch_texts = [_safe_text(item.get("text")) for item in watch_items]
    watch_texts = [item for item in watch_texts if item]
    if watch_texts:
        paragraphs.append(f"接下来重点看：{_join(watch_texts, limit=2)}")

    evidence_count = len(support) + len(challenge)
    if financial_items or evidence_count:
        paragraphs.append(f"研究依据：财务数据 {len(financial_items)} 条、支持或挑战当前逻辑的证据 {evidence_count} 条。")
    if include_citations:
        citations = _citation_lines(overview)
        if citations:
            paragraphs.append("具体依据：" + "；".join(citations) + "。")
        else:
            paragraphs.append("当前已保存的研究记录没有可展开的来源条目。")

    answer = "\n\n".join(paragraphs)
    if len(answer) <= max_length:
        return answer
    # Preserve sentence boundaries where possible instead of dumping a long
    # evidence list into an IM card.
    return answer[: max_length - 1].rstrip("；，。 \n") + "。"
