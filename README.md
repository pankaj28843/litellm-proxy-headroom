# LiteLLM Proxy Headroom

## Run

Install dependencies:

```bash
uv sync
```

Run the owned LiteLLM proxy:

```bash
uv run litellm --config config/litellm.yaml --host 127.0.0.1 --port 4000
```

The LiteLLM config uses ChatGPT's `chatgpt/` provider route and a config-local
callback adapter, `config/headroom_litellm_callback.py`, which imports Headroom
library code for compression and posts analytics to the custom backend.
On a machine without existing ChatGPT device credentials, LiteLLM prompts for
the ChatGPT OAuth device flow during startup.

This repository does not operate Headroom as a product surface: no Headroom CLI,
`headroom proxy`, Headroom MCP server, Headroom dashboard, route alias, or
Compose service belongs here. Headroom remains an installed library dependency
for the compression callback and CCR-compatible adapter; the owned LiteLLM
proxy, analytics backend, PostgreSQL storage, and custom MCP endpoint are the
control plane.

## Check

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

For analytics/integration changes, prove the deployed E2E path first with the
smoke commands in [Analytics Observability Evidence](docs/analytics-observability-evidence.md).
Unit tests are a hardening step after runtime behavior is shown to work.

ChatGPT subscription authentication is handled by LiteLLM's documented OAuth
device flow. Do not commit OAuth tokens, API keys, or LiteLLM master keys.

## Analytics Database

The token-compression analytics backend uses its own PostgreSQL database,
separate from Phoenix storage. Local defaults are in `.env.example`:

```bash
ANALYTICS_DATABASE_URL=postgresql+asyncpg://analytics:analytics@127.0.0.1:55432/analytics
```

Start the local database through Compose and apply migrations:

```bash
docker compose up -d analytics-db
uv run alembic upgrade head
```

Run the custom analytics backend locally:

```bash
uv run uvicorn litellm_proxy_headroom.analytics.adapters.api.app:create_app --factory --host 127.0.0.1 --port 8010
```

Smoke the backend ingress/retrieval/stats/metrics path:

```bash
uv run python scripts/e2e_analytics_smoke.py
```

Smoke the CCR adapter contract used by the imported compression library:

```bash
HEADROOM_ANALYTICS_URL=http://127.0.0.1:8010 \
  uv run python scripts/e2e_headroom_ccr_smoke.py
```

Smoke the buffered LiteLLM callback ingestion path:

```bash
HEADROOM_ANALYTICS_URL=http://127.0.0.1:8010 \
  uv run python scripts/e2e_litellm_buffer_smoke.py
```

Smoke custom backend MCP retrieval and analytics telemetry surfaces:

```bash
uv run python scripts/e2e_mcp_otel_smoke.py
```

Smoke filtered records, dashboard-ready stats, and PostgreSQL spot-check
recomputability:

```bash
uv run python scripts/e2e_query_stats_smoke.py
uv run python scripts/e2e_dashboard_stats_smoke.py
```

The analytics dashboard is served by the custom backend at:

```text
http://127.0.0.1:8010/dashboard
```

It is a server-rendered Jinja/HTMX dashboard backed by the same PostgreSQL
source rows as the read APIs. It supports `15m`, `1h`, `24h`, `7d`, `30d`,
`all`, and custom `from`/`to` ranges; provider/model/strategy/tenant/team/status
filters; negative-savings filtering; live polling; and pause/resume. Empty
states are real empty states, not fake production totals. Use
`scripts/e2e_dashboard_stats_smoke.py` to seed demo evidence.

Smoke historical simulation replay and production-record isolation:

```bash
uv run python scripts/e2e_simulation_smoke.py
```

The analytics package is split into a framework-free domain layer,
application commands, and PostgreSQL adapters under
`src/litellm_proxy_headroom/analytics/`. API, LiteLLM callback, CCR, and OTel
adapters are layered around those modules.

Imported library code can use the analytics backend through the plugin entry
point when a path needs the `CompressionStoreBackend` protocol:

```bash
HEADROOM_CCR_BACKEND=analytics-postgres
HEADROOM_ANALYTICS_URL=http://analytics-backend:8010
```

The plugin implements the library `CompressionStoreBackend` protocol and sends
CCR store/retrieve activity to the custom backend over bounded HTTP. It does
not write directly to PostgreSQL.

The LiteLLM callback posts analytics through a bounded async buffer. Local
defaults are:

