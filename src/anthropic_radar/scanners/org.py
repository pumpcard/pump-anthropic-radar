"""Scanners for organization identity, users, invites, and workspaces."""

from __future__ import annotations

from anthropic_radar.client import RadarClient
from anthropic_radar.models.base import Invite, Organization, User, Workspace


def scan_organization(client: RadarClient) -> Organization | None:
    if not client.has_admin_access:
        return None
    data = client.get("/v1/organizations/me")
    return Organization(id=data.get("id", "unknown"), name=data.get("name"))


def scan_users(client: RadarClient) -> list[User]:
    if not client.has_admin_access:
        return []
    out = []
    for item in client.paginate("/v1/organizations/users"):
        out.append(
            User(
                id=item["id"],
                email=item.get("email"),
                name=item.get("name"),
                role=item.get("role"),
                added_at=item.get("added_at"),
            )
        )
    return out


def scan_invites(client: RadarClient) -> list[Invite]:
    if not client.has_admin_access:
        return []
    out = []
    for item in client.paginate("/v1/organizations/invites"):
        out.append(
            Invite(
                id=item["id"],
                email=item.get("email"),
                role=item.get("role"),
                status=item.get("status"),
                invited_at=item.get("invited_at"),
                expires_at=item.get("expires_at"),
            )
        )
    return out


def scan_workspaces(client: RadarClient) -> list[Workspace]:
    if not client.has_admin_access:
        return []
    out = []
    for item in client.paginate(
        "/v1/organizations/workspaces",
        params={"include_archived": "false"},
    ):
        workspace = Workspace(
            id=item["id"],
            name=item.get("name"),
            archived_at=item.get("archived_at"),
            created_at=item.get("created_at"),
        )
        try:
            members = list(client.paginate(f"/v1/organizations/workspaces/{workspace.id}/members"))
            workspace.member_count = len(members)
        except Exception:
            pass
        out.append(workspace)
    return out
