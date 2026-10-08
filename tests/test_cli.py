"""CLI commands, with the runner stubbed out."""

from __future__ import annotations

import json
import sys
from typing import Any

import pytest
from typer.testing import CliRunner

from anthropic_radar.cli import app
from anthropic_radar.client import AnthropicRadarError, RadarClient
from anthropic_radar.models.base import (
    Finding,
    Organization,
    RunResult,
    Severity,
    UsageBucket,
    Workspace,
)
from anthropic_radar.runner import RunConfig

runner = CliRunner()


def _patch_run(monkeypatch: pytest.MonkeyPatch, result: RunResult | Exception) -> list[RunConfig]:
    seen: list[RunConfig] = []

    def fake_run(client: object, config: RunConfig | None = None) -> RunResult:
        assert config is not None
        seen.append(config)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr("anthropic_radar.cli.Runner.run_sync", fake_run)
    return seen


def _sample_result() -> RunResult:
    return RunResult(
        organization=Organization(id="org_1", name="Acme"),
        workspaces=[Workspace(id="wrkspc_1", name="prod-lambda")],
        usage=[
            UsageBucket(
                model="claude-sonnet",
                workspace_id="wrkspc_1",
                input_tokens=3,
                output_tokens=4,
            )
        ],
    )


def test_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "pump-anthropic-radar 0.0.2" in result.stdout


def test_run_rejects_an_unknown_output() -> None:
    result = runner.invoke(app, ["run", "--output", "xml"])
    assert result.exit_code == 2
    assert "--output" in result.stdout


