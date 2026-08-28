"""Small, provider-agnostic structured output capability layer.

Capabilities are supplied by configuration, never inferred from a model name
or an ``OpenAI-compatible`` connection label.  Native response formats merely
help a model comply; application validators remain the safety boundary.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any, Callable

from src.providers.chat import ChatLLM


class StructuredOutputMode(StrEnum):
    JSON_SCHEMA = "JSON_SCHEMA"
    JSON_OBJECT = "JSON_OBJECT"
    PROMPT_JSON = "PROMPT_JSON"
    TEXT_ONLY = "TEXT_ONLY"


@dataclass(frozen=True)
class StructuredOutputCapabilities:
    supports_json_schema: bool = False
    supports_json_object: bool = False
    supports_plain_json: bool = True
    supports_enum: bool = False
    supports_array_constraints: bool = False
    supports_additional_properties_false: bool = False
    preferred_mode: StructuredOutputMode = StructuredOutputMode.PROMPT_JSON
    fallback_order: tuple[StructuredOutputMode, ...] = ()
    timeout_profile: str = "default"
    source: str = "conservative_default"

    def modes(self) -> list[StructuredOutputMode]:
        supported = {
            StructuredOutputMode.JSON_SCHEMA: self.supports_json_schema,
            StructuredOutputMode.JSON_OBJECT: self.supports_json_object,
            StructuredOutputMode.PROMPT_JSON: self.supports_plain_json,
            StructuredOutputMode.TEXT_ONLY: True,
        }
        requested = self.fallback_order or (
            StructuredOutputMode.JSON_SCHEMA, StructuredOutputMode.JSON_OBJECT,
            StructuredOutputMode.PROMPT_JSON, StructuredOutputMode.TEXT_ONLY,
        )
        ordered = [self.preferred_mode, *requested]
        result: list[StructuredOutputMode] = []
        for mode in ordered:
            if supported.get(mode, False) and mode not in result:
                result.append(mode)
        return result or [StructuredOutputMode.TEXT_ONLY]

    def audit_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["preferred_mode"] = self.preferred_mode.value
        result["fallback_order"] = [mode.value for mode in self.fallback_order]
        return result


@dataclass(frozen=True)
class StructuredOutputResult:
    parsed: dict[str, Any] | None
    text: str | None
    mode_requested: str
    mode_used: str
    fallback_path: list[str]
    attempts: list[dict[str, Any]]
    error_types: list[dict[str, str]]
    capability_profile: dict[str, Any]
    capability_source: str


def _mode(value: Any, fallback: StructuredOutputMode) -> StructuredOutputMode:
    try:
        return StructuredOutputMode(str(value or fallback.value).upper())
    except ValueError:
        return fallback


def resolve_structured_output_capabilities(config: dict[str, Any]) -> StructuredOutputCapabilities:
    """Resolve explicit configuration or use a conservative plain-JSON default.

    ``provider`` and ``model`` are intentionally not inspected.  A connection
    is not proof of any response-format capability.
    """
    raw = dict(config.get("structured_output") or config.get("capabilities", {}).get("structured_output") or {})
    if not raw:
        return StructuredOutputCapabilities()
    raw_order = raw.get("fallback_order") or []
    order = tuple(_mode(item, StructuredOutputMode.PROMPT_JSON) for item in raw_order)
    preferred = _mode(raw.get("preferred_mode"), StructuredOutputMode.PROMPT_JSON)
    return StructuredOutputCapabilities(
        supports_json_schema=bool(raw.get("supports_json_schema", False)),
        supports_json_object=bool(raw.get("supports_json_object", False)),
        supports_plain_json=bool(raw.get("supports_plain_json", True)),
        supports_enum=bool(raw.get("supports_enum", False)),
        supports_array_constraints=bool(raw.get("supports_array_constraints", False)),
        supports_additional_properties_false=bool(raw.get("supports_additional_properties_false", False)),
        preferred_mode=preferred, fallback_order=order,
        timeout_profile=str(raw.get("timeout_profile") or "default"),
        source="model_config_override",
    )


def _parse_json(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("structured output must be a JSON object")
    return value


class StructuredOutputRuntime:
    """Run a contract through the configured provider capabilities."""

    def __init__(self, client_factory: Callable[..., Any] = ChatLLM) -> None:
        self.client_factory = client_factory

    @staticmethod
    def provider_schema(contract_schema: dict[str, Any], mode: StructuredOutputMode,
                        capabilities: StructuredOutputCapabilities) -> dict[str, Any] | None:
        if mode is StructuredOutputMode.JSON_OBJECT:
            return {"type": "json_object"}
        if mode is not StructuredOutputMode.JSON_SCHEMA:
            return None
        # Native schemas may be deliberately weaker than the application
        # contract when a configured connector cannot enforce all constraints.
        schema = json.loads(json.dumps(contract_schema))
        if not capabilities.supports_enum:
            def strip_enum(value: Any) -> None:
                if isinstance(value, dict):
                    value.pop("enum", None)
                    for child in value.values(): strip_enum(child)
                elif isinstance(value, list):
                    for child in value: strip_enum(child)
            strip_enum(schema)
        if not capabilities.supports_array_constraints:
            def strip_arrays(value: Any) -> None:
                if isinstance(value, dict):
                    value.pop("maxItems", None)
                    for child in value.values(): strip_arrays(child)
                elif isinstance(value, list):
                    for child in value: strip_arrays(child)
            strip_arrays(schema)
        if not capabilities.supports_additional_properties_false:
            def relax_extra(value: Any) -> None:
                if isinstance(value, dict):
                    value.pop("additionalProperties", None)
                    for child in value.values(): relax_extra(child)
                elif isinstance(value, list):
                    for child in value: relax_extra(child)
            relax_extra(schema)
        return {"type": "json_schema", "json_schema": {"name": "financial_claims", "strict": True, "schema": schema}}

    def _client(self, config: dict[str, Any]) -> Any:
        if config.get("base_url"):
            return self.client_factory(model_name=config["model"], provider_name="openai",
                                       base_url=config["base_url"], api_key=config.get("api_key") or "")
        return self.client_factory(model_name=config["model"], provider_name=config["provider"])

    def run(self, *, config: dict[str, Any], instruction: str, payload: dict[str, Any],
            contract_schema: dict[str, Any], capabilities: StructuredOutputCapabilities,
            text_instruction: str, text_payload: dict[str, Any],
            invoke_structured: Callable[[StructuredOutputMode, dict[str, Any] | None], dict[str, Any]] | None = None,
            validate: Callable[[dict[str, Any]], dict[str, Any]] | None = None) -> StructuredOutputResult:
        requested = capabilities.preferred_mode.value
        attempts: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        modes = capabilities.modes()
        for mode in modes:
            started = time.monotonic()
            try:
                client = self._client(config) if invoke_structured is None or mode is StructuredOutputMode.TEXT_ONLY else None
                if mode is StructuredOutputMode.TEXT_ONLY:
                    response = client.chat([
                        {"role": "system", "content": text_instruction},
                        {"role": "user", "content": json.dumps(text_payload, ensure_ascii=False, default=str)},
                    ])
                    text = (response.content or "").strip()
                    if not text:
                        raise RuntimeError("empty text fallback response")
                    attempts.append({"mode": mode.value, "success": True, "latency_ms": round((time.monotonic()-started)*1000)})
                    return StructuredOutputResult(None, text, requested, mode.value,
                                                  [item["mode"] for item in attempts], attempts, errors,
                                                  capabilities.audit_dict(), capabilities.source)
                response_format = self.provider_schema(contract_schema, mode, capabilities)
                if invoke_structured is not None:
                    parsed = invoke_structured(mode, response_format)
                else:
                    response = client.chat([
                        {"role": "system", "content": instruction},
                        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
                    ], response_format=response_format)
                    parsed = _parse_json(response.content or "")
                if validate is not None:
                    parsed = validate(parsed)
                attempts.append({"mode": mode.value, "success": True, "latency_ms": round((time.monotonic()-started)*1000)})
                return StructuredOutputResult(parsed, None, requested, mode.value,
                                              [item["mode"] for item in attempts], attempts, errors,
                                              capabilities.audit_dict(), capabilities.source)
            except Exception as exc:  # attempt metadata must not retain provider text or credentials
                latency_ms = round((time.monotonic()-started)*1000)
                diagnostic = {"mode": mode.value, "success": False, "latency_ms": latency_ms}
                error = {"mode": mode.value, "type": type(exc).__name__}
                if hasattr(exc, "audit_dict"):
                    details = exc.audit_dict()
                    diagnostic.update(details)
                    error.update({key: details[key] for key in ("validation_error_code", "claim_index", "error_summary")})
                attempts.append(diagnostic)
                errors.append(error)
        return StructuredOutputResult(None, None, requested, StructuredOutputMode.TEXT_ONLY.value,
                                      [item["mode"] for item in attempts], attempts, errors,
                                      capabilities.audit_dict(), capabilities.source)
