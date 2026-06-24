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
callback shim, `config/headroom_litellm_callback.py`, which imports Headroom
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

Smoke the CCR compatibility contract used by the imported compression library:

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
`src/litellm_proxy_headroom/analytics/`. API, LiteLLM callback, CCR
compatibility, and OTel adapters are layered around those modules.

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
For backend setup, configuration, data flow, retention, extension points, and
operational trade-offs, use
[docs/analytics-backend.md](docs/analytics-backend.md).

The custom analytics backend exposes MCP at:

```text
http://127.0.0.1:8010/mcp/
```

CCR marker retrieval stays on the local analytics path. Imported compression
library code writes stored chunks through the `HEADROOM_CCR_BACKEND` entry
point and the bounded `/headroom/ccr` compatibility routes; agents retrieve
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
  counts and is the signal for API billing-equivalent savings.
- `Combined saving` uses billing-equivalent input:
  `uncached_provider_input + cached_provider_input * cached_input_multiplier`,
  compared with estimated-before input tokens. The default cached-input
  multiplier is `0.10` for the current OpenAI GPT-5.x text cached-input
  pricing ratio as of 2026-06-23 and can be overridden with
  `ANALYTICS_CACHED_INPUT_COST_MULTIPLIER` when the provider, tier, or pricing
  changes.
- `negative_savings_executions` counts executions where compression expanded
  token count. `cost_increase_provider_calls` counts provider calls where the
  measured cost exceeded the estimated baseline.
- Dashboard cost fields compare `provider_calls.cost_total` with estimated
  rows in `cost_calculations`; missing provider cost stays `null` rather than
  becoming a fake zero-dollar value.
- Primary-usefulness status is separate from the one-sided value metrics. A
  useful result requires a direct-vs-proxy Codex CLI proof using aggregate
  provider usage/cost and cache hit across the whole Codex turn/provider-call
  sequence.

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
```

These wrappers read `.env`, do not print secret values, and generate non-secret
runtime config in managed local state. To put them on your PATH without changing
global agent config, symlink the wrapper names:

```bash
ln -sf "$PWD/bin/codex-litellm" "$HOME/.local/bin/codex-litellm"
ln -sf "$PWD/bin/claude-litellm" "$HOME/.local/bin/claude-litellm"
ln -sf "$PWD/bin/opencode-litellm" "$HOME/.local/bin/opencode-litellm"
```

Current support levels are maintained in
[docs/agent-cli-support.md](docs/agent-cli-support.md). Short version: Codex is
the proven path, Claude Code has an isolated wrapper but still needs an
Anthropic-compatible LiteLLM route proof, OpenCode has managed config
generation with route proof pending, and GitHub Copilot CLI is isolation-only
until GitHub exposes a documented local BYOK/base-URL provider surface for the
target model.

`bin/codex-litellm` sets `CODEX_HOME` to the managed `~/.codex-headroom`
directory, writes `config.toml` and `litellm.config.toml`, symlinks native
Codex state such as `sessions` and `auth.json` from `~/.codex`, configures the
LiteLLM Responses provider at `http://127.0.0.1:4000/v1`, and adds the
analytics MCP endpoint for local compression-marker retrieval. The wrapper refuses
`CODEX_LITELLM_HOME=$HOME/.codex` by default because it does not own Headroom's
snapshot/unwrap machinery for mutating a user's native Codex config; choose an
isolated directory instead. It also maps `CODEX_LITELLM_PROJECT` to
`X-LiteLLM-Proxy-Project` for local analytics attribution, defaulting the value
from the launch directory name when unset. It maps `CODEX_LITELLM_CLIENT` to
`X-LiteLLM-Proxy-Client` for local analytics attribution, defaulting to
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

`bin/claude-litellm` sets Claude Code's LiteLLM gateway environment, maps
`LITELLM_MASTER_KEY` to both Anthropic API key env names used by Claude Code,
limits settings to project scope so user `apiKeyHelper` config does not bypass
LiteLLM, and writes a generated analytics MCP config under `~/.claude-headroom`
by default. Set `CLAUDE_LITELLM_HOME` to move that managed home, or
`CLAUDE_LITELLM_STATE_DIR` for compatibility with earlier wrapper tests/scripts.
Set `CLAUDE_LITELLM_BASE_URL` when LiteLLM is not on `http://127.0.0.1:4000`,
and `CLAUDE_LITELLM_ANALYTICS_URL` when analytics is not on
`http://127.0.0.1:8010`. The wrapper validates those URLs before writing
managed config and never writes `LITELLM_MASTER_KEY` into `mcp.json`. With the
current ChatGPT-backed model aliases, real Claude Code smoke reached LiteLLM
and analytics but failed with a 400 because the model group rejects Claude
Code's system-message request shape. Claude Code remains route-gated until an
Anthropic-compatible LiteLLM model route is proven.

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
to spend real provider calls:

