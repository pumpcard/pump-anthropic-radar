"""Scanner for org-wide API keys."""

from __future__ import annotations

from anthropic_radar.client import RadarClient
from anthropic_radar.models.base import ApiKey


def scan_api_keys(client: RadarClient, workspace_id: str | None = None) -> list[ApiKey]:
    if not client.has_admin_access:
        return []
    params: dict[str, str] = {"status": "active"}
    if workspace_id:
        params["workspace_id"] = workspace_id
    out = []
    for item in client.paginate("/v1/organizations/api_keys", params=params):
        created_by = item.get("created_by") or {}
        out.append(
            ApiKey(
                id=item["id"],
                name=item.get("name"),
                workspace_id=item.get("workspace_id"),
                status=item.get("status"),
                created_at=item.get("created_at"),
                created_by=created_by.get("id") if isinstance(created_by, dict) else None,
                partial_key_hint=item.get("partial_key_hint"),
            )
        )
    return out