```bash
HEADROOM_ANALYTICS_BUFFER_SIZE=1000
HEADROOM_ANALYTICS_BUFFER_WORKERS=2
HEADROOM_ANALYTICS_MAX_ATTEMPTS=3
HEADROOM_ANALYTICS_RETRY_BASE_SECONDS=0.1
HEADROOM_ANALYTICS_RETRY_MAX_SECONDS=1.0
HEADROOM_ANALYTICS_SHUTDOWN_TIMEOUT_SECONDS=2.0
```

For repeatable evidence gathering across the backend, Compose logs, PostgreSQL,
metrics, and Phoenix, use
[docs/analytics-observability-evidence.md](docs/analytics-observability-evidence.md).
For full-fidelity local CLI request/response forensics with mitmproxy, use
[docs/harness-engineering.md](docs/harness-engineering.md). MITM is the harness
sensor for "what did the CLI actually send?" claims; account-bracketed Codex
proof remains the primary usefulness signal.
For backend setup, configuration, data flow, retention, extension points, and
operational trade-offs, use
[docs/analytics-backend.md](docs/analytics-backend.md).

The custom analytics backend exposes MCP at:

```text
http://127.0.0.1:8010/mcp/
```

CCR marker retrieval stays on the local analytics path. Imported compression
library code writes stored chunks through the `HEADROOM_CCR_BACKEND` entry
point and the bounded `/headroom/ccr` adapter routes; agents retrieve
marker hashes through the custom MCP tool
`mcp__analytics__litellm_proxy_analytics_retrieve_chunk`. The repo-owned
wrappers register only this analytics MCP endpoint.

Dashboard/read APIs are exposed by the custom backend:

- `GET /dashboard` with dashboard filters: `preset`, `from`, `to`, `provider`,
  `model`, `strategy`, `tenant_id`, `team_id`, `status`, `negative_savings`,
  `data_scope`, `live`, and `paused`.
- `GET /dashboard/partials/live`, `/controls`, `/summary`, `/activity`,
  `/breakdowns`, `/records`, and `/simulations` for HTMX refresh.
- `GET /stats` with filters: `from`, `to`, `provider`, `model`, `strategy`,
  `tenant_id`, `team_id`, `status`, `negative_savings`, and `data_scope`.
- `GET /stats/breakdown?group_by=provider|model|strategy|tenant|team|status`
  with the same filters.
- `GET /stats/dashboard` with the same filters for dashboard-grade totals,
  distributions, latency, cost, cache, retrieval frequency, negative-savings,
  estimated-vs-provider token diagnostics, and primary-usefulness proof status.
- `GET /records/compression` with the same filters plus `limit` and `offset`.
- `GET /records/compression/{request_key}` for request detail. Routine detail
  responses expose hashes, counts, booleans, and token measurements, not raw
  provider metadata or chunk content.
- `POST /simulations/runs` to replay selected historical executions under
  alternate compression/pricing assumptions and store results separately.
- `GET /simulations/runs` and `GET /simulations/runs/{simulation_key}` for
  simulation summaries and results.

Read the numbers as recomputed source-row totals, not cached dashboard state:

- Query surfaces default to `data_scope=real`, which excludes rows marked in
  request metadata as smoke/demo/synthetic/test data. Use `data_scope=test`
  for seeded validation rows or `data_scope=all` when deliberately comparing
  both scopes.
- `requests` is the count of distinct compression request rows among matching
  executions.
- `executions` is the count of compression execution rows. A failed provider
  attempt can still create an execution row.
- `chunks` is the count of chunk rows for those executions.
- `retrievals` is the count of retrieval events joined through matching chunk
  rows.
- `original_tokens`, `compressed_tokens`, and `tokens_saved` are sums from
  compression execution rows. They can be null or zero for routes where no
  compression measurement was produced.
- Provider usage fields such as `provider_input_tokens`,
  `provider_output_tokens`, cached input, and reasoning tokens come from
  provider token usage rows and are separate from compression savings.
- Provider cache hit comes from provider-reported `cached_input_tokens` divided
  by provider-reported input tokens. This is different from backend cache-event
  counts and feeds a billing-equivalent input estimate.
- Billing-equivalent input uses:
  `uncached_provider_input + cached_provider_input * cached_input_multiplier`,
  compared with estimated-before input tokens. This is a one-sided diagnostic,
  not proof of provider-credit savings or usable capacity. The default
  cached-input multiplier is `0.10` for the current OpenAI GPT-5.x text
  cached-input pricing ratio as of 2026-06-23 and can be overridden with
  `ANALYTICS_CACHED_INPUT_COST_MULTIPLIER` when the provider, tier, or pricing
  changes.
