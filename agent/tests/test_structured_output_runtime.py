from __future__ import annotations

from src.structured_output import (
    StructuredOutputCapabilities,
    StructuredOutputMode,
    StructuredOutputRuntime,
    resolve_structured_output_capabilities,
)


CONTRACT = {"type": "object", "properties": {"summary": {"type": "string"}, "claims": {"type": "array"}}}


def _run(capabilities: StructuredOutputCapabilities, responses: dict[StructuredOutputMode, object]):
    seen: list[tuple[StructuredOutputMode, dict | None]] = []

    def invoke(mode: StructuredOutputMode, schema: dict | None):
        seen.append((mode, schema))
        value = responses[mode]
        if isinstance(value, Exception):
            raise value
        return value

    result = StructuredOutputRuntime().run(
        config={"provider": "any", "model": "MODEL_A"}, instruction="json", payload={}, contract_schema=CONTRACT,
        capabilities=capabilities, text_instruction="text", text_payload={}, invoke_structured=invoke,
    )
    return result, seen


def test_capability_resolution_is_conservative_without_override() -> None:
    capabilities = resolve_structured_output_capabilities({"provider": "anything", "model": "MODEL_A"})
    assert capabilities.preferred_mode is StructuredOutputMode.PROMPT_JSON
    assert capabilities.modes() == [StructuredOutputMode.PROMPT_JSON, StructuredOutputMode.TEXT_ONLY]


def test_capability_override_is_configuration_not_model_name() -> None:
    capabilities = resolve_structured_output_capabilities({"provider": "anything", "model": "MODEL_B", "structured_output": {
        "supports_json_schema": True, "supports_json_object": True, "preferred_mode": "JSON_SCHEMA",
    }})
    assert capabilities.source == "model_config_override"
    assert capabilities.modes()[:3] == [StructuredOutputMode.JSON_SCHEMA, StructuredOutputMode.JSON_OBJECT, StructuredOutputMode.PROMPT_JSON]


def test_json_schema_json_object_prompt_modes_and_fallback() -> None:
    schema_capabilities = StructuredOutputCapabilities(
        supports_json_schema=True, supports_json_object=True, preferred_mode=StructuredOutputMode.JSON_SCHEMA,
        supports_enum=True, supports_array_constraints=True, supports_additional_properties_false=True,
    )
    result, seen = _run(schema_capabilities, {StructuredOutputMode.JSON_SCHEMA: {"summary": "ok", "claims": []}})
    assert result.mode_used == "JSON_SCHEMA" and seen[0][1]["type"] == "json_schema"

    object_capabilities = StructuredOutputCapabilities(supports_json_object=True, preferred_mode=StructuredOutputMode.JSON_OBJECT)
    result, seen = _run(object_capabilities, {StructuredOutputMode.JSON_OBJECT: {"summary": "ok", "claims": []}})
    assert result.mode_used == "JSON_OBJECT" and seen[0][1] == {"type": "json_object"}

    prompt_capabilities = StructuredOutputCapabilities(preferred_mode=StructuredOutputMode.PROMPT_JSON)
    result, seen = _run(prompt_capabilities, {StructuredOutputMode.PROMPT_JSON: {"summary": "ok", "claims": []}})
    assert result.mode_used == "PROMPT_JSON" and seen[0][1] is None

    result, seen = _run(schema_capabilities, {
        StructuredOutputMode.JSON_SCHEMA: RuntimeError("unsupported"),
        StructuredOutputMode.JSON_OBJECT: {"summary": "ok", "claims": []},
    })
    assert result.mode_used == "JSON_OBJECT"
    assert result.error_types == [{"mode": "JSON_SCHEMA", "type": "RuntimeError"}]
    assert [mode for mode, _ in seen] == [StructuredOutputMode.JSON_SCHEMA, StructuredOutputMode.JSON_OBJECT]


def test_all_structured_failures_return_summary_only_boundary() -> None:
    capabilities = StructuredOutputCapabilities(preferred_mode=StructuredOutputMode.PROMPT_JSON)
    result, _ = _run(capabilities, {StructuredOutputMode.PROMPT_JSON: RuntimeError("no json")})
    assert result.parsed is None
    assert result.mode_used == "TEXT_ONLY"
    assert result.error_types[0] == {"mode": "PROMPT_JSON", "type": "RuntimeError"}


def test_application_validator_rejection_uses_next_structured_mode() -> None:
    capabilities = StructuredOutputCapabilities(
        supports_json_schema=True, supports_json_object=True, preferred_mode=StructuredOutputMode.JSON_SCHEMA,
    )

    def invoke(mode: StructuredOutputMode, schema: dict | None):
        return {"summary": "bad"} if mode is StructuredOutputMode.JSON_SCHEMA else {"summary": "ok", "claims": []}

    def validate(value: dict):
        if "claims" not in value:
            raise ValueError("claims required")
        return value

    result = StructuredOutputRuntime().run(
        config={"provider": "any", "model": "MODEL_A"}, instruction="json", payload={}, contract_schema=CONTRACT,
        capabilities=capabilities, text_instruction="text", text_payload={}, invoke_structured=invoke, validate=validate,
    )
    assert result.mode_used == "JSON_OBJECT"
    assert result.error_types == [{"mode": "JSON_SCHEMA", "type": "ValueError"}]
