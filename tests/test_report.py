"""Organization cost report rows and CSV writing. No network."""

from __future__ import annotations

import csv

import pytest

from anthropic_radar.client import RadarClient
from anthropic_radar.scanners.report import (
    COSTS_PATH,
    REPORT_FIELDS,
    CostReport,
    ReportError,
    fetch_cost_report,
    write_report_csv,
)


def test_fetch_cost_report_converts_cents_and_drops_zeros() -> None:
    client = RadarClient(admin_key="sk-ant-admin")
    seen: list[dict[str, object]] = []

    def get(path: str, params: dict[str, object] | None = None, admin: bool = True) -> dict:
        seen.append({"path": path, **dict(params or {})})
        page = (params or {}).get("page")
        if page == "page-2":
            return {
                "data": [
                    {
                        "starting_at": "2026-01-02T00:00:00Z",
                        "ending_at": "2026-01-03T00:00:00Z",
                        "results": [
                            {
                                "amount": "200",
                                "currency": "USD",
                                "description": "embeddings",
                                "workspace_id": "wrkspc_2",
                            }
                        ],
                    }
                ],
                "has_more": False,
            }
        return {
            "data": [
                {
                    "starting_at": "2026-01-01T00:00:00Z",
                    "ending_at": "2026-01-02T00:00:00Z",
                    "results": [
                        {
                            "amount": "123.45",
                            "currency": "USD",
                            "description": "Claude Sonnet 4 Usage - Input Tokens",
                            "workspace_id": "wrkspc_1",
                        },
                        {
                            "amount": "0",
                            "currency": "USD",
                            "description": "zero",
                            "workspace_id": "wrkspc_1",
                        },
                        {
                            "amount": "25",
                            "currency": "usd",
                            "model": "claude-haiku",
                            "workspace_id": None,
                        },
                    ],
                }
            ],
            "has_more": True,
            "next_page": "page-2",
        }

    client.get = get  # type: ignore[method-assign]

    report = fetch_cost_report(client, lookback_days=7, workspace_id="wrkspc_9")

    assert isinstance(report, CostReport)
    assert report.truncated is False
    assert report.rows == [
        {
            "Date": "2026-01-01",
            "ProjectID": "wrkspc_1",
            "LineItem": "Claude Sonnet 4 Usage - Input Tokens",
            "Amount": "1.234500",
            "Currency": "USD",
        },
        {
            "Date": "2026-01-01",
            "ProjectID": "-",
            "LineItem": "claude-haiku",
            "Amount": "0.250000",
            "Currency": "USD",
        },
        {
            "Date": "2026-01-02",
            "ProjectID": "wrkspc_2",
            "LineItem": "embeddings",
            "Amount": "2.000000",
            "Currency": "USD",
        },
    ]
    assert seen[0]["path"] == COSTS_PATH
    assert seen[0]["bucket_width"] == "1d"
    assert seen[0]["group_by[]"] == ["workspace_id", "description"]
    assert seen[0]["workspace_ids[]"] == ["wrkspc_9"]
    assert seen[1]["page"] == "page-2"


def test_fetch_requires_an_admin_key() -> None:
    client = RadarClient(api_key="sk-ant-api")
    with pytest.raises(ReportError, match="ANTHROPIC_ADMIN_KEY"):
        fetch_cost_report(client)


def test_fetch_rejects_an_empty_window() -> None:
    client = RadarClient(admin_key="sk-ant-admin")

    def get(path: str, params: dict[str, object] | None = None, admin: bool = True) -> dict:
        return {
            "data": [
                {
                    "starting_at": "2026-01-01T00:00:00Z",
                    "results": [{"amount": "0", "currency": "USD"}],
                }
            ],
            "has_more": False,
        }

    client.get = get  # type: ignore[method-assign]
    with pytest.raises(ReportError, match="no cost rows"):
        fetch_cost_report(client)


def test_write_report_csv_uses_the_shared_columns(tmp_path) -> None:
    destination = tmp_path / "nested" / "report.csv"
    written = write_report_csv(
        destination,
        [
            {
                "Date": "2026-01-01",
                "ProjectID": "wrkspc_1",
                "LineItem": "tokens",
                "Amount": "1.000000",
                "Currency": "USD",
                "extra": "ignored",
            }
        ],
    )
    assert written == destination
    with destination.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert list(rows[0]) == REPORT_FIELDS
    assert rows[0]["Amount"] == "1.000000"