- `negative_savings_executions` counts executions where compression expanded
  token count. `cost_increase_provider_calls` counts provider calls where the
  measured cost exceeded the estimated baseline.
- Dashboard cost fields compare `provider_calls.cost_total` with estimated
  rows in `cost_calculations`; missing provider cost stays `null` rather than
  becoming a fake zero-dollar value. The estimated delta is diagnostic until
  direct-vs-proxy proof exists.
- Primary-usefulness status is separate from the one-sided diagnostics. A
  useful Codex result requires a direct-vs-proxy Codex CLI proof bracketed by
  first-party account snapshots for quota/credit depletion. Aggregate provider
  usage/cost and cache hit across the whole Codex turn/provider-call sequence
  remain explanatory diagnostics.

## Docker Compose

The default operator path is:

```bash
make
```

That creates `.env` from `.env.example` if needed, creates local runtime
directories, builds the shared Python image, and starts:

- LiteLLM API: <http://127.0.0.1:4000>
- Open WebUI: <http://127.0.0.1:8080>
- Phoenix: <http://127.0.0.1:6006>
- Phoenix PostgreSQL on the private Compose network
- Analytics PostgreSQL: `127.0.0.1:${ANALYTICS_POSTGRES_PORT:-55432}`
- Analytics backend: <http://127.0.0.1:8010>
- Analytics MCP: <http://127.0.0.1:8010/mcp/>

Useful targets:

```bash
make auth-import        # copy existing sibling ChatGPT OAuth file if present
make mcp                # print the analytics MCP endpoint
make models             # refresh LiteLLM models from codex debug models
make e2e                # send a real request through LiteLLM -> analytics
make analytics-smoke    # run synthetic backend/CCR/MCP/stats/simulation smokes
make logs SERVICE=litellm
make ps
make down
make check
```

## Agent CLI Wrappers

Use the repo-owned wrappers when running agent CLIs through this LiteLLM stack:

```bash
./bin/codex-litellm --help
./bin/codex-litellm exec "reply with a short health marker"

./bin/claude-litellm --help
./bin/claude-litellm --print --verbose --output-format stream-json \
  "reply with a short health marker"

./bin/opencode-litellm --help
./bin/opencode-litellm run --format json "reply with a short health marker"

./bin/copilot-litellm --version

./bin/pi-litellm --version
./bin/pi-litellm --mode json --no-tools -p "reply with a short health marker"
```

These wrappers read `.env`, do not print secret values, and generate non-secret
runtime config in managed local state. To put them on your PATH without changing
global agent config, symlink the wrapper names:

```bash
ln -sf "$PWD/bin/codex-litellm" "$HOME/.local/bin/codex-litellm"
ln -sf "$PWD/bin/claude-litellm" "$HOME/.local/bin/claude-litellm"
ln -sf "$PWD/bin/opencode-litellm" "$HOME/.local/bin/opencode-litellm"
ln -sf "$PWD/bin/copilot-litellm" "$HOME/.local/bin/copilot-litellm"
ln -sf "$PWD/bin/pi-litellm" "$HOME/.local/bin/pi-litellm"
```

Current support levels are maintained in
[docs/agent-cli-support.md](docs/agent-cli-support.md), and the shared
provider-row proof schema is in
[docs/multi-cli-proof-contract.md](docs/multi-cli-proof-contract.md). Short
version: Codex routes through LiteLLM, and the latest account-bracketed
mutable-output proof is both shareable and provider/cache positive. Marker
`codex-savings-direct-first-20260626T142720Z` ran 12 resumed `gpt-5.5` turns per
lane with `HEADROOM_RESPONSES_MUTABLE_OUTPUT_COMPRESSION=true` only for the
experiment, direct first then proxy, yolo-equivalent mode, 240 seconds of
account settle per lane, and `1,247,878` combined input tokens. First-party
Codex account snapshots passed as `proxy_not_worse`: direct primary five-hour
quota moved `36 -> 37`, proxy primary stayed `37 -> 37`, weekly quota stayed
`21 -> 21` in both lanes, reset credits stayed `2`, and daily account tokens
stayed `11,351,861`. Provider/cache diagnostics passed: proxy cached input was
`+26,624`, newly processed input was `-26,448`, billing-equivalent input was
`-23,785.6`, and cache-ratio delta was `+0.042409`; cost remains unavailable.
Raw total tokens were `+176` for proxy and are recorded as a diagnostic warning,
not a failure, because billing-equivalent input improved. Proxy DB rows
recorded 24 provider calls, 23 successful mutable-output executions, and
`189,504` local tokens saved. Report:
`tmp/codex-savings-report/codex-savings-direct-first-20260626T142720Z/report.html`.
MITM marker `codex-mitm-mutable-output-gpt55-20260626T134521Z` captured direct
HTTP diagnostic, proxy inbound, and LiteLLM outbound provider request shape for
the same request-shape family; MITM remains diagnostic and does not replace
account snapshots.

