# pump-anthropic-radar

**Anthropic infrastructure FinOps SDK** — part of the [Hyperscaler Radar](https://github.com/gomorsmi) suite.

Scan your Anthropic organization for users, invites, workspaces, API keys, Messages API
usage, Claude Code usage, and cost. Detect external service relationships from workspace
and API-key names, flag cost and hygiene anomalies, and export inventory to CSV or a
draw.io architecture diagram. Log in to Pump and upload the cost and usage CSVs the
same way `pump-openai-radar` does.

---

## Install

```bash
pip install pump-anthropic-radar
```

Requires Python 3.10+. CSV and draw.io export ship in the base install. The `[csv]`
and `[drawio]` extras still resolve (as no-ops) so older pins keep working.

From this checkout:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

---

## Auth: two tiers, on purpose

Every org-visibility endpoint in the Anthropic API lives behind the Admin API.
That requires an Admin key (`sk-ant-admin01-...`) from Console → Settings → Admin Keys.
A standard key (`sk-ant-api03-...`) cannot list users, workspaces, keys, usage, or cost.

* `api_key` only → `pump-anthropic-radar` runs, and every org-level table comes back empty.
  That is a supported "no access" state, so the CLI stays usable for a quick check.
* `admin_key` set (env `ANTHROPIC_ADMIN_KEY` or `--admin-key`) → full org-wide scan.
  Pump upload needs this key, because the cost report is admin-only.

```python
from anthropic_radar import RadarClient, Runner

# Reads ANTHROPIC_API_KEY / ANTHROPIC_ADMIN_KEY from the environment
client = RadarClient()
result = Runner.run_sync(client)

print(result.summary())
result.export_csv("./out/")
result.export_drawio("./out/anthropic_arch.drawio")
```

### Scoped to one workspace

```python
from anthropic_radar import RadarClient, Runner, RunConfig

client = RadarClient(admin_key="sk-ant-admin01-...")
config = RunConfig(workspace_id="wrkspc_xxx", usage_lookback_days=14)
result = Runner.run_sync(client, config)
```

---

## CLI

```bash
# Full scan — findings table to stdout
pump-anthropic-radar run

# CSVs + draw.io diagram
pump-anthropic-radar run --csv-dir ./out --drawio-file arch.drawio

# JSON instead of a table
pump-anthropic-radar run --output json --out-file scan.json

# Admin key for org-wide data (or set $ANTHROPIC_ADMIN_KEY)
pump-anthropic-radar run --admin-key sk-ant-admin01-... --csv-dir ./out

# Findings only
pump-anthropic-radar findings

# Scope to a workspace, 14-day usage lookback
pump-anthropic-radar run --workspace wrkspc_xxx --lookback 14

# Log in to Pump, then push the org cost report (admin key required)
pump-anthropic-radar login
pump-anthropic-radar run --admin-key sk-ant-admin01-... --upload

# Check or forget the stored Pump token
pump-anthropic-radar status
pump-anthropic-radar logout

# Print the version
pump-anthropic-radar version
```

Flags follow the Radar suite convention: `--output/-o` selects `table` or `json`,
`--out-file` writes the JSON payload, `--csv-dir` writes per-resource CSVs.
`--upload` writes `report.csv` (costs) and `usage.csv` (token usage) and pushes
them with the token from `pump-anthropic-radar login`. Costs upload as role `billing`;
usage uploads as role `inventory`. `--upload-token` does the same with a one-shot
token and overrides the stored login. `--report-file` chooses the cost CSV path;
with `--csv-dir` and no `--report-file` it is `{csv-dir}/report.csv`. `usage.csv`
is written next to it.

`--lookback` is the usage window (default 7 days). The cost scan and the Pump
cost report use `max(lookback, 30)` days, matching `RunConfig.cost_lookback_days`.

---

## Pump onboarding

The token is exchanged for a presigned S3 URL. Only the cost and usage CSVs leave the machine.

1. Log in. This runs the browser OAuth flow and stores an upload token locally:

   ```bash
   pump-anthropic-radar login
   ```

2. Scan and upload with an Anthropic admin key:

   ```bash
   pump-anthropic-radar run --admin-key sk-ant-admin01-... --upload
   ```

   This scans the org, pulls daily costs from `/v1/organizations/cost_report`,
   writes `report.csv` and `usage.csv`, and uploads them with the stored token.
   Costs are role `billing`; usage is role `inventory`. `--csv-dir` and
   `--drawio-file` still work on the same command. Pass `--report-file` to
   choose where the cost CSV is written.
3. Pump detects the upload and runs its analysis.

`pump-anthropic-radar status` shows whether a token is stored (not the token itself).
`pump-anthropic-radar logout` deletes it. A one-shot token still works without logging in:

```bash
pump-anthropic-radar run --admin-key sk-ant-admin01-... --upload-token <TOKEN>
```

`report.csv` columns, same shape as the other Pump radars:

| Column    | Meaning                                              |
| --------- | ---------------------------------------------------- |
| Date      | UTC day (`YYYY-MM-DD`)                               |
| ProjectID | Anthropic workspace, or `-` for the default workspace |
| LineItem  | Cost description (model and token category)          |
| Amount    | Non-zero cost in major units, 6 decimal places       |
| Currency  | e.g. `USD`                                           |

The Admin API reports amounts in lowest currency units (cents). `"123.45"` USD
is `$1.23`, and that dollar amount is what `Amount` contains. Zero-cost buckets
are omitted. The token carries no company id — Pump binds the company and the
S3 key server-side. Login stores the API origin it used, and `run --upload`
sends the report there. Override it with `--api-base` or `PUMP_API_BASE`
(default `https://api.pump.co`):

```bash
pump-anthropic-radar run --admin-key sk-ant-admin01-... --upload-token <TOKEN> --api-base http://localhost:8001
```

`anthropic_radar/upload.py` posts `{api_base}/api/v1/estimate/radar/urls` once per
file, with `{"token", "role", "provider": "anthropic"}`. Costs use role `billing`
and usage uses role `inventory`. Each CSV is then `PUT` as `Content-Type: text/csv`.

---

## Findings engine

| Rule ID   | Severity | Condition                                              |
| --------- | -------- | ------------------------------------------------------ |
| KEY_001   | MEDIUM   | Active API key had zero usage in the lookback window  |
| KEY_002   | INFO     | API key not scoped to any workspace (org default)     |
| WS_001    | LOW      | Workspace has zero active API keys                     |
| INV_001   | LOW      | Invite still pending after 30+ days                    |
| USAGE_001 | MEDIUM   | Model consumes > 10M tokens in the lookback window     |
| COST_001  | INFO     | Priority service-tier usage present                    |
| CC_001    | INFO     | Claude Code usage present with no matching workspace   |

---

## Service relationship detection

Claude has no server-side assistant instructions field to mine the way OpenAI
assistants do. `detect_relationships()` scans workspace names and API-key
names for the same external-service signals, and emits `ServiceRelationship`
edges (dashed lines in the draw.io diagram):

| Kind     | Signals                                                        |
| -------- | -------------------------------------------------------------- |
| AWS      | `aws`, `s3`, `ec2`, `lambda`, `dynamodb`, `sqs`, `bedrock`     |
| GCP      | `gcp`, `bigquery`, `gcs`, `google-cloud`, `vertex`             |
| AZURE    | `azure`, `blob-storage`, `cosmosdb`                            |
| DATABASE | `postgres`, `mysql`, `mongo`, `redis`, `neon`, `supabase`      |
| SLACK    | `slack`                                                        |
| EMAIL    | `sendgrid`, `mailgun`, `smtp`                                  |
| WEBHOOK  | `webhook`, `http-`                                             |

---

## SDK structure

```
src/anthropic_radar/
├── client.py            # RadarClient (api_key vs admin_key)
├── runner.py            # Runner, RunConfig, RunResult
├── findings.py          # FindingEngine
├── relationships.py     # naming-convention service relationship detection
├── models/base.py       # Pydantic v2 models
├── scanners/            # org, API keys, usage, Claude Code, cost report
├── exporters/           # CSV + draw.io
├── pump_login.py        # `login` / `logout` / `status` (OAuth + PKCE)
├── upload.py            # Pump presigned-URL upload (billing + inventory)
└── cli.py               # pump-anthropic-radar CLI
```

---

## Development

Python 3.10 or newer. With [uv](https://docs.astral.sh/uv/):

```bash
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"
```

The package lives in `src/anthropic_radar`, including `cli.py` and `pump_login.py`.

```bash
./pump-anthropic-radar --help
./pump-anthropic-radar login
./pump-anthropic-radar status
./pump-anthropic-radar logout
./pump-anthropic-radar run --upload
./pump-anthropic-radar version
```

`login` opens a browser for the Pump OAuth flow and writes the token to
`$XDG_CONFIG_HOME/pump-anthropic-radar/credentials.json`, or
`~/.config/pump-anthropic-radar/credentials.json` when `XDG_CONFIG_HOME` is unset.
`PUMP_ANTHROPIC_RADAR_CONFIG_DIR` overrides that directory. `PUMP_API_BASE` and
`PUMP_APP_BASE` override the Pump origins, as do `--api-base` and `--app-base`.
Scans read `ANTHROPIC_API_KEY` and, for org-wide data, `ANTHROPIC_ADMIN_KEY`.

```bash
python -m pytest
python -m unittest tests.test_pump_login
```

---

## Part of the Hyperscaler Radar suite

`aws-radar` · `gcp-radar` · `azure-radar` · `oci-radar` · `openai-radar` · `pump-anthropic-radar` · `gemini-radar` · `datadog-radar`

## License

MIT
