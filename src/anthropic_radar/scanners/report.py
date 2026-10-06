"""Cost report CSV for Pump onboarding.

Pump's estimate flow (the same ``/api/v1/estimate/radar/urls`` exchange
pump-aws-radar uses) takes a billing file and an inventory file. This module
builds the billing file from the Anthropic Admin cost report. Token usage,
uploaded as role ``inventory``, is written by
:func:`anthropic_radar.scanners.usage.write_usage_csv`.

Anthropic reports ``amount`` in lowest currency units. ``"123.45"`` USD is
``$1.23``. Rows here are major units, six decimal places, so the CSV matches
the dollar amounts Pump already ingests from the other radars. Zero-cost
buckets are omitted. ``ProjectID`` holds the workspace id (or ``-`` for the
org default workspace, which the API returns as null).
"""

from __future__ import annotations

import csv
import warnings
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from anthropic_radar.client import AnthropicRadarError, RadarClient

COSTS_PATH = "/v1/organizations/cost_report"

#: Column order shared with the other Pump radars. ProjectID is the workspace.
REPORT_FIELDS = ["Date", "ProjectID", "LineItem", "Amount", "Currency"]

#: Stop after this many pages, so a wide lookback cannot loop unbounded.
MAX_PAGES = 50

_CENTS = Decimal(100)
_QUANT = Decimal("0.000001")


class ReportError(AnthropicRadarError):
    """The cost report cannot be built or is empty."""


@dataclass(frozen=True)
class CostReport:
    rows: list[dict[str, str]]
    truncated: bool = False


def _window(lookback_days: int) -> tuple[str, str]:
    """UTC day bounds. ``ending_at`` is exclusive and includes the current day."""
    end = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    end = end + timedelta(days=1)
    start = end - timedelta(days=lookback_days)
    return (
        start.isoformat().replace("+00:00", "Z"),
        end.isoformat().replace("+00:00", "Z"),
    )


def _day(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(int(value), tz=timezone.utc).date().isoformat()
        except (OSError, OverflowError, ValueError):
            return None
    text = str(value)
    if text.isdigit():
        try:
            return datetime.fromtimestamp(int(text), tz=timezone.utc).date().isoformat()
        except (OSError, OverflowError, ValueError):
            return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return None


def _amount(result: dict[str, Any]) -> tuple[str, str] | None:
    """Return ``(dollars, currency)`` or None when the bucket is zero or unusable.

    String and number amounts are Anthropic minor units (cents) and are divided
    by 100. A dict ``{"value", "currency"}`` is treated the same way.
    """
    raw = result.get("amount")
    currency = result.get("currency")
    if isinstance(raw, dict):
        currency = raw.get("currency") or currency
        raw = raw.get("value")
    if raw is None:
        return None
    try:
        cents = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None
    if cents == 0:
        return None
    dollars = (cents / _CENTS).quantize(_QUANT, rounding=ROUND_HALF_UP)
    code = str(currency or "usd").upper()
    return f"{dollars:.6f}", code


def _row(bucket_start: Any, result: dict[str, Any]) -> dict[str, str] | None:
    parsed = _amount(result)
    if parsed is None:
        return None
    day = _day(bucket_start)
    if day is None:
        return None
    amount, currency = parsed
    workspace = result.get("workspace_id") or "-"
    line_item = result.get("description") or result.get("model") or "-"
    return {
        "Date": day,
        "ProjectID": str(workspace),
        "LineItem": str(line_item),
        "Amount": amount,
        "Currency": currency,
    }


def fetch_cost_report(
    client: RadarClient,
    *,
    lookback_days: int = 30,
    workspace_id: str | None = None,
) -> CostReport:
    """Pull daily org costs and shape them into report rows.

    Requires an admin key. Zero-cost buckets are dropped. An empty window
    raises :class:`ReportError` so the caller does not upload a header-only file.
    """
    if not client.has_admin_access:
        raise ReportError(
            "The Pump report needs org cost data. Pass --admin-key or set ANTHROPIC_ADMIN_KEY."
        )

    starting_at, ending_at = _window(lookback_days)
    params: dict[str, Any] = {
        "starting_at": starting_at,
        "ending_at": ending_at,
        "bucket_width": "1d",
        "group_by[]": ["workspace_id", "description"],
    }
    if workspace_id:
        params["workspace_ids[]"] = [workspace_id]

    rows: list[dict[str, str]] = []
    pages = 0
    truncated = False

    while pages < MAX_PAGES:
        try:
            payload = client.get(COSTS_PATH, params)
        except AnthropicRadarError as exc:
            if pages == 0:
                raise
            warnings.warn(
                f"Cost report pagination stopped after {pages} page(s): {exc}",
                RuntimeWarning,
                stacklevel=2,
            )
            truncated = True
            break

        for bucket in payload.get("data") or []:
            bucket_start = bucket.get("starting_at")
            for result in bucket.get("results") or []:
                if not isinstance(result, dict):
                    continue
                row = _row(bucket_start, result)
                if row is not None:
                    rows.append(row)

        pages += 1
        if not payload.get("has_more"):
            break
        next_page = payload.get("next_page")
        if not next_page:
            break
        params["page"] = next_page
    else:
        truncated = True
        warnings.warn(
            "Cost report hit the page limit and may be incomplete.",
            RuntimeWarning,
            stacklevel=2,
        )

    if not rows:
        raise ReportError(
            "Anthropic returned no cost rows for this window, so there is nothing to upload."
        )

    return CostReport(rows=rows, truncated=truncated)


def write_report_csv(path: str | Path, rows: list[dict[str, str]]) -> Path:
    """Write ``report.csv``. Returns the path written."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=REPORT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return destination
