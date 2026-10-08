"""Scanners against a fake client. No network."""

from __future__ import annotations

from datetime import datetime, timezone
from itertools import pairwise

from anthropic_radar.client import RadarClient
from anthropic_radar.scanners.api_keys import scan_api_keys
from anthropic_radar.scanners.org import scan_organization, scan_users
from anthropic_radar.scanners.usage import _daily_windows, scan_cost, scan_usage


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


def test_daily_windows_include_september_on_an_october_run() -> None:
    """30 days ending 9 October starts 9 September, and must be two requests."""
    now = datetime(2026, 10, 8, 18, 30, tzinfo=timezone.utc)
    assert _daily_windows(30, now=now) == [
        ("2026-09-09T00:00:00Z", "2026-10-01T00:00:00Z", 22),
        ("2026-10-01T00:00:00Z", "2026-10-09T00:00:00Z", 8),
    ]


def test_daily_windows_cover_a_lookback_longer_than_one_month() -> None:
    now = datetime(2026, 10, 8, 18, 30, tzinfo=timezone.utc)
    windows = _daily_windows(40, now=now)
    assert [window[0] for window in windows] == [
        "2026-08-30T00:00:00Z",
        "2026-09-01T00:00:00Z",
        "2026-10-01T00:00:00Z",
    ]
    assert windows[-1][1] == "2026-10-09T00:00:00Z"
    assert all(prev[1] == nxt[0] for prev, nxt in pairwise(windows))
    assert all(1 <= limit <= 31 for _, _, limit in windows)


def test_daily_windows_cross_the_year_boundary() -> None:
    now = datetime(2027, 1, 5, 12, tzinfo=timezone.utc)
    assert _daily_windows(30, now=now) == [
        ("2026-12-07T00:00:00Z", "2027-01-01T00:00:00Z", 25),
        ("2027-01-01T00:00:00Z", "2027-01-06T00:00:00Z", 5),
    ]


def test_usage_scan_keeps_every_month_in_the_lookback(monkeypatch) -> None:
    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 8, 18, 30, tzinfo=tz)

    monkeypatch.setattr("anthropic_radar.scanners.usage.datetime", _Frozen)
    client = RadarClient(admin_key="sk-ant-admin")
    seen: list[dict[str, object]] = []

    def get(path: str, params: dict | None = None, admin: bool = True) -> dict:
        query = dict(params or {})
        seen.append(query)
        start = str(query["starting_at"])
        if query.get("page") == "sept-page-2":
            return {
                "data": [
                    {
                        "starting_at": "2026-09-16T00:00:00Z",
                        "results": [{"model": "claude-haiku", "uncached_input_tokens": 2}],
                    }
                ],
                "has_more": False,
            }
        if start.startswith("2026-09"):
            return {
                "data": [
                    {
                        "starting_at": "2026-09-09T00:00:00Z",
                        "results": [{"model": "claude-sonnet", "uncached_input_tokens": 5}],
                    }
                ],
                "has_more": True,
                "next_page": "sept-page-2",
            }
        return {
            "data": [
                {
                    "starting_at": "2026-10-01T00:00:00Z",
                    "results": [{"model": "claude-opus", "uncached_input_tokens": 9}],
                }
            ],
            "has_more": False,
        }

    client.get = get  # type: ignore[method-assign]
    usage = scan_usage(client, lookback_days=30)
    months = {bucket.starting_at.month for bucket in usage if bucket.starting_at}
    assert months == {9, 10}
    assert [bucket.model for bucket in usage] == ["claude-sonnet", "claude-haiku", "claude-opus"]
    september = [query for query in seen if str(query["starting_at"]).startswith("2026-09")]
    assert september[0]["limit"] == 22
    assert september[1]["page"] == "sept-page-2"
    assert seen[-1]["ending_at"] == "2026-10-09T00:00:00Z"


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
