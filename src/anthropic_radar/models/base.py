"""Pydantic v2 models for all Anthropic org resource types."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, computed_field


class Severity(str, Enum):
    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class RelationshipKind(str, Enum):
    AWS = "AWS"
    GCP = "GCP"
    AZURE = "AZURE"
    DATABASE = "DATABASE"
    SLACK = "SLACK"
    EMAIL = "EMAIL"
    WEBHOOK = "WEBHOOK"


class Organization(BaseModel):
    id: str
    name: str | None = None


class User(BaseModel):
    id: str
    email: str | None = None
    name: str | None = None
    role: str | None = None
    added_at: datetime | None = None


class Invite(BaseModel):
    id: str
    email: str | None = None
    role: str | None = None
    status: str | None = None
    invited_at: datetime | None = None
    expires_at: datetime | None = None


class Workspace(BaseModel):
    id: str
    name: str | None = None
    archived_at: datetime | None = None
    created_at: datetime | None = None
    member_count: int = 0
    active_key_count: int = 0


class ApiKey(BaseModel):
    id: str
    name: str | None = None
    workspace_id: str | None = None
    status: str | None = None  # active | inactive | archived
    created_at: datetime | None = None
    created_by: str | None = None
    partial_key_hint: str | None = None
    tokens_in_lookback: int = 0
    last_used_at: datetime | None = None


class UsageBucket(BaseModel):
    starting_at: datetime | None = None
    model: str | None = None
    workspace_id: str | None = None
    api_key_id: str | None = None
    service_tier: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0

    @computed_field
    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_creation_tokens
        )

    @classmethod
    def csv_fields(cls) -> list[str]:
        # total_tokens is a property, so it is not in model_fields — add it.
        return [*cls.model_fields, "total_tokens"]

    def csv_row(self) -> dict[str, Any]:
        row = self.model_dump(mode="json")
        row["total_tokens"] = self.total_tokens
        return row


class ClaudeCodeUsageBucket(BaseModel):
    starting_at: datetime | None = None
    user_id: str | None = None
    model: str | None = None
    sessions: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    lines_added: int = 0
    lines_removed: int = 0


class CostBucket(BaseModel):
    starting_at: datetime | None = None
    workspace_id: str | None = None
    model: str | None = None
    description: str | None = None
    # Major currency units (dollars). The Admin API reports cents.
    amount_usd: float = 0.0


class ServiceRelationship(BaseModel):
    source_kind: str  # "workspace" | "api_key"
    source_id: str
    source_label: str | None = None
    kind: RelationshipKind
    signal: str


class Finding(BaseModel):
    rule_id: str
    severity: Severity
    resource_kind: str
    resource_id: str
    message: str


class RunResult(BaseModel):
    organization: Organization | None = None
    users: list[User] = Field(default_factory=list)
    invites: list[Invite] = Field(default_factory=list)
    workspaces: list[Workspace] = Field(default_factory=list)
    api_keys: list[ApiKey] = Field(default_factory=list)
    usage: list[UsageBucket] = Field(default_factory=list)
    claude_code_usage: list[ClaudeCodeUsageBucket] = Field(default_factory=list)
    cost: list[CostBucket] = Field(default_factory=list)
    relationships: list[ServiceRelationship] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    admin_scan: bool = False

    def summary(self) -> str:
        org = self.organization.name if self.organization else "n/a (no admin key)"
        pending = len([i for i in self.invites if (i.status or "").lower() == "pending"])
        lines = [
            "Anthropic Radar scan summary",
            f"  organization:        {org}",
            f"  users:               {len(self.users)}",
            f"  pending invites:     {pending}",
            f"  workspaces:          {len(self.workspaces)}",
            f"  api keys:            {len(self.api_keys)}",
            f"  usage buckets:       {len(self.usage)}",
            f"  claude code buckets: {len(self.claude_code_usage)}",
            f"  cost buckets:        {len(self.cost)}",
            f"  relationships:       {len(self.relationships)}",
            f"  findings:            {len(self.findings)}",
        ]
        by_sev: dict[str, int] = {}
        for finding in self.findings:
            by_sev[finding.severity.value] = by_sev.get(finding.severity.value, 0) + 1
        if by_sev:
            rendered = ", ".join(f"{key}={value}" for key, value in sorted(by_sev.items()))
            lines.append("  findings by severity: " + rendered)
        return "\n".join(lines)

    def export_csv(self, out_dir: str | Path) -> list[Path]:
        from anthropic_radar.exporters.csv import export_csv

        return export_csv(self, out_dir)

    def export_drawio(self, out_path: str | Path) -> Path:
        from anthropic_radar.exporters.drawio import export_drawio

        return export_drawio(self, out_path)
