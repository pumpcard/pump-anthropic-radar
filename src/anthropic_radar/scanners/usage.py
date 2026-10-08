"""Scanners for the Usage & Cost Admin API."""

from __future__ import annotations

import csv
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from anthropic_radar.client import RadarClient
from anthropic_radar.models.base import ClaudeCodeUsageBucket, CostBucket, UsageBucket

# A 1d usage page holds at most this many buckets. The API default is 7.
_DAILY_PAGE_LIMIT = 31


def _stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _window(lookback_days: int) -> tuple[str, str]:
    end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(days=lookback_days)
    return _stamp(start), _stamp(end)


def _day_bounds(lookback_days: int, *, now: datetime | None = None) -> tuple[datetime, datetime]:
    """Inclusive start and exclusive end, snapped to UTC midnights.

    ``end`` is the next UTC midnight, so the current day is inside the window.
    """
    current = now or datetime.now(timezone.utc)
    end = current.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    end = end + timedelta(days=1)
    return end - timedelta(days=lookback_days), end


def _next_month(day: datetime) -> datetime:
    if day.month == 12:
        return day.replace(year=day.year + 1, month=1, day=1)
    return day.replace(month=day.month + 1, day=1)


def _daily_windows(
    lookback_days: int, *, now: datetime | None = None
) -> list[tuple[str, str, int]]:
    """Split a daily lookback into requests the usage API will fill completely.

    A ``1d`` response holds at most 31 buckets, and defaults to 7 when ``limit``
    is omitted. A range that crosses a UTC month boundary returns only the
    month of ``ending_at``, so each month is requested on its own.
    """
    start, end = _day_bounds(lookback_days, now=now)
    windows: list[tuple[str, str, int]] = []
    cursor = start
    while cursor < end:
        piece_end = min(
            _next_month(cursor.replace(day=1)),
            end,
            cursor + timedelta(days=_DAILY_PAGE_LIMIT),
        )
        windows.append((_stamp(cursor), _stamp(piece_end), (piece_end - cursor).days))
        cursor = piece_end
    return windows


def _pages(client: RadarClient, path: str, params: dict[str, Any]) -> Iterator[dict[str, Any]]:
    params = dict(params)
    while True:
        page = client.get(path, params=params)
        yield from page.get("data", [])
        if not page.get("has_more"):
            break
        params["page"] = page.get("next_page")
        if not params["page"]:
            break


def _actor_user_id(result: dict[str, Any]) -> str | None:
    actor = result.get("actor")
    if isinstance(actor, dict):
        user_id = actor.get("user_id")
        if isinstance(user_id, str):
            return user_id
    user_id = result.get("user_id")
    return user_id if isinstance(user_id, str) else None


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _usage_row(bucket: dict[str, Any], result: dict[str, Any]) -> UsageBucket:
    uncached = result.get("uncached_input_tokens", result.get("input_tokens", 0))
    return UsageBucket(
        starting_at=bucket.get("starting_at"),
        model=result.get("model"),
        workspace_id=result.get("workspace_id"),
        api_key_id=result.get("api_key_id"),
        service_tier=result.get("service_tier"),
        input_tokens=_int(uncached),
        output_tokens=_int(result.get("output_tokens")),
        cache_read_tokens=_int(result.get("cache_read_input_tokens")),
        cache_creation_tokens=_int(result.get("cache_creation_input_tokens")),
    )


def scan_usage(
    client: RadarClient,
    lookback_days: int = 7,
    bucket_width: str = "1d",
) -> list[UsageBucket]:
    if not client.has_admin_access:
        return []
    if bucket_width == "1d":
        windows = _daily_windows(lookback_days)
    else:
        starting_at, ending_at = _window(lookback_days)
        windows = [(starting_at, ending_at, _DAILY_PAGE_LIMIT)]
    out: list[UsageBucket] = []
    for starting_at, ending_at, limit in windows:
        for bucket in _pages(
            client,
            "/v1/organizations/usage_report/messages",
            {
                "starting_at": starting_at,
                "ending_at": ending_at,
                "bucket_width": bucket_width,
                "limit": limit,
                "group_by[]": ["model", "workspace_id", "api_key_id", "service_tier"],
            },
        ):
            for result in bucket.get("results", [bucket]):
                out.append(_usage_row(bucket, result))
    return out


def scan_claude_code_usage(
    client: RadarClient,
    lookback_days: int = 7,
) -> list[ClaudeCodeUsageBucket]:
    if not client.has_admin_access:
        return []
    out: list[ClaudeCodeUsageBucket] = []
    try:
        for starting_at, ending_at, limit in _daily_windows(lookback_days):
            for bucket in _pages(
                client,
                "/v1/organizations/usage_report/claude_code",
                {
                    "starting_at": starting_at,
                    "ending_at": ending_at,
                    "bucket_width": "1d",
                    "limit": limit,
                },
            ):
                for result in bucket.get("results", [bucket]):
                    added = result.get(
                        "lines_of_code_added", result.get("code_edit_tool_lines_added", 0)
                    )
                    removed = result.get(
                        "lines_of_code_removed",
                        result.get("code_edit_tool_lines_removed", 0),
                    )
                    out.append(
                        ClaudeCodeUsageBucket(
                            starting_at=bucket.get("starting_at"),
                            user_id=_actor_user_id(result),
                            model=result.get("model"),
                            sessions=_int(result.get("num_sessions")),
                            input_tokens=_int(result.get("input_tokens")),
                            output_tokens=_int(result.get("output_tokens")),
                            lines_added=_int(added),
                            lines_removed=_int(removed),
                        )
                    )
    except Exception:
        # Claude Code usage may be unavailable to orgs without seats provisioned.
        return out
    return out


def _amount_usd(result: dict[str, Any]) -> float:
    """Return major units (dollars). The Admin API reports lowest units (cents)."""
    raw = result.get("amount")
    if isinstance(raw, dict):
        raw = raw.get("value")
    if raw is None:
        try:
            return float(result.get("amount_usd") or 0)
        except (TypeError, ValueError):
            return 0.0
    try:
        return float(Decimal(str(raw)) / Decimal(100))
    except (InvalidOperation, ValueError):
        return 0.0


def scan_cost(client: RadarClient, lookback_days: int = 30) -> list[CostBucket]:
    if not client.has_admin_access:
        return []
    starting_at, ending_at = _window(lookback_days)
    out: list[CostBucket] = []
    for bucket in _pages(
        client,
        "/v1/organizations/cost_report",
        {
            "starting_at": starting_at,
            "ending_at": ending_at,
            "bucket_width": "1d",
            "group_by[]": ["workspace_id", "description"],
        },
    ):
        for result in bucket.get("results", [bucket]):
            out.append(
                CostBucket(
                    starting_at=bucket.get("starting_at"),
                    workspace_id=result.get("workspace_id"),
                    model=result.get("model"),
                    description=result.get("description"),
                    amount_usd=_amount_usd(result),
                )
            )
    return out


def write_usage_csv(path: str | Path, rows: list[UsageBucket]) -> Path:
    """Write token usage for the Pump ``inventory`` upload. Returns the path written."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=UsageBucket.csv_fields(), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(row.csv_row() for row in rows)
    return destination