The passthrough-off experiment
`codex-savings-passthrough-off-20260626T130210Z` also passed account-capacity
but failed provider/cache diagnostics; outbound MITM showed that `off` removes
`client_metadata` and `prompt_cache_key`, so do not use that experiment as a
savings path.
Claude Code is still route-gated on the current
ChatGPT-backed `gpt-5.x` deployment. OpenCode routes through LiteLLM but still
has no cache-usefulness proof after the latest on/off practical comparison.
GitHub Copilot CLI routes through LiteLLM BYOK after upgrading to Copilot CLI
1.0.65 and a current three-call `gpt-5.5` practical series, but remains
time-window/cache-unproven. Pi routes through LiteLLM after upgrading to Pi
0.80.2 but is not useful on the latest `agent-90` versus compression-off
practical proof. Do not claim cache/cost savings without an aggregate practical
proof.

The next Codex usefulness proof must be longer than the historical one-turn
artifacts: run 8-12 resumed `codex exec --json` user-message turns per lane
with `gpt-5.5`, yolo-equivalent execution mode on both lanes, and at least
`1,000,000` combined direct-plus-wrapper input tokens before classifying
five-hour/weekly quota usefulness. MITM captures remain required for claims
about wrapper request shape, headers, bodies, continuation fields, or transport;
first-party Codex account snapshots remain the quota proof.

`bin/codex-litellm` sets `CODEX_HOME` to the managed `~/.codex-headroom`
directory, writes `config.toml` and `litellm.config.toml`, symlinks native
Codex state such as `sessions` and `auth.json` from `~/.codex`, configures the
LiteLLM Responses provider at `http://127.0.0.1:4000/v1`, and adds the
analytics MCP endpoint for local compression-marker retrieval. The wrapper refuses
`CODEX_LITELLM_HOME=$HOME/.codex` by default because it does not own Headroom's
snapshot/unwrap machinery for mutating a user's native Codex config; choose an
isolated directory instead. It also maps `CODEX_LITELLM_PROJECT` to
`X-LLM-Proxy-Project` for local analytics attribution, defaulting the value
from the launch directory name when unset. It maps `CODEX_LITELLM_CLIENT` to
`X-LLM-Proxy-Client` for local analytics attribution, defaulting to
`codex`. The wrapper refuses custom Codex `--profile` values by default
because they can bypass the generated LiteLLM provider; omit `--profile`, use
`--profile litellm`, or set
`CODEX_LITELLM_ALLOW_PROFILE_OVERRIDE=1` only for deliberate debugging. Set
`CODEX_LITELLM_BASE_URL` when the local LiteLLM service is not on
`http://127.0.0.1:4000`; the wrapper normalizes it to the `/v1` OpenAI-compatible
base URL used by both `OPENAI_BASE_URL` and the generated Codex provider. Set
`CODEX_LITELLM_ANALYTICS_URL` when the analytics backend is not on
`http://127.0.0.1:8010`; the wrapper normalizes it to the local analytics
`/mcp/` endpoint. Set `CODEX_LITELLM_REASONING_EFFORT` to `minimal`, `low`,
`medium`, `high`, or `xhigh` when the isolated profile should pin Codex
`model_reasoning_effort`. Set `CODEX_LITELLM_MODEL_VERBOSITY` to `low`,
`medium`, or `high` when the isolated profile should pin Codex
`model_verbosity`.

`bin/pi-litellm` sets `PI_CODING_AGENT_DIR` to managed `~/.pi-headroom`, writes
`models.json` with a custom `litellm` provider using the OpenAI Responses API,
and maps `PI_LITELLM_MODEL` plus `PI_LITELLM_SMALL_MODEL` to generated model
entries. The generated config references `LITELLM_MASTER_KEY` as an environment
variable and sends local `X-LLM-Proxy-*` attribution headers through Pi's
documented custom-provider header config. The wrapper defaults to `gpt-5.5`;
use `PI_LITELLM_MODEL=gpt-5.4-mini` only for smoke routing. Latest Pi
practical proof compared normal `agent-90` with
`PI_LITELLM_COMPRESSION_MODE=off`; normal compression used `27327` more total
provider tokens and `35044.40` more billing-equivalent input tokens, with cost
still unavailable.

