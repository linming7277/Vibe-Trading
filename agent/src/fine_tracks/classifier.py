"""Strict, provider-backed fine-track discovery and company classification."""

from __future__ import annotations

import json
import re
from typing import Any, Protocol

from src.providers.chat import ChatLLM

from .models import business_text, normalize_track_name, track_semantic_key


class TrackClassifierRuntime(Protocol):
    def invoke(self, *, provider: str, model: str, instruction: str,
               payload: dict[str, Any]) -> dict[str, Any]: ...


def _response_format(company_codes: list[str]) -> dict[str, Any]:
    company = {
        "type": "object",
        "properties": {
            "stock_code": {"type": "string", "enum": company_codes},
            "membership_type": {"type": "string", "enum": ["PRIMARY", "SECONDARY"]},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "reason": {"type": "string", "minLength": 4, "maxLength": 240},
        },
        "required": ["stock_code", "membership_type", "confidence", "reason"],
        "additionalProperties": False,
    }
    schema = {
        "type": "object",
        "properties": {
            "industry_code": {"type": "string"},
            "industry_name": {"type": "string"},
            "tracks": {"type": "array", "maxItems": 12, "items": {
                "type": "object",
                "properties": {
                    "track_name": {"type": "string", "minLength": 2, "maxLength": 40},
                    "description": {"type": "string", "minLength": 6, "maxLength": 240},
                    "companies": {"type": "array", "items": company},
                },
                "required": ["track_name", "description", "companies"],
                "additionalProperties": False,
            }},
            "unclassified": {"type": "array", "items": {
                "type": "object",
                "properties": {
                    "stock_code": {"type": "string", "enum": company_codes},
                    "classification_status": {"type": "string", "enum": ["UNCLASSIFIED", "INDUSTRY_CLASSIFICATION_CONFLICT"]},
                    "reason": {"type": "string", "minLength": 4, "maxLength": 240},
                },
                "required": ["stock_code", "classification_status", "reason"],
                "additionalProperties": False,
            }},
        },
        "required": ["industry_code", "industry_name", "tracks", "unclassified"],
        "additionalProperties": False,
    }
    return {"type": "json_schema", "json_schema": {"name": "fine_track_classification", "strict": True, "schema": schema}}


def _parse_json(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("<answer>") and text.endswith("</answer>"):
        text = text[len("<answer>"):-len("</answer>")].strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("classifier response must be a JSON object")
    return value


class ProviderTrackClassifierRuntime:
    def invoke(self, *, provider: str, model: str, instruction: str,
               payload: dict[str, Any]) -> dict[str, Any]:
        client = ChatLLM(model_name=model, provider_name=provider)
        if provider.strip().lower() == "ollama":
            instruction = f"/no_think\n{instruction}"
        content = json.dumps(payload, ensure_ascii=False, default=str)
        # Codex OAuth is part of the same provider layer but its lightweight
        # adapter does not expose LangChain ``bind(response_format=...)``.
        # Keep strict post-validation for every provider and request native
        # JSON Schema wherever the selected adapter supports it.
        response = client.chat(
            [{"role": "system", "content": instruction}, {"role": "user", "content": content}],
            response_format=(None if provider.strip().lower() in {"openai-codex", "ollama"} else
                             _response_format([row["stock_code"] for row in payload["companies"]])),
        )
        if not response.content:
            raise RuntimeError("empty model response")
        return _parse_json(response.content)

    def invoke_with_connection(self, *, model: str, base_url: str, api_key: str,
                               instruction: str, payload: dict[str, Any]) -> dict[str, Any]:
        client = ChatLLM(
            model_name=model,
            provider_name="openai",
            base_url=base_url,
            api_key=api_key,
        )
        response = client.chat(
            [{"role": "system", "content": instruction},
             {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)}],
            response_format=_response_format([row["stock_code"] for row in payload["companies"]]),
        )
        if not response.content:
            raise RuntimeError("empty model response")
        return _parse_json(response.content)


