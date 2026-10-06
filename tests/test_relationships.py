"""Workspace and API-key name signals."""

from anthropic_radar.models.base import ApiKey, RelationshipKind, Workspace
from anthropic_radar.relationships import detect_relationships


def test_detects_signals_in_workspace_and_key_names() -> None:
    rels = detect_relationships(
        [Workspace(id="wrkspc_1", name="prod-lambda-ingest")],
        [ApiKey(id="key_1", name="slack-bot", partial_key_hint="sk-ant...hook")],
    )
    kinds = {(rel.source_kind, rel.kind, rel.signal) for rel in rels}
    assert ("workspace", RelationshipKind.AWS, "lambda") in kinds
    assert ("api_key", RelationshipKind.SLACK, "slack") in kinds