`bin/claude-litellm` sets Claude Code's LiteLLM gateway environment, maps
`LITELLM_MASTER_KEY` to both Anthropic API key env names used by Claude Code,
limits settings to project scope so user `apiKeyHelper` config does not bypass
LiteLLM, and writes a generated analytics MCP config under `~/.claude-headroom`
by default. Set `CLAUDE_LITELLM_HOME` to move that managed home, or
`CLAUDE_LITELLM_STATE_DIR` to override the generated state directory in
wrapper tests/scripts.
Set `CLAUDE_LITELLM_BASE_URL` when LiteLLM is not on `http://127.0.0.1:4000`,
and `CLAUDE_LITELLM_ANALYTICS_URL` when analytics is not on
`http://127.0.0.1:8010`. The wrapper also appends local
`X-LLM-Proxy-Client`, `X-LLM-Proxy-Project`, and optional
`X-LLM-Proxy-Run` headers through Claude Code's `ANTHROPIC_CUSTOM_HEADERS`
surface for analytics correlation. It preserves existing custom headers and
does not write them to generated files. The wrapper validates URLs before
writing managed config and never writes `LITELLM_MASTER_KEY` into `mcp.json`.
LiteLLM's Claude Code docs describe `/v1/messages` routing for non-Anthropic
models, but the current deployment only has ChatGPT-backed `gpt-5.x` aliases.
Fresh real Claude Code smoke reached LiteLLM and analytics, produced
marker-correlated failed DB rows, and still failed with a 400 because that model
group rejects Claude Code's system-message request shape. Claude Code remains
route-gated until an Anthropic-compatible or otherwise Claude-compatible
LiteLLM model route is proven.

`bin/opencode-litellm` sets OpenCode's config and XDG state roots under the
managed `~/.opencode-headroom` home by default, writes a generated
`opencode.json` custom provider for LiteLLM using `@ai-sdk/openai-compatible`,
and references `LITELLM_MASTER_KEY` through `{env:LITELLM_MASTER_KEY}` instead
of copying the key. Set `OPENCODE_LITELLM_HOME` to move the managed home. Set
`OPENCODE_LITELLM_BASE_URL` and `OPENCODE_LITELLM_ANALYTICS_URL` when services
are not on `http://127.0.0.1:4000` and `http://127.0.0.1:8010`. The wrapper
pins `--model litellm/gpt-5.5` for run-style commands unless the command
already supplies `--model`. `opencode models litellm`, a real `gpt-5.4-mini`
smoke run, and a real practical `gpt-5.5` series have reached LiteLLM with
marker-correlated provider usage. The practical series currently has no
provider-reported cached input and no observed cost, so OpenCode routing is
supported but cache usefulness is not proven.

`bin/copilot-litellm` sets `COPILOT_HOME` to managed `~/.copilot-headroom`,
disables Copilot auto-update for wrapper sessions, refuses native `~/.copilot`
by default, and configures Copilot CLI's documented BYOK provider surface for
the local LiteLLM OpenAI-compatible endpoint. It maps `LITELLM_MASTER_KEY` to
`COPILOT_PROVIDER_BEARER_TOKEN`, sets `COPILOT_PROVIDER_BASE_URL` from
`COPILOT_LITELLM_BASE_URL` normalized to `/v1`, uses
`COPILOT_PROVIDER_TYPE=openai`, and defaults to
`COPILOT_PROVIDER_WIRE_API=responses` for GPT-5.x models. Set
`COPILOT_LITELLM_MODEL`, `COPILOT_LITELLM_PROVIDER_MODEL_ID`, or
`COPILOT_LITELLM_WIRE_MODEL` when Copilot's agent configuration model and the
LiteLLM wire model should differ. Current proof: real Copilot CLI 1.0.65
`gpt-5.4-mini` smoke and three-call `gpt-5.5` practical series route through
LiteLLM by narrow time-window DB correlation. The latest practical aggregate
has 3 provider-reported `/v1/responses` rows, input `47216`, total `47482`,
cached input absent, and observed cost unavailable, so cache/cost usefulness is
not claimed.

For marker-capable wrappers, use `*_LITELLM_COMPRESSION_MODE=off` only as a
proof baseline. Codex, Claude Code, OpenCode, and Pi normalize that setting to
`X-LLM-Proxy-Compression: off`; the callback records
`litellm_proxy_compression_mode=off`, skips Headroom compression transforms,
and still records provider usage rows for aggregate comparison. Copilot CLI is
excluded because its documented BYOK surface does not expose local request
headers.

## Agent-90 Usefulness Harness

