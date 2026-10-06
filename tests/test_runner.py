"""Runner wires scanners, relationships, and findings. requests.get is stubbed."""

from __future__ import annotations

from anthropic_radar.client import RadarClient
from anthropic_radar.runner import RunConfig, Runner


class _Resp:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.status_code = 200
        self.text = ""

    def json(self) -> dict:
        return self._payload


def test_run_sync_collects_inventory(monkeypatch) -> None:
    def fake_get(
        url: str, headers: dict | None = None, params: dict | None = None, timeout: float = 0
    ):
        path = url.split("api.anthropic.com", 1)[-1]
        if path == "/v1/organizations/me":
            return _Resp({"id": "org_1", "name": "Acme"})
        if path == "/v1/organizations/users":
            return _Resp(
                {"data": [{"id": "user_1", "email": "a@b.c", "role": "admin"}], "has_more": False}
            )
        if path == "/v1/organizations/invites":
            return _Resp({"data": [], "has_more": False})
        if path == "/v1/organizations/workspaces":
            return _Resp({"data": [{"id": "wrkspc_1", "name": "prod-lambda"}], "has_more": False})
        if path.endswith("/members"):
            return _Resp({"data": [{"id": "user_1"}], "has_more": False})
        if path == "/v1/organizations/api_keys":
            return _Resp(
                {
                    "data": [
                        {
                            "id": "key_1",
                            "name": "slack-bot",
                            "workspace_id": "wrkspc_1",
                            "status": "active",
                            "created_by": {"id": "user_1"},
                        }
                    ],
                    "has_more": False,
                }
            )
        if path.endswith("/usage_report/messages"):
            return _Resp(
                {
                    "data": [
                        {
                            "starting_at": "2026-01-01T00:00:00Z",
                            "results": [
                                {
                                    "model": "claude-sonnet",
                                    "workspace_id": "wrkspc_1",
                                    "api_key_id": "key_1",
                                    "uncached_input_tokens": 8,
                                    "output_tokens": 2,
                                }
                            ],
                        }
                    ],
                    "has_more": False,
                }
            )
        if path.endswith("/usage_report/claude_code"):
            return _Resp({"data": [], "has_more": False})
        if path.endswith("/cost_report"):
            return _Resp(
                {
                    "data": [
                        {
                            "starting_at": "2026-01-01T00:00:00Z",
                            "results": [
                                {
                                    "amount": "100",
                                    "currency": "USD",
                                    "description": "tokens",
                                    "workspace_id": "wrkspc_1",
                                }
                            ],
                        }
                    ],
                    "has_more": False,
                }
            )
        raise AssertionError(path)

    monkeypatch.setattr("anthropic_radar.client.requests.get", fake_get)
    result = Runner.run_sync(
        RadarClient(admin_key="sk-ant-admin"),
        RunConfig(workspace_id="wrkspc_1", usage_lookback_days=7, cost_lookback_days=30),
    )

    assert result.organization is not None
    assert result.organization.name == "Acme"
    assert result.workspaces[0].member_count == 1
    assert result.api_keys[0].name == "slack-bot"
    assert result.usage[0].total_tokens == 10
    assert result.cost[0].amount_usd == 1.0
    assert any(rel.signal == "lambda" for rel in result.relationships)
    assert any(rel.signal == "slack" for rel in result.relationships)
    assert result.findings == []
