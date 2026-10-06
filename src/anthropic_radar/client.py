"""RadarClient: auth and low-level HTTP for the Anthropic Admin API."""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import requests

API_BASE = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"


class AnthropicRadarError(RuntimeError):
    pass


class RadarClient:
    """Thin wrapper around the Anthropic Admin API.

    Two auth modes, mirroring the rest of the Radar suite:

    * ``api_key`` (``sk-ant-api03-...``) — standard key. There is no
      per-project usage surface for Claude the way there is for OpenAI
      projects, so org tables stay empty.
    * ``admin_key`` (``sk-ant-admin01-...``) — org Admin API key. Unlocks
      users, invites, workspaces, API keys, usage, cost, and Claude Code
      usage. Only organization admins can provision one.
    """

    def __init__(
        self,
        api_key: str | None = None,
        admin_key: str | None = None,
        base_url: str = API_BASE,
        timeout: float = 30.0,
    ) -> None:
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.admin_key = admin_key or os.environ.get("ANTHROPIC_ADMIN_KEY")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    @property
    def has_admin_access(self) -> bool:
        return bool(self.admin_key)

    def _headers(self, admin: bool) -> dict[str, str]:
        key = self.admin_key if admin else (self.api_key or self.admin_key)
        if not key:
            raise AnthropicRadarError(
                "No credentials found. Set ANTHROPIC_API_KEY and/or "
                "ANTHROPIC_ADMIN_KEY, or pass api_key=/admin_key= explicitly."
            )
        return {
            "x-api-key": key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }

    def get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        admin: bool = True,
    ) -> dict[str, Any]:
        if admin and not self.has_admin_access:
            raise AnthropicRadarError(
                f"{path} requires an Admin API key (sk-ant-admin01-...). "
                "Only org admins can provision one, via Console > Settings > Admin Keys."
            )
        resp = requests.get(
            f"{self.base_url}{path}",
            headers=self._headers(admin=admin),
            params=params,
            timeout=self.timeout,
        )
        if resp.status_code >= 400:
            raise AnthropicRadarError(f"GET {path} failed [{resp.status_code}]: {resp.text[:500]}")
        return resp.json()

    def paginate(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        admin: bool = True,
    ) -> Iterator[dict[str, Any]]:
        """Yield every item across an Admin API list endpoint's pages."""
        params = dict(params or {})
        while True:
            page = self.get(path, params=params, admin=admin)
            yield from page.get("data", [])
            if not page.get("has_more"):
                break
            after = page.get("last_id") or page.get("next_page")
            if not after:
                break
            if "next_page" in page:
                params["page"] = after
            else:
                params["after_id"] = after