Use the A/B harness before making dashboard value claims. This is the
usefulness proof path; dashboard smoke scripts only validate read models and
must not be used as the primary usefulness proof. The harness defaults to a
dry-run plan that prints the direct Codex lane, the `./bin/codex-litellm` proxy
lane, artifact paths, stop rules, and the proxy DB query template:

```bash
uv run python scripts/e2e_agent90_usefulness.py --marker AGENT90_PROOF
```

Run a cheap Codex CLI smoke proof with the mini model when you only need to
verify the local harness/service path:

```bash
uv run python scripts/e2e_agent90_usefulness.py --marker AGENT90_SMOKE --model gpt-5.4-mini --task-lines 3 --execute --query-db
```

Run practical usefulness proofs with the primary model only when you are ready
to spend real Codex account quota and provider calls:

```bash
uv run python scripts/e2e_agent90_usefulness.py --marker AGENT90_PROOF --model gpt-5.5 --execute --query-db
```

Interpret the proof at the run level, not from one selected provider call. The
primary question is whether real Codex account capacity depletes less, or at
least not materially more, through `./bin/codex-litellm` than through direct
Codex. Capture first-party account snapshots before and after each lane:

```bash
python3 scripts/codex_account_snapshot.py --pretty
```

The helper uses `codex app-server --stdio` JSON-RPC calls to
`account/rateLimits/read` and `account/usage/read` without reading credential
files. Those snapshots expose five-hour and weekly used percentages, reset
times, credits, reset-credit count, and account token activity when the current
CLI/account supports them. Use `--account-snapshot-settle-seconds <seconds>`
for quota surfaces that update after the Codex lane completes; the default is
`0.0` so smoke and fixture runs do not wait. The harness also retries
unavailable snapshots with `--account-snapshot-attempts` and
`--account-snapshot-retry-delay-seconds` because `codex app-server --stdio` can
occasionally return usage without rate-limit data.

A Codex CLI turn can produce a sequence of provider calls, so provider-token
diagnostics still come from `summary.json` aggregate lane totals and
`token_comparison.mvp_usefulness`: direct vs proxy input, cached input, output,
reasoning, total tokens, cost when present, billing-equivalent input, and
cache-hit ratio across the whole Codex turn/provider-call sequence. A proxy DB
row proving compression ran is necessary evidence, but it does not prove
usefulness unless the account-depletion comparison passes or is explicitly
unavailable and the result is kept unproven. The harness also writes
`trajectory-summary.json` per lane and `summary.json.trajectory_comparison`
with local Codex JSONL command strings, agent messages, event counts, completed
command counts, and command-output size estimates. Use that trajectory
comparison to decide whether a provider-token/cache delta was measured over
comparable agent behavior.

Before `--execute` spends provider calls, the harness checks that LiteLLM is
reachable at `--litellm-url` (default `http://127.0.0.1:4000`) and that
`/v1/models` advertises the pinned Codex model. It also checks
`/callbacks/list` for the local `HeadroomCallback`, so a proxy that is live but
missing the compression callback fails before Codex runs. If `LITELLM_MASTER_KEY`
is present in the harness environment, these LiteLLM preflights send it as a
bearer token; artifacts record only that auth was used, never the key value.
With `--query-db`, it also requires analytics readiness at
`--analytics-url/ready` (default `http://127.0.0.1:8010/ready`). A failed
preflight writes `preflight-result.json` and a top-level `summary.json` with no
lane results, then exits before direct or proxy Codex runs. Use
`--skip-preflight` only for intentional fixture/smoke runs where the services
are not expected to be present.

To also capture the proxy analytics proof table in the same run:

```bash
uv run python scripts/e2e_agent90_usefulness.py --marker AGENT90_PROOF --model gpt-5.5 --execute --query-db
```

