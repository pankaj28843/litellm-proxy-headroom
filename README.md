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
callback shim, `config/headroom_litellm_callback.py`, which wraps Headroom's
compression callback and posts analytics to the custom backend.
On a machine without existing ChatGPT device credentials, LiteLLM prompts for
the ChatGPT OAuth device flow during startup.

The Headroom proxy container is not part of the default runtime. Headroom remains
an installed library dependency for compression callbacks and CCR compatibility
adapters; the owned LiteLLM proxy, analytics backend, PostgreSQL storage, and
custom MCP endpoint are the control plane.

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

Smoke Headroom's supported CCR backend contract against the analytics backend:

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
`src/litellm_proxy_headroom/analytics/`. API, LiteLLM callback, Headroom CCR,
and OTel adapters are layered around those modules.

Headroom-compatible CCR code can use the analytics backend through the plugin
entry point when a library path needs the `CompressionStoreBackend` protocol:

```bash
HEADROOM_CCR_BACKEND=analytics-postgres
HEADROOM_ANALYTICS_URL=http://analytics-backend:8010
```

The plugin implements Headroom's `CompressionStoreBackend` protocol and sends
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

## Headroom Callback

The LiteLLM config uses `config/headroom_litellm_callback.py` as a small
Headroom v0.27.0 compatibility shim. It keeps LiteLLM's class callback loading
working and selects Headroom's built-in `agent-90` local compression profile.
The default stack leaves `HEADROOM_API_KEY` unset, so no extra profile-specific
environment variable is required.

## Codex Models

Run `make models` to repopulate `config/litellm.yaml` from
`codex debug models`. The generated entries expose each API-supported Codex
model slug directly and map it to LiteLLM's ChatGPT subscription provider as
`chatgpt/<slug>`.
