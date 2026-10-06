"""Findings rules against an in-memory scan."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from anthropic_radar.findings import FindingEngine
from anthropic_radar.models.base import (
    ApiKey,
    ClaudeCodeUsageBucket,
    Invite,
    Organization,
    RunResult,
    UsageBucket,
    Workspace,
)


def _ids(result: RunResult) -> set[str]:
    return {finding.rule_id for finding in FindingEngine().run(result)}


def test_idle_and_unscoped_keys() -> None:
    result = RunResult(
        api_keys=[
            ApiKey(id="key_idle", name="idle", status="active", workspace_id="wrkspc_1"),
            ApiKey(id="key_used", name="used", status="active", workspace_id="wrkspc_1"),
            ApiKey(id="key_default", name="default", status="active"),
        ],
        usage=[UsageBucket(model="claude", api_key_id="key_used", input_tokens=10)],
        workspaces=[Workspace(id="wrkspc_1", name="prod")],
    )
    rules = _ids(result)
    assert "KEY_001" in rules
    assert "KEY_002" in rules
    assert "WS_001" not in rules


def test_empty_workspace_and_stale_invite() -> None:
    invited = datetime.now(timezone.utc) - timedelta(days=45)
    result = RunResult(
        workspaces=[Workspace(id="wrkspc_empty", name="sandbox")],
        invites=[Invite(id="inv_1", email="a@b.c", status="pending", invited_at=invited)],
    )
    assert _ids(result) == {"WS_001", "INV_001"}


def test_token_priority_and_claude_code_rules() -> None:
    result = RunResult(
        organization=Organization(id="org_1", name="Acme"),
        usage=[
            UsageBucket(model="claude-opus", input_tokens=10_000_001, service_tier="priority"),
        ],
        claude_code_usage=[ClaudeCodeUsageBucket(user_id="user_1", sessions=2)],
    )
    assert _ids(result) == {"USAGE_001", "COST_001", "CC_001"}