Both lanes use `-a never`, `-s read-only`, the same prompt shape, the same
Codex model through `-m`, the same Codex `model_reasoning_effort` through `-c`,
the same Codex `model_verbosity` through `-c`, and `codex exec --json` so
provider-reported usage is parsed from `turn.completed.usage` events instead
of human stderr formatting. The harness defaults to the practical model
`gpt-5.5` with reasoning effort `medium` and model verbosity `medium`; use
`--model gpt-5.4-mini` for smoke checks, and override
`--reasoning-effort <effort>` and `--model-verbosity <verbosity>` when direct
Codex and LiteLLM should be compared on a different configured effort or
verbosity. The proxy lane
passes the same `--litellm-url` into `bin/codex-litellm` as
`CODEX_LITELLM_BASE_URL`, so preflight and Codex routing use the same LiteLLM
instance. It also passes `--analytics-url` as `CODEX_LITELLM_ANALYTICS_URL`,
`--reasoning-effort` as `CODEX_LITELLM_REASONING_EFFORT`, and
`--model-verbosity` as `CODEX_LITELLM_MODEL_VERBOSITY`, so local analytics MCP
retrieval, analytics readiness, and isolated profile defaults match the proof
plan. The LiteLLM service declares
`HEADROOM_SAVINGS_PROFILE=agent-90` by default, and the harness records the
same expected strategy in the DB proof query. Non-default values are validated
against Headroom's built-in profile registry before the harness runs. Artifacts
are written under `tmp/agent90-usefulness/<marker>/`, including per-lane
`summary-lines.txt`, `token-summary.json`, `trajectory-summary.json`, and a
top-level `summary.json` with direct-vs-proxy token and trajectory deltas when
both summaries parse completely. If Codex prints a lane cost summary,
`token-summary.json` also records `cost_usd` and `summary.json` compares
proxy-minus-direct cost; if a lane does not report cost, the cost comparison is
marked `missing` rather than estimated.
`summary.json` also records the provider diagnostic `mvp_usefulness`:
cache-adjusted input must not regress using cached input multiplier `0.10`,
cache-hit ratio must not drop by more than `0.05`, and cost must not regress
when both lanes report it. Raw total-token regression is retained as a warning
when billing-equivalent input improves, and remains a failure when
billing-equivalent input is also worse or unavailable. It also records a
provider-diagnostic `completion_contract` such as `provider_usage_cache_cost`
or `provider_usage_cache` with `cost_status="unavailable"`. Missing cost is
never estimated. `trajectory_comparison.interpretation` separately reports
whether both lanes produced Codex JSON events and whether completed command
counts and command-output size estimates matched; a failed trajectory match
does not by itself fail the run, but it prevents treating provider/cache deltas
as trajectory-normalized evidence. A measured token regression or incomplete
token summary exits non-zero even when both Codex lanes and the DB query
succeed. With `--query-db`, the
proxy lane also writes `db-proof.sql`,
`db-proof.stdout.txt`, `db-proof.stderr.txt`, and `db-proof-result.json`. The
proxy lane sets `LITELLM_PROXY_RUN_MARKER=<marker>`; `bin/codex-litellm` maps
that opt-in value to `X-LLM-Proxy-Run`; it also sends
`X-LLM-Proxy-Project` from `CODEX_LITELLM_PROJECT`. Analytics persists
those as `request_metadata.litellm_proxy_run_marker` and
`request_metadata.litellm_proxy_project` for DB correlation. The proxy lane
also sets `CODEX_LITELLM_CLIENT=codex`; the wrapper sends it as
`X-LLM-Proxy-Client`, and analytics persists
`request_metadata.litellm_proxy_client` for proof grouping. The DB query uses
the run marker when present and falls back to the proxy lane time window plus
`--db-window-grace-seconds` for buffered ingestion. A proxy DB proof row is
necessary but not sufficient: usefulness requires comparing direct-vs-proxy
Codex account depletion first. Provider-reported tokens, cached input behavior,
and observed cost only when Codex reports it are secondary diagnostics.

Keep LiteLLM `general_settings.forward_client_headers_to_llm_api` disabled in
this deployment. LiteLLM forwards arbitrary `x-*` request headers upstream when
that setting is enabled, while `X-LLM-Proxy-Run`, `X-LLM-Proxy-Project`, and
`X-LLM-Proxy-Client` are local analytics attribution headers.

Version/source-surface: TechDocs tenants `openai-codex-docs` from
<https://developers.openai.com>, `litellm` from <https://docs.litellm.ai>, and
`anthropic-claude-docs` from <https://claude.com> / <https://platform.claude.com>
were fetched on 2026-06-23; local dependencies are `litellm[proxy]` and
`headroom-ai==0.27.0`, while CLI versions are host-installed. The wrapper
contract follows those docs: Codex provider/auth config lives under
`CODEX_HOME` and uses `base_url`, `env_key`, `openai_base_url`, a launch-time
`OPENAI_BASE_URL`, and `wire_api = "responses"`;
Claude Code routes through LiteLLM with `ANTHROPIC_BASE_URL`,
`ANTHROPIC_AUTH_TOKEN`, `/v1/messages`, and gateway model discovery.
The Codex wrapper intentionally does not set `supports_websockets = true` yet:
that would let Codex use LiteLLM's Responses WebSocket route, which still needs
runtime proof that this repo's compression callback and analytics correlation
run on every `response.create` frame. The supported proof path is HTTP
Responses until that evidence exists.

