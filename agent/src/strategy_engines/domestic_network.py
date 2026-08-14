"""Network policy for first-party mainland-China value-data sources.

This module deliberately bypasses ``HTTP_PROXY`` / ``HTTPS_PROXY`` for the
project's own HTTP clients.  It does not, and cannot, bypass a transparent
system/TUN proxy: configure the matching domain rules in the proxy client too.
"""

from __future__ import annotations

from collections.abc import Mapping

import httpx


# Keep this list aligned with launcher/windows/clash-domestic-data-direct-rules.yaml.
DOMESTIC_DATA_DIRECT_DOMAIN_SUFFIXES = (
    "gov.cn",
    "ndrc.gov.cn",
    "miit.gov.cn",
    "pbc.gov.cn",
    "safe.gov.cn",
    "chinamoney.com.cn",
    "shibor.org",
    "tushare.pro",
)


def direct_domestic_http_client(
    *,
    timeout: float = 20.0,
    headers: Mapping[str, str] | None = None,
) -> httpx.Client:
    """Return an HTTP client that never inherits shell proxy variables.

    ``trust_env=False`` is intentional.  Domestic official sources should
    leave through the local direct route even while the LLM/Codex traffic uses
    ``HTTP_PROXY`` or ``HTTPS_PROXY``.
    """

    return httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        trust_env=False,
        headers=dict(headers or {}),
    )
