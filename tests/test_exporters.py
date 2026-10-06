"""CSV and draw.io export."""

from anthropic_radar.models.base import (
    ApiKey,
    Organization,
    RunResult,
    ServiceRelationship,
    RelationshipKind,
    Workspace,
)


def test_export_csv_and_drawio(tmp_path) -> None:
    result = RunResult(
        organization=Organization(id="org_1", name="Acme"),
        workspaces=[Workspace(id="wrkspc_1", name="prod-lambda")],
        api_keys=[ApiKey(id="key_1", name="slack-bot", workspace_id="wrkspc_1", status="active")],
        relationships=[
            ServiceRelationship(
                source_kind="workspace",
                source_id="wrkspc_1",
                source_label="prod-lambda",
                kind=RelationshipKind.AWS,
                signal="lambda",
            )
        ],
    )
    written = result.export_csv(tmp_path / "csv")
    names = {path.name for path in written}
    assert "workspaces.csv" in names
    assert "api_keys.csv" in names
    assert "slack-bot" in (tmp_path / "csv" / "api_keys.csv").read_text(encoding="utf-8")

    diagram = result.export_drawio(tmp_path / "arch.drawio")
    xml = diagram.read_text(encoding="utf-8")
    assert "Acme" in xml
    assert "prod-lambda" in xml
    assert "dashed=1" in xml