ChatGPT OAuth is persistent via `./data/chatgpt:/data/chatgpt` and
`CHATGPT_TOKEN_DIR=/data/chatgpt`. If the sibling
`../litellm-proxy/data/chatgpt/auth.json` exists, `make auth-import` copies it
without printing its contents.

The compose stack sends LiteLLM OTel v2 traces to Phoenix using the
`arize_phoenix` callback and keeps prompt/response capture disabled with
`OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=no_content`. Open WebUI is
configured to forward OpenAI-compatible requests directly to the owned LiteLLM
service, but its own OTel exporter is disabled so routine UI health checks and
sqlite connection spans do not pollute Phoenix. The analytics backend sends
traces to Phoenix when OTel is enabled and continues to expose `/dashboard`,
`/health`, `/ready`, `/stats`, `/metrics`, `/stats/dashboard`,
`/records/compression`, `/simulations/runs`, and `/mcp/` independently.

## Compression Library Callback Boundary

The LiteLLM config uses `config/headroom_litellm_callback.py` as a small
callback adapter. It implements LiteLLM's class callback loading surface and
selects Headroom's built-in `HEADROOM_SAVINGS_PROFILE`, defaulting to
`agent-90` for local compression. The default stack leaves
`HEADROOM_API_KEY` unset, so no extra profile-specific environment variable is
required.

Responses tool-output mutation is disabled by default with
`HEADROOM_RESPONSES_MUTABLE_OUTPUT_COMPRESSION=false`. The default-wrapper
12-turn resumed `gpt-5.5` direct-vs-proxy Codex proof still skipped mutable
Responses compression and remained provider-negative. The mutable-on experiment
`codex-savings-direct-first-20260626T142720Z` proved both account-capacity
shareability and provider/cache savings with the gate enabled temporarily for
the proxy runtime. Keep mutable output compression as an explicit experiment
knob until it is intentionally promoted into the default stack. The callback
records skipped executions and still captures provider/cache/Phoenix metadata
unless mutation is explicitly enabled for a separate fresh proof.
The passthrough-off diagnostic
`codex-savings-passthrough-off-20260626T130210Z` is not that experiment; it
removed cache-sensitive outbound fields and failed provider/cache diagnostics.

For a practical post-change Codex proof, use the resumed-session harness rather
than a one-turn smoke:

```bash
uv run python scripts/e2e_agent90_usefulness.py \
  --marker codex-gpt55-resumed-$(date -u +%Y%m%dT%H%M%SZ) \
  --model gpt-5.5 \
  --session-turns 12 \
  --task-lines 1800 \
  --min-combined-input-tokens 1000000 \
  --account-snapshot-settle-seconds 240 \
  --account-snapshot-attempts 4 \
  --query-db \
  --yolo \
  --execute
```

Run MITM as a separate full-fidelity request-shape trace for the same model and
marker family when explaining any quota delta:

```bash
python3 scripts/mitm_codex_capture.py --lane direct --model gpt-5.5 --disable-websockets-for-capture --execute
python3 scripts/mitm_codex_capture.py --lane proxy --model gpt-5.5 --no-bypass-localhost --execute
```

Codex Responses calls preserve cache-sensitive fields through LiteLLM's
ChatGPT adapter by default. This keeps native request identity fields such as
`model`, `prompt_cache_key`, `client_metadata`, `service_tier`,
`parallel_tool_calls`, `previous_response_id`, `text`, and `truncation` in the
provider body via LiteLLM `extra_body`. The callback also records
continuation/cache diagnostics for provider-row forensics: top-level field
presence, `previous_response_id` presence, input item type counts, output item
count, and last input item type. Set
`HEADROOM_RESPONSES_CHATGPT_PROVIDER_PASSTHROUGH=false` only for explicit
field-drop experiments. The `codex-savings-passthrough-off-20260626T130210Z`
proof showed this setting is not a savings fix: LiteLLM outbound MITM removed
`client_metadata` and `prompt_cache_key`, and provider/cache diagnostics got
worse. Do not treat field preservation itself as a compression usefulness
proof.

This callback adapter is the entire Headroom boundary. Do not add Headroom
CLI/proxy/MCP or dashboard workflows to this repository; add only local adapter
code that imports documented library surfaces needed by LiteLLM or the CCR
adapter.

## Codex Models

Run `make models` to repopulate `config/litellm.yaml` from
`codex debug models`. The generated entries expose each API-supported Codex
model slug directly and map it to LiteLLM's ChatGPT subscription provider as
`chatgpt/<slug>`.
