"""Per-resource CSV export."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from anthropic_radar.models.base import RunResult

_TABLES = [
    "users",
    "invites",
    "workspaces",
    "api_keys",
    "usage",
    "claude_code_usage",
    "cost",
    "relationships",
    "findings",
]


def _fields(item: BaseModel) -> list[str]:
    csv_fields = getattr(type(item), "csv_fields", None)
    if callable(csv_fields):
        return list(csv_fields())
    return list(item.model_dump().keys())


def _row(item: BaseModel) -> dict[str, Any]:
    csv_row = getattr(item, "csv_row", None)
    if callable(csv_row):
        raw = csv_row()
    else:
        raw = item.model_dump(mode="json")
    return {key: ("" if value is None else value) for key, value in raw.items()}


def export_csv(result: RunResult, out_dir: str | Path) -> list[Path]:
    destination = Path(out_dir)
    destination.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for table in _TABLES:
        rows = getattr(result, table)
        if not rows:
            continue
        path = destination / f"{table}.csv"
        fieldnames = _fields(rows[0])
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow(_row(row))
        written.append(path)
    return written
