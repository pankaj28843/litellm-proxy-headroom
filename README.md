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

Dashboard/read APIs are exposed by the custom backend:

- `GET /dashboard` with dashboard filters: `preset`, `from`, `to`, `provider`,
  `model`, `strategy`, `tenant_id`, `team_id`, `status`, `negative_savings`,
  `live`, and `paused`.
- `GET /dashboard/partials/live`, `/controls`, `/summary`, `/activity`,
  `/breakdowns`, `/records`, and `/simulations` for HTMX refresh.
- `GET /stats` with filters: `from`, `to`, `provider`, `model`, `strategy`,
  `tenant_id`, `team_id`, `status`, and `negative_savings`.
- `GET /stats/breakdown?group_by=provider|model|strategy|tenant|team|status`
  with the same filters.
- `GET /stats/dashboard` with the same filters for dashboard-grade totals,
  distributions, latency, cost, cache, retrieval frequency, negative-savings,
  and estimated-vs-provider token deltas.
- `GET /records/compression` with the same filters plus `limit` and `offset`.
- `GET /records/compression/{request_key}` for request detail. Routine detail
  responses expose hashes, counts, booleans, and token measurements, not raw
  provider metadata or chunk content.
- `POST /simulations/runs` to replay selected historical executions under
  alternate compression/pricing assumptions and store results separately.
- `GET /simulations/runs` and `GET /simulations/runs/{simulation_key}` for
  simulation summaries and results.

Read the numbers as recomputed source-row totals, not cached dashboard state:

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
```

Both wrappers read `.env`, do not print secret values, and generate non-secret
runtime config under `tmp/`. To put them on your PATH without changing global
agent config, symlink the wrapper names:

```bash
ln -sf "$PWD/bin/codex-litellm" "$HOME/.local/bin/codex-litellm"
ln -sf "$PWD/bin/claude-litellm" "$HOME/.local/bin/claude-litellm"
```

`bin/codex-litellm` sets a repo-owned `CODEX_HOME`, configures the LiteLLM
Responses provider at `http://127.0.0.1:4000/v1`, and adds the analytics MCP
endpoint.

`bin/claude-litellm` sets Claude Code's LiteLLM gateway environment, limits
settings to project scope so user `apiKeyHelper` config does not bypass
LiteLLM, and passes a repo-owned analytics MCP config. With the current
ChatGPT-backed model aliases, Claude Code reaches LiteLLM and analytics but
can receive a 400 from the model group because Claude Code sends system
messages and the current ChatGPT provider path rejects them.

Version/source-surface: TechDocs tenants `openai-codex-docs` from
<https://developers.openai.com>, `litellm` from <https://docs.litellm.ai>, and
`anthropic-claude-docs` from <https://claude.com> / <https://platform.claude.com>
were fetched on 2026-06-23; local dependencies are `litellm[proxy]` and
`headroom-ai==0.27.0`, while CLI versions are host-installed. The wrapper
contract follows those docs: Codex provider/auth config lives under
`CODEX_HOME` and uses `base_url`, `env_key`, and `wire_api = "responses"`;
Claude Code routes through LiteLLM with `ANTHROPIC_BASE_URL`,
`ANTHROPIC_AUTH_TOKEN`, `/v1/messages`, and gateway model discovery.

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
working and selects Headroom's built-in `agent-90` local compression profile.
The default stack leaves `HEADROOM_API_KEY` unset, so no extra profile-specific
environment variable is required.

This callback shim is the entire Headroom boundary. Do not add Headroom
CLI/proxy/MCP or dashboard workflows to this repository; add only local adapter
code that imports documented library surfaces needed by LiteLLM or CCR
compatibility.

## Codex Models

Run `make models` to repopulate `config/litellm.yaml` from
`codex debug models`. The generated entries expose each API-supported Codex
model slug directly and map it to LiteLLM's ChatGPT subscription provider as
`chatgpt/<slug>`.
