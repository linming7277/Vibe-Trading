"""Hermes supervisor Feishu credentials for outbound research publications.

Hermes owns the interactive bot gateways.  The Value Line backend deliberately
does not start its legacy Feishu channels, but daily reports and Bitable
publication still need the same supervisor app identity for outbound calls.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


def _default_env_path() -> Path:
    configured = str(os.getenv("HZSTOCK_HERMES_SUPERVISOR_ENV_PATH") or "").strip()
    if configured:
        return Path(configured)
    local_app_data = str(os.getenv("LOCALAPPDATA") or "").strip()
    if local_app_data:
        return Path(local_app_data) / "hermes" / "profiles" / "hzstocksupervisor" / ".env"
    return Path.home() / ".hermes" / "profiles" / "hzstocksupervisor" / ".env"


def _read_dotenv(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[7:].strip()
        if key:
            values[key] = value.strip().strip("\"'")
    return values


@dataclass(frozen=True)
class HermesSupervisorFeishuCredentials:
    app_id: str
    app_secret: str
    domain: str
    target_id: str

    @classmethod
    def load(
        cls,
        *,
        env_path: Path | None = None,
        values: Mapping[str, str] | None = None,
    ) -> "HermesSupervisorFeishuCredentials":
        source = dict(values) if values is not None else _read_dotenv(env_path or _default_env_path())
        app_id = str(source.get("FEISHU_APP_ID") or "").strip()
        app_secret = str(source.get("FEISHU_APP_SECRET") or "").strip()
        target_id = str(source.get("FEISHU_HOME_CHANNEL") or "").strip()
        if not app_id or not app_secret:
            raise RuntimeError("Hermes supervisor Feishu credentials are not configured")
        if not target_id:
            raise RuntimeError("Hermes supervisor Feishu home channel is not configured")
        return cls(
            app_id=app_id,
            app_secret=app_secret,
            domain=str(source.get("FEISHU_DOMAIN") or "feishu").strip().lower() or "feishu",
            target_id=target_id,
        )

    def create_lark_client(self):
        from src.channels.feishu import _load_lark_runtime

        lark, feishu_domain, lark_domain = _load_lark_runtime()
        domain = lark_domain if self.domain == "lark" else feishu_domain
        return (
            lark.Client.builder()
            .app_id(self.app_id)
            .app_secret(self.app_secret)
            .domain(domain)
            .log_level(lark.LogLevel.INFO)
            .build()
        )