CLASSIFIER_INSTRUCTION = """你是细分赛道分类研究员，只做同一父级行业内部的业务分类。仅依据输入的主营业务、经营范围、公司描述、产品和产业链位置；禁止使用股票名称猜业务，禁止使用股价、涨跌幅、PE、市值或资金指标。先复用 existing_tracks，只有确实无法归入才创建新赛道。赛道体现产品、技术、客户、业务模式或产业链环节，粒度适中，通常2至12个。每家公司最多1个PRIMARY和2个SECONDARY；每个归类必须给出引用业务事实的理由。不能跨出父行业；父行业归属可疑时只输出INDUSTRY_CLASSIFICATION_CONFLICT。严格返回指定JSON，不输出解释文字。"""


def validate_batch_result(result: dict[str, Any], *, industry: dict[str, Any],
                          company_codes: set[str]) -> dict[str, Any]:
    if str(result.get("industry_code")) != industry["industry_code"]:
        raise ValueError("classifier returned a different parent industry")
    tracks = result.get("tracks")
    unclassified = result.get("unclassified")
    if not isinstance(tracks, list) or not isinstance(unclassified, list):
        raise ValueError("classifier output is missing tracks/unclassified arrays")
    counts: dict[str, dict[str, int]] = {}
    normalized_tracks: list[dict[str, Any]] = []
    for track in tracks:
        name = normalize_track_name(track.get("track_name", ""))
        description = str(track.get("description") or "").strip()
        if len(name) < 2 or len(description) < 6:
            raise ValueError("track name/description is incomplete")
        companies = []
        for company in track.get("companies") or []:
            code = str(company.get("stock_code") or "").upper()
            membership_type = str(company.get("membership_type") or "").upper()
            if code not in company_codes or membership_type not in {"PRIMARY", "SECONDARY"}:
                raise ValueError("classifier returned an invalid company or membership type")
            confidence = float(company.get("confidence"))
            reason = str(company.get("reason") or "").strip()
            if not 0 <= confidence <= 1 or len(reason) < 4:
                raise ValueError("classifier returned invalid confidence/reason")
            counter = counts.setdefault(code, {"PRIMARY": 0, "SECONDARY": 0})
            counter[membership_type] += 1
            if counter["PRIMARY"] > 1 or counter["SECONDARY"] > 2:
                raise ValueError(f"membership limit exceeded for {code}")
            companies.append({"stock_code": code, "membership_type": membership_type,
                              "confidence": confidence, "reason": reason})
        normalized_tracks.append({"track_name": name, "description": description, "companies": companies})
    clean_unclassified = []
    for row in unclassified:
        code = str(row.get("stock_code") or "").upper()
        if code not in company_codes:
            raise ValueError("classifier returned an invalid unclassified company")
        clean_unclassified.append({
            "stock_code": code,
            "classification_status": str(row.get("classification_status") or "UNCLASSIFIED"),
            "reason": str(row.get("reason") or "模型未给出可验证分类"),
        })
    return {"industry_code": industry["industry_code"], "industry_name": industry["industry_name"],
            "tracks": normalized_tracks, "unclassified": clean_unclassified}


def merge_batch_result(aggregate: dict[str, Any], batch: dict[str, Any]) -> None:
    by_key = {track_semantic_key(row["track_name"]): row for row in aggregate["tracks"]}
    for track in batch["tracks"]:
        key = track_semantic_key(track["track_name"])
        if key in by_key:
            by_key[key]["companies"].extend(track["companies"])
            if not by_key[key].get("description"):
                by_key[key]["description"] = track["description"]
        else:
            aggregate["tracks"].append(track)
            by_key[key] = track
    aggregate["unclassified"].extend(batch["unclassified"])


def eligible_profile(profile: dict[str, Any]) -> bool:
    # Concise but explicit TDX values such as “白酒” or “煤炭开采” are real
    # business facts. Only a genuinely empty profile is insufficient.
    return profile.get("data_status") != "MISSING" and len(business_text(profile)) >= 2
