"""Persistent, redacted settings for personal IM channels."""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any

import httpx

from src.channels.feishu import FeishuConfig
from src.config.paths import get_config_path
from src.config.schema import AgentConfig

try:
    import yaml
except ImportError:  # pragma: no cover - JSON is the default operator format
    yaml = None  # type: ignore


def _read_raw_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        value = json.loads(text)
    elif path.suffix.lower() in {".yaml", ".yml"}:
        if yaml is None:
            raise ValueError("PyYAML is required to update a YAML agent config")
        value = yaml.safe_load(text) or {}
    else:
        raise ValueError(f"Unsupported agent config format: {path.suffix or '<none>'}")
    if not isinstance(value, dict):
        raise ValueError("Agent config must be a JSON/YAML object")
    return value


def _write_raw_config(path: Path, value: dict[str, Any]) -> None:
    """Atomically persist operator config without ever logging its contents."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".json":
        payload = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    elif path.suffix.lower() in {".yaml", ".yml"}:
        if yaml is None:
            raise ValueError("PyYAML is required to update a YAML agent config")
        payload = yaml.safe_dump(value, allow_unicode=True, sort_keys=False)
    else:
        raise ValueError(f"Unsupported agent config format: {path.suffix or '<none>'}")

    fd, tmp_name = tempfile.mkstemp(prefix=".agent.", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        with suppress(OSError):
            os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, path)
        with suppress(OSError):
            os.chmod(path, 0o600)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()
def get_feishu_channel_settings(
    channel_name: str,
    *,
    include_secret: bool = False,
    config_path: Path | None = None,
) -> dict[str, Any]:
    """Read one named Feishu bot instance without exposing its secret by default."""
    if channel_name not in {
        "feishu", "feishu_supervisor", "feishu_risk", "feishu_valuation", "feishu_macro_policy",
    }:
        raise ValueError(f"unsupported Feishu channel: {channel_name}")
    path = get_config_path(config_path)
    raw = _read_raw_config(path)
    channels = raw.get("channels") if isinstance(raw.get("channels"), dict) else {}
    section = channels.get(channel_name) if isinstance(channels.get(channel_name), dict) else {}
    config = FeishuConfig.model_validate(section)
    result: dict[str, Any] = {
        "auto_start": bool(channels.get("auto_start", False)),
        "enabled": config.enabled,
        "app_id": config.app_id,
        "app_secret_configured": bool(config.app_secret),
        "domain": config.domain,
        "group_policy": config.group_policy,
        "reply_to_message": config.reply_to_message,
        "streaming": config.streaming,
        "topic_isolation": config.topic_isolation,
        "default_agent": config.default_agent,
        "low_value_leader_notification": {
            "enabled": config.low_value_leader_notification.enabled,
            "target_configured": bool(config.low_value_leader_notification.target_id),
            "web_base_url": config.low_value_leader_notification.web_base_url,
            "dry_run": config.low_value_leader_notification.dry_run,
        },
        "daily_research_brief_notification": {
            "enabled": config.daily_research_brief_notification.enabled,
            "target_configured": bool(config.daily_research_brief_notification.target_id),
            "web_base_url": config.daily_research_brief_notification.web_base_url,
            "dry_run": config.daily_research_brief_notification.dry_run,
        },
        "allow_from_count": len(config.allow_from),
        "config_path": str(path),
    }
    if include_secret:
        result["app_secret"] = config.app_secret
        result["allow_from"] = list(config.allow_from)
    return result


def get_feishu_settings(
    *,
    include_secret: bool = False,
    config_path: Path | None = None,
) -> dict[str, Any]:
    """Backward-compatible reader for the existing financial Feishu bot."""
    return get_feishu_channel_settings(
        "feishu", include_secret=include_secret, config_path=config_path,
    )


def save_feishu_channel_settings(
    channel_name: str,
    values: dict[str, Any],
    *,
    config_path: Path | None = None,
) -> dict[str, Any]:
    """Persist one named Feishu bot instance while preserving sibling bots."""
    if channel_name not in {
        "feishu", "feishu_supervisor", "feishu_risk", "feishu_valuation", "feishu_macro_policy",
    }:
        raise ValueError(f"unsupported Feishu channel: {channel_name}")
    path = get_config_path(config_path)
    raw = _read_raw_config(path)
    channels = raw.get("channels")
    if not isinstance(channels, dict):
        channels = {}
        raw["channels"] = channels
    existing = channels.get(channel_name)
    merged = dict(existing) if isinstance(existing, dict) else {}
    merged.update(values)
    auto_start = bool(merged.pop("auto_start", channels.get("auto_start", False)))
    validated = FeishuConfig.model_validate(merged)
    normalized = validated.model_dump(mode="json")
    # Preserve channel-scoped operator configuration that belongs to the
    # generic runtime rather than FeishuConfig itself.
    if isinstance(existing, dict) and "operators" in existing:
        normalized["operators"] = existing["operators"]
    channels[channel_name] = normalized
    channels["auto_start"] = auto_start
    AgentConfig.model_validate(raw)
    _write_raw_config(path, raw)
    return get_feishu_channel_settings(channel_name, config_path=path)


def save_feishu_settings(
    values: dict[str, Any],
    *,
    config_path: Path | None = None,
) -> dict[str, Any]:
    """Backward-compatible writer for the existing financial Feishu bot."""
    return save_feishu_channel_settings("feishu", values, config_path=config_path)


def verify_feishu_credentials(app_id: str, app_secret: str) -> dict[str, str]:
    """Verify credentials and return non-secret bot identity metadata."""
    try:
        # Feishu is a domestic control-plane dependency.  Do not inherit the
        # workstation's optional external research proxy, which can terminate
        # TLS handshakes and make valid bot credentials look broken.
        with httpx.Client(timeout=12.0, trust_env=False) as client:
            token_response = client.post(
                "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                json={"app_id": app_id, "app_secret": app_secret},
            )
            token_response.raise_for_status()
            token_payload = token_response.json()
            if token_payload.get("code") not in (None, 0):
                raise ValueError(
                    f"飞书凭证验证失败（code={token_payload.get('code')}）："
                    f"{token_payload.get('msg') or '未知错误'}"
                )
            token = str(token_payload.get("tenant_access_token") or "")
            if not token:
                raise ValueError("飞书凭证验证失败：未返回 tenant_access_token")
            bot_response = client.get(
                "https://open.feishu.cn/open-apis/bot/v3/info",
                headers={"Authorization": f"Bearer {token}"},
            )
            bot_response.raise_for_status()
            bot_payload = bot_response.json()
            if bot_payload.get("code") not in (None, 0):
                raise ValueError(
                    f"读取飞书机器人信息失败（code={bot_payload.get('code')}）："
                    f"{bot_payload.get('msg') or '未知错误'}"
                )
    except httpx.HTTPError as exc:
        raise ValueError(f"无法连接飞书开放平台：{type(exc).__name__}") from exc

    bot = ((bot_payload.get("data") or {}).get("bot") or bot_payload.get("bot") or {})
    return {
        "app_name": str(bot.get("app_name") or bot.get("name") or ""),
        "open_id": str(bot.get("open_id") or ""),
    }