```bash
uv run python scripts/e2e_agent90_usefulness.py --marker AGENT90_PROOF --model gpt-5.5 --execute --query-db
```

Interpret the proof at the run level, not from one selected provider call. A
Codex CLI turn can produce a sequence of provider calls, so the pass/fail
decision comes from `summary.json` aggregate lane totals and
`token_comparison.mvp_usefulness`: direct vs proxy input, cached input, output,
reasoning, total tokens, cost when present, billing-equivalent input, and
cache-hit ratio across the whole Codex turn/provider-call sequence. A proxy DB
row proving compression ran is necessary evidence, but it does not prove
usefulness unless the aggregate direct-vs-proxy comparison passes.

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
`summary-lines.txt`, `token-summary.json`, and a top-level `summary.json` with
direct-vs-proxy token deltas when both summaries parse completely. If Codex
prints a lane cost summary, `token-summary.json` also records `cost_usd` and
`summary.json` compares proxy-minus-direct cost; if a lane does not report
cost, the cost comparison is marked `missing` rather than estimated.
`summary.json` also records `mvp_usefulness`: total tokens must not regress,
cache-adjusted input must not regress using cached input multiplier `0.10`,
cache-hit ratio must not drop by more than `0.05`, and cost must not regress
when both lanes report it. It also records `completion_contract`: when both
lanes report observed cost, the passing scope is `provider_usage_cache_cost`;
when Codex reports no lane cost, the passing scope can only be
`provider_usage_cache` with `cost_status="unavailable"`. Missing cost is never
estimated. A measured regression or incomplete token summary exits non-zero
even when both Codex lanes and the DB query succeed. With `--query-db`, the
proxy lane also writes `db-proof.sql`,
`db-proof.stdout.txt`, `db-proof.stderr.txt`, and `db-proof-result.json`. The
proxy lane sets `LITELLM_PROXY_RUN_MARKER=<marker>`; `bin/codex-litellm` maps
that opt-in value to `X-LiteLLM-Proxy-Run`; it also sends
`X-LiteLLM-Proxy-Project` from `CODEX_LITELLM_PROJECT`. Analytics persists
those as `request_metadata.litellm_proxy_run_marker` and
`request_metadata.litellm_proxy_project` for DB correlation. The proxy lane
also sets `CODEX_LITELLM_CLIENT=codex`; the wrapper sends it as
`X-LiteLLM-Proxy-Client`, and analytics persists
`request_metadata.litellm_proxy_client` for proof grouping. The DB query uses
the run marker when present and falls back to the proxy lane time window plus
`--db-window-grace-seconds` for buffered ingestion. A proxy DB proof row is
necessary but not sufficient: usefulness requires comparing provider-reported
direct-vs-proxy tokens and cached input behavior, plus observed cost only when
Codex reports it.

Keep LiteLLM `general_settings.forward_client_headers_to_llm_api` disabled in
this deployment. LiteLLM forwards arbitrary `x-*` request headers upstream when
that setting is enabled, while `X-LiteLLM-Proxy-Run` and
`X-LiteLLM-Proxy-Project` and `X-LiteLLM-Proxy-Client` are local analytics
attribution headers.

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
service. The analytics backend sends traces to Phoenix when OTel is enabled and
continues to expose `/dashboard`, `/health`, `/ready`, `/stats`, `/metrics`,
`/stats/dashboard`, `/records/compression`, `/simulations/runs`, and `/mcp/`
independently.

## Compression Library Callback Boundary

The LiteLLM config uses `config/headroom_litellm_callback.py` as a small
Headroom v0.27.0 compatibility shim. It keeps LiteLLM's class callback loading
working and selects Headroom's built-in `HEADROOM_SAVINGS_PROFILE`, defaulting
to `agent-90` for local compression. The default stack leaves
`HEADROOM_API_KEY` unset, so no extra profile-specific environment variable is
required.

This callback shim is the entire Headroom boundary. Do not add Headroom
CLI/proxy/MCP or dashboard workflows to this repository; add only local adapter
code that imports documented library surfaces needed by LiteLLM or CCR
compatibility.

## Codex Models

Run `make models` to repopulate `config/litellm.yaml` from
`codex debug models`. The generated entries expose each API-supported Codex
model slug directly and map it to LiteLLM's ChatGPT subscription provider as
`chatgpt/<slug>`.
