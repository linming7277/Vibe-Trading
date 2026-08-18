"""Safe provider catalogue for per-agent model selection."""

from __future__ import annotations

import json
import hashlib
import urllib.request
from functools import lru_cache
from pathlib import Path
from typing import Any

from src.config.accessor import get_env_config
from src.providers.capabilities import get_llm_credentials


def _catalog_rows() -> list[dict[str, Any]]:
    path = Path(__file__).resolve().parents[1] / "providers" / "llm_providers.json"
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _local_ollama_models(base_url: str) -> tuple[str, ...]:
    """Discover installed local models without exposing credentials."""
    url = f"{base_url.rstrip('/').removesuffix('/v1')}/v1/models"
    try:
        with urllib.request.urlopen(url, timeout=1.5) as response:  # noqa: S310 - fixed local provider URL
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return ()
    rows = payload.get("data", []) if isinstance(payload, dict) else []
    return tuple(sorted({str(row.get("id") or "").strip() for row in rows if isinstance(row, dict)} - {""}))


def safe_provider_catalog(configs: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Return provider/model availability without secret values or secret names."""
    cfg = get_env_config().llm
    configured_models: dict[str, set[str]] = {}
    for item in configs or []:
        configured_models.setdefault(str(item.get("provider") or ""), set()).add(str(item.get("model") or ""))
    configured_models.setdefault(cfg.langchain_provider.strip().lower(), set()).add(cfg.langchain_model_name.strip())
    result: list[dict[str, Any]] = []
    for raw in _catalog_rows():
        provider = str(raw["name"]).strip().lower()
        default_model = str(raw.get("default_model") or "").strip()
        models = {default_model, *configured_models.get(provider, set())}
        credentials = get_llm_credentials(
            provider, default_model, allow_openai_fallback=False
        )
        requires_key = bool(raw.get("api_key_required", True))
        configured = bool(credentials.get("api_key")) if requires_key else True
        if provider == "ollama":
            models.update(_local_ollama_models(str(raw.get("default_base_url") or "http://localhost:11434")))
        result.append({
            "provider": provider,
            "label": str(raw.get("label") or provider),
            "models": sorted(model for model in models if model),
            "default_model": default_model,
            "configured": configured,
        })
    return result


def validate_provider_model(provider: str, model: str, configs: list[dict[str, Any]] | None = None) -> None:
    row = next((item for item in safe_provider_catalog(configs) if item["provider"] == provider), None)
    if not row:
        raise ValueError("CONFIGURATION_ERROR: unknown provider")
    if not model.strip():
        raise ValueError("CONFIGURATION_ERROR: model is required")
    if model not in row["models"]:
        raise ValueError("CONFIGURATION_ERROR: model is not available for this provider")


def _model_selection_id(provider: str, model: str) -> str:
    """Create a stable opaque id so the UI only needs to understand models."""
    value = f"{provider}\0{model}".encode("utf-8")
    return hashlib.sha256(value).hexdigest()[:24]


def _role_model_options(
    role: str,
    configs: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, tuple[str, str]]]:
    catalog = safe_provider_catalog(configs)
    current = next((item for item in configs if item.get("role") == role), None)
    candidates: dict[str, list[tuple[str, bool]]] = {}
    for source in catalog:
        provider = str(source["provider"])
        if role == "track_classifier" and provider == "ollama":
            continue
        for model in source["models"]:
            candidates.setdefault(str(model), []).append((provider, bool(source["configured"])))

    options: list[dict[str, Any]] = []
    lookup: dict[str, tuple[str, str]] = {}
    for model_name in sorted(candidates, key=str.casefold):
        choices = candidates[model_name]
        current_provider = str(current.get("provider")) if current and current.get("model") == model_name else ""
        provider, ready = next(
            (
                (provider, configured)
                for provider, configured in choices
                if provider == current_provider and configured
            ),
            next(
                ((provider, configured) for provider, configured in choices if configured),
                next(((provider, configured) for provider, configured in choices if provider == current_provider), choices[0]),
            ),
        )
        model_id = _model_selection_id(provider, model_name)
        lookup[model_id] = (provider, model_name)
        options.append({"id": model_id, "name": model_name, "ready": ready})
    return options, lookup


def safe_agent_model_settings(configs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return per-role model choices without exposing connection implementation details."""
    items: list[dict[str, Any]] = []
    for config in configs:
        role = str(config["role"])
        options, lookup = _role_model_options(role, configs)
        current_provider = str(config.get("provider") or "").strip()
        current_model = str(config.get("model") or "").strip()
        current_id = _model_selection_id(current_provider, current_model) if current_provider and current_model else ""
        current_option = next((option for option in options if option["id"] == current_id), None)
        items.append({
            "role": role,
            "model_id": current_id,
            "model_name": current_model,
            "enabled": bool(config["enabled"]),
            "ready": bool(current_option and current_option["ready"]),
            "updated_at": config["updated_at"],
            "models": options,
        })
    return items


def resolve_agent_model_selection(
    role: str,
    model_id: str,
    configs: list[dict[str, Any]],
) -> tuple[str, str]:
    """Resolve an opaque model choice back to the internal connection details."""
    _, lookup = _role_model_options(role, configs)
    try:
        return lookup[model_id]
    except KeyError as exc:
        raise ValueError("CONFIGURATION_ERROR: selected model is not available for this researcher") from exc
