"""pump-anthropic-radar CLI."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import typer
from rich.console import Console
from rich.status import Status
from rich.table import Table

from anthropic_radar import __version__
from anthropic_radar.client import AnthropicRadarError, RadarClient
from anthropic_radar.models.base import Finding, UsageBucket
from anthropic_radar.pump_login import LoginError, clear_credentials, load_credentials
from anthropic_radar.pump_login import login as pump_login
from anthropic_radar.runner import RunConfig, Runner, RunResult

console = Console()

# The active loading notification, if a command is inside an API wait.
# RadarClient.on_request updates the top of this stack with the path in flight.
_waits: list[_ApiWait] = []


class _ApiWait:
    """Loading notification shown while a command is blocked on an HTTP API.

    A real terminal gets a spinner on stderr. Pipes, JSON output, and tests
    get one line on stderr instead, so a payload written to stdout stays intact.
    """

    def __init__(self, message: str) -> None:
        self._message = message
        self._console = Console(stderr=True, highlight=False)
        self._status: Status | None = None

    def __enter__(self) -> _ApiWait:
        _waits.append(self)
        try:
            if _stderr_is_tty():
                self._status = self._console.status(self._message)
                self._status.start()
            else:
                self._console.print(self._message)
        except Exception:
            _waits.pop()
            raise
        return self

    def update(self, message: str) -> None:
        if message == self._message:
            return
        self._message = message
        if self._status is not None:
            self._status.update(message)
        else:
            self._console.print(message)

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        try:
            if self._status is not None:
                self._status.stop()
        finally:
            if self in _waits:
                _waits.remove(self)


def _stderr_is_tty() -> bool:
    """True when stderr can show an in-place spinner.

    ``Console.is_terminal`` treats a non-empty ``FORCE_COLOR`` as a terminal,
    including ``FORCE_COLOR=0``, so it is the wrong signal here.
    """
    isatty = getattr(sys.stderr, "isatty", None)
    try:
        tty = bool(isatty()) if callable(isatty) else False
    except (ValueError, OSError):
        return False
    if not tty:
        return False
    term = os.environ.get("TERM", "")
    return term.lower() not in ("", "dumb", "unknown")


def _on_api_request(path: str) -> None:
    if _waits:
        _waits[-1].update(f"Waiting on Anthropic API {path} …")


app = typer.Typer(
    add_completion=False,
    help="Anthropic Radar — infrastructure FinOps scanner. Part of the Hyperscaler Radar suite.",
)

SEVERITY_STYLE = {
    "critical": "bold white on red",
    "high": "bold red",
    "medium": "yellow",
    "low": "cyan",
    "info": "dim",
}

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


def _build_client(api_key: str | None, admin_key: str | None) -> RadarClient:
    return RadarClient(api_key=api_key, admin_key=admin_key, on_request=_on_api_request)


def _render_inventory(result: RunResult) -> None:
    table = Table(title="Inventory", title_justify="left", header_style="bold magenta")
    table.add_column("Resource")
    table.add_column("Count", justify="right")
    table.add_column("Notes")

    pending = sum(1 for invite in result.invites if (invite.status or "").lower() == "pending")
    total_tokens = sum(bucket.total_tokens for bucket in result.usage)
    org = result.organization.name if result.organization else "n/a (no admin key)"

    table.add_row("Organization", org, "")
    table.add_row("Users", str(len(result.users)), "")
    table.add_row("Invites", str(len(result.invites)), f"{pending} pending")
    table.add_row("Workspaces", str(len(result.workspaces)), "")
    table.add_row("API keys", str(len(result.api_keys)), "")
    table.add_row("Usage records", str(len(result.usage)), f"{total_tokens:,} tokens")
    table.add_row("Claude Code", str(len(result.claude_code_usage)), "")
    table.add_row("Cost buckets", str(len(result.cost)), "")
    table.add_row("Relationships", str(len(result.relationships)), "")
    console.print(table)


def _render_relationships(result: RunResult) -> None:
    if not result.relationships:
        return
    table = Table(
        title="External service relationships",
        title_justify="left",
        header_style="bold magenta",
    )
    table.add_column("Source")
    table.add_column("Kind", width=12)
    table.add_column("Service", width=12)
    table.add_column("Signal", width=18)
    for rel in result.relationships:
        table.add_row(
            rel.source_label or rel.source_id,
            rel.source_kind,
            rel.kind.value,
            rel.signal,
        )
    console.print(table)


def _render_findings(findings: list[Finding]) -> None:
    if not findings:
        console.print("[green]No findings — nothing flagged against the current rule set.[/green]")
        return

    ranked = sorted(
        findings, key=lambda finding: SEVERITY_ORDER.get(finding.severity.value.lower(), 9)
    )
    table = Table(title="Findings", title_justify="left", header_style="bold magenta")
    table.add_column("Sev", width=8)
    table.add_column("Rule", width=10)
    table.add_column("Resource", width=24, overflow="ellipsis")
    table.add_column("Finding", overflow="fold")

    for finding in ranked:
        key = finding.severity.value.lower()
        style = SEVERITY_STYLE.get(key, "")
        table.add_row(
            f"[{style}]{finding.severity.value}[/{style}]",
            finding.rule_id,
            finding.resource_id,
            finding.message,
        )
    console.print(table)

    counts: dict[str, int] = {}
    for finding in ranked:
        counts[finding.severity.value] = counts.get(finding.severity.value, 0) + 1
    summary = "  ".join(
        f"[{SEVERITY_STYLE.get(sev.lower(), '')}]{count} {sev}[/{SEVERITY_STYLE.get(sev.lower(), '')}]"
        for sev, count in sorted(
            counts.items(), key=lambda kv: SEVERITY_ORDER.get(kv[0].lower(), 9)
        )
    )
    console.print(f"\n{summary}\n")


def _resolve_pump_upload(
    *,
    upload: bool,
    upload_token: str | None,
    api_base: str | None,
) -> tuple[str | None, str]:
    """Return the token and API base for this run.

    ``--upload-token`` wins. ``--upload`` uses the token stored by
    ``pump-anthropic-radar login``. An explicit ``--api-base`` (or ``PUMP_API_BASE``)
    overrides the base saved at login.
    """
    from anthropic_radar.pump_login import DEFAULT_API_BASE, token_is_expired

    if upload_token:
        return upload_token, api_base or DEFAULT_API_BASE
    if not upload:
        return None, api_base or DEFAULT_API_BASE

    try:
        creds = load_credentials()
    except LoginError as exc:
        console.print(f"[bold red]Upload failed:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc
    if creds is None:
        console.print(
            "[bold red]Upload failed:[/bold red] Not logged in. "
            "Run `pump-anthropic-radar login`, or pass --upload-token."
        )
        raise typer.Exit(code=1)
    if token_is_expired(creds):
        console.print(
            "[bold red]Upload failed:[/bold red] Pump login expired. "
            "Run `pump-anthropic-radar login` again."
        )
        raise typer.Exit(code=1)
    return creds.access_token, api_base or creds.api_base


def _report_destination(report_file: str | None, csv_dir: str | None) -> Path:
    if report_file:
        return Path(report_file)
    if csv_dir:
        return Path(csv_dir) / "report.csv"
    return Path("report.csv")


def _cost_lookback(lookback_days: int) -> int:
    """Cost history is at least 30 days, matching RunConfig."""
    return max(lookback_days, 30)


def _write_and_maybe_upload_report(
    client: RadarClient,
    *,
    lookback_days: int,
    workspace_id: str | None,
    upload_token: str | None,
    api_base: str,
    report_file: str | None,
    csv_dir: str | None,
    usage: list[UsageBucket],
) -> None:
    """Write the cost report and usage CSV, and PUT them to Pump when a token is set.

    Costs upload as role ``billing``. Usage always uploads as role ``inventory``,
    including a header-only file when the scan found no usage rows. Pump starts
    analysis only after both objects exist.
    """
    from anthropic_radar.scanners.report import ReportError, fetch_cost_report, write_report_csv
    from anthropic_radar.scanners.usage import write_usage_csv
    from anthropic_radar.upload import UploadError, upload_csvs

    destination = _report_destination(report_file, csv_dir)
    try:
        with _ApiWait("Waiting on Anthropic API …"):
            report = fetch_cost_report(
                client,
                lookback_days=lookback_days,
                workspace_id=workspace_id,
            )
    except (ReportError, AnthropicRadarError) as exc:
        console.print(f"[bold red]Report failed:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc

    if report.truncated:
        console.print("[yellow]Cost report may be incomplete.[/yellow]")

    written = write_report_csv(destination, report.rows)
    console.print(f"[green]Wrote[/green] {written}")

    usage_path = write_usage_csv(destination.with_name("usage.csv"), usage)
    console.print(f"[green]Wrote[/green] {usage_path}")
    if not usage:
        console.print("[yellow]No usage rows; wrote a header-only inventory file.[/yellow]")

    if not upload_token:
        return

    files = {"billing": str(written), "inventory": str(usage_path)}
    console.print(f"Uploading to Pump ({api_base})")
    try:
        upload_csvs(api_base=api_base, token=upload_token, files=files)
    except UploadError as exc:
        console.print(f"[bold red]Upload failed:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc
    console.print("[green]Your Anthropic cost and usage data is on its way to Pump.[/green]")


def _payload(result: RunResult) -> dict:
    return result.model_dump(mode="json")


@app.command()
def run(
    workspace: str | None = typer.Option(
        None, "--workspace", help="Scope the scan to this workspace ID."
    ),
    api_key: str | None = typer.Option(None, "--api-key", help="Overrides ANTHROPIC_API_KEY."),
    admin_key: str | None = typer.Option(
        None,
        "--admin-key",
        help="Overrides ANTHROPIC_ADMIN_KEY. Unlocks org-wide usage and cost.",
    ),
    lookback: int = typer.Option(
        7, "--lookback", help="Days of usage history. Cost uses at least 30."
    ),
    output: str = typer.Option("table", "--output", "-o", help="table | json"),
    out_file: str | None = typer.Option(None, "--out-file", help="Write the JSON payload here."),
    csv_dir: str | None = typer.Option(None, "--csv-dir", help="Write per-resource CSVs here."),
    drawio_file: str | None = typer.Option(
        None, "--drawio-file", help="Write a draw.io architecture diagram here."
    ),
    upload: bool = typer.Option(
        False,
        "--upload",
        help="Upload costs as billing and usage as inventory, using `pump-anthropic-radar login`.",
    ),
    upload_token: str | None = typer.Option(
        None,
        "--upload-token",
        help="Pump upload token. Overrides the token stored by `pump-anthropic-radar login`.",
    ),
    api_base: str | None = typer.Option(
        None,
        "--api-base",
        envvar="PUMP_API_BASE",
        help="Pump API origin. Overrides the base stored by login.",
    ),
    report_file: str | None = typer.Option(
        None,
        "--report-file",
        help="Write the cost report CSV here. With --upload, defaults to report.csv.",
    ),
) -> None:
    """Scan an Anthropic organization."""
    if output not in ("table", "json"):
        console.print(f"[bold red]--output must be 'table' or 'json', got '{output}'.[/bold red]")
        raise typer.Exit(code=2)

    client = _build_client(api_key, admin_key)
    token, pump_base = _resolve_pump_upload(
        upload=upload, upload_token=upload_token, api_base=api_base
    )
    config = RunConfig(
        workspace_id=workspace,
        usage_lookback_days=lookback,
        cost_lookback_days=_cost_lookback(lookback),
    )

    started = time.time()
    try:
        with _ApiWait("Waiting on Anthropic API …"):
            result = Runner.run_sync(client, config)
    except AnthropicRadarError as exc:
        console.print(f"[bold red]Scan failed:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc
    elapsed = time.time() - started

    if output == "json":
        text = json.dumps(_payload(result), indent=2, default=str)
        if out_file:
            Path(out_file).write_text(text, encoding="utf-8")
            console.print(f"[green]Wrote[/green] {out_file}")
        else:
            sys.stdout.write(text + "\n")
    else:
        _render_inventory(result)
        _render_relationships(result)
        _render_findings(result.findings)
        if out_file:
            Path(out_file).write_text(
                json.dumps(_payload(result), indent=2, default=str),
                encoding="utf-8",
            )
            console.print(f"[green]Wrote[/green] {out_file}")

    if csv_dir:
        for path in result.export_csv(csv_dir):
            console.print(f"[green]Wrote[/green] {path}")

    if drawio_file:
        console.print(f"[green]Wrote[/green] {result.export_drawio(drawio_file)}")

    if token or report_file:
        _write_and_maybe_upload_report(
            client,
            lookback_days=config.cost_lookback_days,
            workspace_id=workspace,
            upload_token=token,
            api_base=pump_base,
            report_file=report_file,
            csv_dir=csv_dir,
            usage=result.usage,
        )

    if output != "json":
        console.print(f"[dim]Scan complete in {elapsed:.2f}s[/dim]")


@app.command()
def findings(
    workspace: str | None = typer.Option(
        None, "--workspace", help="Scope the scan to this workspace ID."
    ),
    api_key: str | None = typer.Option(None, "--api-key", help="Overrides ANTHROPIC_API_KEY."),
    admin_key: str | None = typer.Option(
        None, "--admin-key", help="Overrides ANTHROPIC_ADMIN_KEY."
    ),
    lookback: int = typer.Option(7, "--lookback", help="Days of usage history."),
    output: str = typer.Option("table", "--output", "-o", help="table | json"),
) -> None:
    """Scan, then print only the findings table."""
    client = _build_client(api_key, admin_key)
    config = RunConfig(
        workspace_id=workspace,
        usage_lookback_days=lookback,
        cost_lookback_days=_cost_lookback(lookback),
    )
    try:
        with _ApiWait("Waiting on Anthropic API …"):
            result = Runner.run_sync(client, config)
    except AnthropicRadarError as exc:
        console.print(f"[bold red]Scan failed:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc

    if output == "json":
        sys.stdout.write(
            json.dumps([item.model_dump(mode="json") for item in result.findings], indent=2) + "\n"
        )
    else:
        _render_findings(result.findings)


@app.command()
def login(
    api_base: str | None = typer.Option(
        None,
        "--api-base",
        help="Pump API origin. Defaults to $PUMP_API_BASE or https://api.pump.co.",
    ),
    app_base: str | None = typer.Option(
        None,
        "--app-base",
        help="Pump app origin. Defaults to $PUMP_APP_BASE or https://app.pump.co.",
    ),
) -> None:
    """Log in with Pump (OAuth 2.0 authorization code + PKCE) and store the token."""
    try:
        pump_login(api_base=api_base, app_base=app_base)
    except LoginError as exc:
        typer.echo(f"Login failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc


@app.command()
def logout() -> None:
    """Forget the Pump token stored by `login`."""
    typer.echo("Logged out." if clear_credentials() else "Not logged in.")


@app.command()
def status() -> None:
    """Show whether a Pump token is stored, without printing the token."""
    try:
        creds = load_credentials()
    except LoginError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if creds is None:
        typer.echo("Not logged in. Run `pump-anthropic-radar login`.")
        raise typer.Exit(code=1)
    upload = f" upload {creds.upload_id}" if creds.upload_id else ""
    typer.echo(f"Logged in to {creds.api_base}.{upload} Token expires {creds.expires_at}.")


@app.command()
def version() -> None:
    """Print the version."""
    console.print(f"pump-anthropic-radar {__version__}")


def main() -> None:
    app(prog_name="pump-anthropic-radar")


if __name__ == "__main__":
    main()
