"""Scanners against a fake client. No network."""

from __future__ import annotations

from anthropic_radar.client import RadarClient
from anthropic_radar.scanners.api_keys import scan_api_keys
from anthropic_radar.scanners.org import scan_organization, scan_users
from anthropic_radar.scanners.usage import scan_cost, scan_usage


def test_scanners_without_an_admin_key_are_empty() -> None:
    client = RadarClient(api_key="sk-ant-api")
    assert scan_organization(client) is None
    assert scan_users(client) == []
    assert scan_api_keys(client) == []
    assert scan_usage(client) == []
    assert scan_cost(client) == []


def test_usage_and_cost_parse_admin_payloads() -> None:
    client = RadarClient(admin_key="sk-ant-admin")

    def get(path: str, params: dict | None = None, admin: bool = True) -> dict:
        if path.endswith("/usage_report/messages"):
            return {
                "data": [
                    {
                        "starting_at": "2026-01-01T00:00:00Z",
                        "results": [
                            {
                                "model": "claude-sonnet",
                                "workspace_id": "wrkspc_1",
                                "api_key_id": "key_1",
                                "service_tier": "standard",
                                "uncached_input_tokens": 10,
                                "output_tokens": 4,
                                "cache_read_input_tokens": 1,
                            }
                        ],
                    }
                ],
                "has_more": False,
            }
        if path.endswith("/cost_report"):
            return {
                "data": [
                    {
                        "starting_at": "2026-01-01T00:00:00Z",
                        "results": [
                            {
                                "amount": "250",
                                "currency": "USD",
                                "description": "tokens",
                                "model": "claude-sonnet",
                                "workspace_id": "wrkspc_1",
                            }
                        ],
                    }
                ],
                "has_more": False,
            }
        raise AssertionError(path)

    client.get = get  # type: ignore[method-assign]

    usage = scan_usage(client, lookback_days=1)
    assert usage[0].model == "claude-sonnet"
    assert usage[0].total_tokens == 15

    cost = scan_cost(client, lookback_days=1)
    assert cost[0].amount_usd == 2.5
    assert cost[0].description == "tokens"


def test_api_key_scan_can_scope_a_workspace() -> None:
    client = RadarClient(admin_key="sk-ant-admin")
    seen: dict[str, object] = {}

    def paginate(path: str, params: dict | None = None, admin: bool = True):
        seen["path"] = path
        seen["params"] = params
        yield {
            "id": "key_1",
            "name": "worker",
            "workspace_id": "wrkspc_9",
            "status": "active",
            "created_by": {"id": "user_1"},
            "partial_key_hint": "sk-ant-api03-ab",
        }

    client.paginate = paginate  # type: ignore[method-assign]
    keys = scan_api_keys(client, workspace_id="wrkspc_9")
    assert keys[0].created_by == "user_1"
    assert seen["params"] == {"status": "active", "workspace_id": "wrkspc_9"}