def test_run_json_includes_the_scan_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _patch_run(monkeypatch, _sample_result())

    result = runner.invoke(
        app,
        [
            "run",
            "--admin-key",
            "sk-ant-admin",
            "--workspace",
            "wrkspc_1",
            "--lookback",
            "7",
            "-o",
            "json",
        ],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert "Waiting on Anthropic API" not in result.stdout
    assert "Waiting on Anthropic API" in result.stderr
    assert payload["organization"]["name"] == "Acme"
    assert payload["workspaces"][0]["id"] == "wrkspc_1"
    assert payload["usage"][0]["total_tokens"] == 7
    assert seen[0].workspace_id == "wrkspc_1"
    assert seen[0].usage_lookback_days == 7
    assert seen[0].cost_lookback_days == 30


def test_run_json_can_be_written_to_a_file(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    destination = tmp_path / "scan.json"
    _patch_run(monkeypatch, RunResult())

    result = runner.invoke(
        app,
        ["run", "--output", "json", "--out-file", str(destination)],
    )

    assert result.exit_code == 0, result.stdout
    assert "Wrote" in result.stdout
    assert json.loads(destination.read_text(encoding="utf-8"))["workspaces"] == []


def test_findings_command_prints_json(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_run(
        monkeypatch,
        RunResult(
            findings=[
                Finding(
                    rule_id="KEY_002",
                    severity=Severity.INFO,
                    resource_kind="api_key",
                    resource_id="apikey_1",
                    message="org default",
                )
            ]
        ),
    )

    result = runner.invoke(app, ["findings", "--admin-key", "sk-ant-admin", "--output", "json"])

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert "Waiting on Anthropic API" not in result.stdout
    assert "Waiting on Anthropic API" in result.stderr
    assert payload[0]["rule_id"] == "KEY_002"
    assert payload[0]["severity"] == "INFO"


def test_run_uploads_the_cost_report(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    _patch_run(monkeypatch, _sample_result())
    report_path = tmp_path / "report.csv"
    seen: dict[str, object] = {}

    def fake_fetch(client: object, *, lookback_days: int, workspace_id: str | None) -> object:
        from anthropic_radar.scanners.report import CostReport

        seen["lookback"] = lookback_days
        seen["workspace"] = workspace_id
        assert getattr(client, "has_admin_access", False)
        return CostReport(
            rows=[
                {
                    "Date": "2026-01-01",
                    "ProjectID": "wrkspc_1",
                    "LineItem": "Claude Sonnet Usage - Input Tokens",
                    "Amount": "1.500000",
                    "Currency": "USD",
                }
            ]
        )

    def fake_upload(api_base: str, token: str, files: dict[str, str]) -> None:
        seen["api_base"] = api_base
        seen["token"] = token
        seen["files"] = files

    monkeypatch.setattr("anthropic_radar.scanners.report.fetch_cost_report", fake_fetch)
    monkeypatch.setattr("anthropic_radar.upload.upload_csvs", fake_upload)

    result = runner.invoke(
        app,
        [
            "run",
            "--admin-key",
            "sk-ant-admin",
            "--workspace",
            "wrkspc_1",
            "--lookback",
            "7",
            "--upload-token",
            "tok",
            "--api-base",
            "http://localhost:8001",
            "--report-file",
            str(report_path),
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert seen["lookback"] == 30
    assert seen["workspace"] == "wrkspc_1"
    assert seen["api_base"] == "http://localhost:8001"
    assert seen["token"] == "tok"
    usage_path = report_path.with_name("usage.csv")
    assert seen["files"] == {"billing": str(report_path), "inventory": str(usage_path)}
    assert "Claude Sonnet Usage - Input Tokens" in report_path.read_text(encoding="utf-8")
    assert "claude-sonnet" in usage_path.read_text(encoding="utf-8")
    assert "on its way to Pump" in result.stdout


def test_run_uploads_usage_when_none_was_found(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    _patch_run(monkeypatch, RunResult())
    report_path = tmp_path / "report.csv"
    seen: dict[str, object] = {}

    def fake_fetch(client: object, *, lookback_days: int, workspace_id: str | None) -> object:
        from anthropic_radar.scanners.report import CostReport

        return CostReport(
            rows=[
                {
                    "Date": "2026-01-01",
                    "ProjectID": "wrkspc_1",
                    "LineItem": "tokens",
                    "Amount": "1.000000",
                    "Currency": "USD",
                }
            ]
        )

    def fake_upload(api_base: str, token: str, files: dict[str, str]) -> None:
        seen["files"] = files

    monkeypatch.setattr("anthropic_radar.scanners.report.fetch_cost_report", fake_fetch)
    monkeypatch.setattr("anthropic_radar.upload.upload_csvs", fake_upload)

    result = runner.invoke(
        app,
        [
            "run",
            "--admin-key",
            "sk-ant-admin",
            "--upload-token",
            "tok",
            "--report-file",
            str(report_path),
        ],
    )

    usage_path = report_path.with_name("usage.csv")
    assert result.exit_code == 0, result.stdout
    assert seen["files"] == {"billing": str(report_path), "inventory": str(usage_path)}
    assert usage_path.is_file()
    text = usage_path.read_text(encoding="utf-8")
    assert text.startswith("starting_at,")
    assert text.count("\n") == 1
    assert "header-only inventory file" in result.stdout


def test_run_can_write_the_report_without_uploading(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _patch_run(monkeypatch, RunResult())
    destination = tmp_path / "custom.csv"

    def fake_fetch(client: object, *, lookback_days: int, workspace_id: str | None) -> object:
        from anthropic_radar.scanners.report import CostReport

        return CostReport(
            rows=[
                {
                    "Date": "2026-01-02",
                    "ProjectID": "-",
                    "LineItem": "embeddings",
                    "Amount": "0.250000",
                    "Currency": "USD",
                }
            ]
        )

    def fail_upload(*args: object, **kwargs: object) -> None:
        raise AssertionError("upload should not run without a token")

    monkeypatch.setattr("anthropic_radar.scanners.report.fetch_cost_report", fake_fetch)
    monkeypatch.setattr("anthropic_radar.upload.upload_csvs", fail_upload)

    result = runner.invoke(
        app,
        ["run", "--admin-key", "sk-ant-admin", "--report-file", str(destination)],
    )

    assert result.exit_code == 0, result.stdout
    assert destination.is_file()
    assert "Uploading" not in result.stdout


def test_run_upload_defaults_the_report_into_csv_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _patch_run(monkeypatch, _sample_result())
    csv_dir = tmp_path / "out"
    seen: dict[str, object] = {}

    def fake_fetch(client: object, *, lookback_days: int, workspace_id: str | None) -> object:
        from anthropic_radar.scanners.report import CostReport

        return CostReport(
            rows=[
                {
                    "Date": "2026-01-02",
                    "ProjectID": "wrkspc_1",
                    "LineItem": "embeddings",
                    "Amount": "0.250000",
                    "Currency": "USD",
                }
            ]
        )

    def fake_upload(api_base: str, token: str, files: dict[str, str]) -> None:
        seen["files"] = files

    monkeypatch.setattr("anthropic_radar.scanners.report.fetch_cost_report", fake_fetch)
    monkeypatch.setattr("anthropic_radar.upload.upload_csvs", fake_upload)

    result = runner.invoke(
        app,
        [
            "run",
            "--admin-key",
            "sk-ant-admin",
            "--csv-dir",
            str(csv_dir),
            "--upload-token",
            "tok",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert seen["files"] == {
        "billing": str(csv_dir / "report.csv"),
        "inventory": str(csv_dir / "usage.csv"),
    }
    assert (csv_dir / "report.csv").is_file()
    assert (csv_dir / "usage.csv").is_file()


def test_run_report_requires_an_admin_key(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    _patch_run(monkeypatch, RunResult())

    result = runner.invoke(
        app,
        [
            "run",
            "--api-key",
            "sk-ant-api",
            "--upload-token",
            "tok",
            "--report-file",
            str(tmp_path / "r.csv"),
        ],
    )

    assert result.exit_code == 1
    assert "ANTHROPIC_ADMIN_KEY" in result.stdout


def test_run_reports_upload_failure(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    _patch_run(monkeypatch, RunResult())

    def fake_fetch(client: object, *, lookback_days: int, workspace_id: str | None) -> object:
        from anthropic_radar.scanners.report import CostReport

        return CostReport(
            rows=[
                {
                    "Date": "2026-01-01",
                    "ProjectID": "wrkspc_1",
                    "LineItem": "tokens",
                    "Amount": "1.000000",
                    "Currency": "USD",
                }
            ]
        )

    def fake_upload(api_base: str, token: str, files: dict[str, str]) -> None:
        from anthropic_radar.upload import UploadError

        raise UploadError("token was rejected")

    monkeypatch.setattr("anthropic_radar.scanners.report.fetch_cost_report", fake_fetch)
    monkeypatch.setattr("anthropic_radar.upload.upload_csvs", fake_upload)

    destination = tmp_path / "report.csv"
    result = runner.invoke(
        app,
        [
            "run",
            "--admin-key",
            "sk-ant-admin",
            "--upload-token",
            "tok",
            "--report-file",
            str(destination),
        ],
    )

    assert result.exit_code == 1
    assert "token was rejected" in result.stdout
    assert destination.is_file()


def test_run_names_the_api_it_is_waiting_on(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(client: RadarClient, config: RunConfig | None = None) -> RunResult:
        assert client.on_request is not None
        client.on_request("/v1/organizations/me")
        client.on_request("/v1/organizations/usage_report/messages")
        client.on_request("/v1/organizations/usage_report/messages")
        return RunResult()

    monkeypatch.setattr("anthropic_radar.cli.Runner.run_sync", fake_run)
    result = runner.invoke(app, ["run", "--admin-key", "sk-ant-admin"])

    assert result.exit_code == 0, result.stdout
    assert "Waiting on Anthropic API …" in result.stderr
    assert "Waiting on Anthropic API /v1/organizations/me …" in result.stderr
    assert "Waiting on Anthropic API /v1/organizations/usage_report/messages …" in result.stderr
    # A second page of the same endpoint does not repeat the line.
    assert result.stderr.count("usage_report/messages") == 1
    assert "Waiting on Anthropic API" not in result.stdout


def test_run_notifies_while_the_cost_report_waits(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _patch_run(monkeypatch, RunResult())

    def fake_fetch(client: RadarClient, *, lookback_days: int, workspace_id: str | None) -> object:
        from anthropic_radar.scanners.report import CostReport

        assert client.on_request is not None
        client.on_request("/v1/organizations/cost_report")
        return CostReport(
            rows=[
                {
                    "Date": "2026-01-01",
                    "ProjectID": "wrkspc_1",
                    "LineItem": "tokens",
                    "Amount": "1.000000",
                    "Currency": "USD",
                }
            ]
        )

    monkeypatch.setattr("anthropic_radar.scanners.report.fetch_cost_report", fake_fetch)
    monkeypatch.setattr("anthropic_radar.upload.upload_csvs", lambda **kwargs: None)

    result = runner.invoke(
        app,
        [
            "run",
            "--admin-key",
            "sk-ant-admin",
            "--upload-token",
            "tok",
            "--report-file",
            str(tmp_path / "report.csv"),
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert "Waiting on Anthropic API /v1/organizations/cost_report …" in result.stderr
    assert "Waiting on Anthropic API" not in result.stdout


def test_api_wait_uses_a_spinner_on_a_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    import io

    from anthropic_radar import cli as cli_mod

    class _Tty(io.StringIO):
        def isatty(self) -> bool:
            return True

    buf = _Tty()
    monkeypatch.setattr(sys, "stderr", buf)
    monkeypatch.setenv("TERM", "xterm-256color")

    with cli_mod._ApiWait("Waiting on Anthropic API …"):
        cli_mod._on_api_request("/v1/organizations/me")
        cli_mod._on_api_request("/v1/organizations/me")

    assert cli_mod._waits == []
    rendered = buf.getvalue()
    assert "Waiting on Anthropic API /v1/organizations/me" in rendered
    assert rendered.count("/v1/organizations/me") == 1


def test_api_wait_stops_when_the_call_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    import io

    from anthropic_radar import cli as cli_mod

    buf = io.StringIO()
    monkeypatch.setattr(sys, "stderr", buf)

    with pytest.raises(RuntimeError, match="down"):
        with cli_mod._ApiWait("Waiting on Anthropic API …"):
            raise RuntimeError("down")

    assert cli_mod._waits == []
    assert "Waiting on Anthropic API …" in buf.getvalue()


def test_run_reports_scanner_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_run(monkeypatch, AnthropicRadarError("admin key rejected"))

    result = runner.invoke(app, ["run", "--admin-key", "sk-ant-admin"])

    assert result.exit_code == 1
    assert "admin key rejected" in result.stdout


def _save_login(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    token: str = "stored-token",
    api_base: str = "http://login.example",
    expires_at: str | None = None,
) -> None:
    from datetime import datetime, timedelta, timezone

    from anthropic_radar.pump_login import PumpCredentials, save_credentials

    monkeypatch.setenv("PUMP_ANTHROPIC_RADAR_CONFIG_DIR", str(tmp_path))
    if expires_at is None:
        expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    save_credentials(
        PumpCredentials(
            access_token=token,
            token_type="Bearer",
            expires_at=expires_at,
            upload_id="upload-9",
            scope="radar",
            api_base=api_base,
        ),
        tmp_path / "credentials.json",
    )


def _patch_cost_report(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    seen: dict[str, object] = {}

    def fake_fetch(client: object, *, lookback_days: int, workspace_id: str | None) -> object:
        from anthropic_radar.scanners.report import CostReport

        return CostReport(
            rows=[
                {
                    "Date": "2026-01-01",
                    "ProjectID": "wrkspc_1",
                    "LineItem": "tokens",
                    "Amount": "1.000000",
                    "Currency": "USD",
                }
            ]
        )

    def fake_upload(api_base: str, token: str, files: dict[str, str]) -> None:
        seen["api_base"] = api_base
        seen["token"] = token
        seen["files"] = files

    monkeypatch.setattr("anthropic_radar.scanners.report.fetch_cost_report", fake_fetch)
    monkeypatch.setattr("anthropic_radar.upload.upload_csvs", fake_upload)
    return seen


def test_run_upload_uses_the_login_token(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    _patch_run(monkeypatch, _sample_result())
    _save_login(tmp_path, monkeypatch)
    seen = _patch_cost_report(monkeypatch)
    report_path = tmp_path / "report.csv"

    result = runner.invoke(
        app,
        ["run", "--admin-key", "sk-ant-admin", "--upload", "--report-file", str(report_path)],
    )

    assert result.exit_code == 0, result.stdout
    assert seen["token"] == "stored-token"
    assert seen["api_base"] == "http://login.example"
    assert seen["files"] == {
        "billing": str(report_path),
        "inventory": str(report_path.with_name("usage.csv")),
    }
    assert "stored-token" not in result.stdout


def test_run_upload_token_overrides_the_stored_login(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _patch_run(monkeypatch, RunResult())
    _save_login(tmp_path, monkeypatch)
    seen = _patch_cost_report(monkeypatch)

    result = runner.invoke(
        app,
        [
            "run",
            "--admin-key",
            "sk-ant-admin",
            "--upload",
            "--upload-token",
            "one-shot",
            "--api-base",
            "http://override.example",
            "--report-file",
            str(tmp_path / "report.csv"),
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert seen["token"] == "one-shot"
    assert seen["api_base"] == "http://override.example"


def test_run_upload_without_login_stops_before_the_scan(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    seen = _patch_run(monkeypatch, RunResult())
    monkeypatch.setenv("PUMP_ANTHROPIC_RADAR_CONFIG_DIR", str(tmp_path))

    result = runner.invoke(app, ["run", "--upload"])

    assert result.exit_code == 1
    assert "pump-anthropic-radar login" in result.stdout
    assert "Waiting on Anthropic API" not in result.stderr
    assert seen == []


def test_run_upload_rejects_an_expired_login(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from datetime import datetime, timedelta, timezone

    seen = _patch_run(monkeypatch, RunResult())
    expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    _save_login(tmp_path, monkeypatch, expires_at=expired)

    result = runner.invoke(app, ["run", "--upload"])

    assert result.exit_code == 1
    assert "expired" in result.stdout.lower()
    assert seen == []


def test_status_and_logout_round_trip(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("PUMP_ANTHROPIC_RADAR_CONFIG_DIR", str(tmp_path))

    missing = runner.invoke(app, ["status"])
    assert missing.exit_code == 1
    assert "Not logged in" in missing.stdout

    _save_login(tmp_path, monkeypatch, token="secret-token", api_base="http://login.example")
    present = runner.invoke(app, ["status"])
    assert present.exit_code == 0, present.stdout
    assert "http://login.example" in present.stdout
    assert "upload-9" in present.stdout
    assert "secret-token" not in present.stdout

    gone = runner.invoke(app, ["logout"])
    assert gone.exit_code == 0
    assert "Logged out" in gone.stdout
    assert runner.invoke(app, ["status"]).exit_code == 1


def test_login_command_reports_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    from anthropic_radar.pump_login import LoginError

    def boom(**kwargs: object) -> None:
        raise LoginError("browser denied")

    monkeypatch.setattr("anthropic_radar.cli.pump_login", boom)
    result = runner.invoke(app, ["login"])
    assert result.exit_code == 1
    assert "browser denied" in result.stdout + result.stderr


def test_findings_command_reports_scan_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(client: object, config: RunConfig | None = None) -> Any:
        raise AnthropicRadarError("down")

    monkeypatch.setattr("anthropic_radar.cli.Runner.run_sync", fake_run)
    result = runner.invoke(app, ["findings", "--admin-key", "sk-ant-admin"])
    assert result.exit_code == 1
    assert "down" in result.stdout
