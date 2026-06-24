# Analytics Backend

This backend is the owned ingress for compression analytics. LiteLLM remains
the OpenAI-compatible control plane on port 4000. A small Headroom library
integration is imported only behind the LiteLLM callback and CCR-compatible
adapter; the analytics backend owns HTTP ingest, CCR retrieval, MCP, stats,
metrics, custom dashboard APIs, simulations, and PostgreSQL persistence on
port 8010.

There is no Headroom CLI, `headroom proxy`, Headroom MCP container, Headroom
dashboard, Headroom API service, or Headroom Compose service in this repo. Do
not add one. Keep Headroom engagement limited to imported library code behind
local adapters.

## Local Setup

Install dependencies and start the stack:

```bash
uv sync
make
```

Apply or verify migrations:

```bash
uv run alembic upgrade head
uv run alembic current
```

Run the backend outside Compose when debugging:

```bash
docker compose up -d analytics-db phoenix
uv run uvicorn litellm_proxy_headroom.analytics.adapters.api.app:create_app \
  --factory --host 127.0.0.1 --port 8010
```

Health checks:

```bash
curl -fsS http://127.0.0.1:8010/health
curl -fsS http://127.0.0.1:8010/ready
```

## Runtime Topology

```text
Open WebUI -> LiteLLM proxy -> compression library callback
                              -> bounded analytics HTTP buffer
                              -> analytics backend
                              -> PostgreSQL

Library CompressionStoreBackend -> analytics backend CCR compatibility endpoints
MCP clients                     -> analytics backend /mcp/
Phoenix                         <- LiteLLM and analytics OTel exporters
```

Core boundaries:

- `analytics/domain`: compression, provider usage, economics, chunks,
  simulation, and ports. No framework or adapter imports.
- `analytics/application`: commands, services, read models, buffering, and
  simulation schemas.
- `analytics/adapters`: FastAPI, FastMCP, LiteLLM, CCR compatibility,
  PostgreSQL, and OpenTelemetry adapters.

## Configuration

Important local variables:

| Variable | Purpose |
|---|---|
| `ANALYTICS_DATABASE_URL` | SQLAlchemy asyncio URL for the analytics PostgreSQL database. |
| `ANALYTICS_BACKEND_PORT` | Host port for the backend, default `8010`. |
| `ANALYTICS_CACHED_INPUT_COST_MULTIPLIER` | Billing-equivalent multiplier for provider-reported cached input tokens, default `0.10`. Use this to match current provider pricing without changing stored token rows. OpenAI prompt-cache pricing for current GPT-5.x text classes lists cached input as 10% of uncached input as of 2026-06-23; override this value for other providers, processing tiers, or future pricing changes. |
| `HEADROOM_ANALYTICS_URL` | Host-side analytics backend URL for scripts and local LiteLLM runs. Compose injects the container URL for LiteLLM. |
| `HEADROOM_ANALYTICS_TIMEOUT_SECONDS` | Short callback HTTP timeout. Keep this low so analytics does not block model requests. |
| `HEADROOM_ANALYTICS_BUFFER_SIZE` | Bounded callback queue depth. Full queues drop analytics and increment buffer counters. |
| `HEADROOM_ANALYTICS_BUFFER_WORKERS` | Async delivery workers for callback ingestion. |
| `HEADROOM_ANALYTICS_MAX_ATTEMPTS` | Delivery attempts before an event is counted failed. |
| `HEADROOM_ANALYTICS_PENDING_LIMIT` | Maximum in-memory LiteLLM request captures waiting for a response callback. |
| `HEADROOM_CCR_BACKEND` | Imported-library CCR entry point. Use `analytics-postgres`. |
| `HEADROOM_CCR_ANALYTICS_TIMEOUT_SECONDS` | Timeout for the CCR compatibility HTTP backend. |
| `HEADROOM_CCR_LOCAL_CACHE_ENTRIES` | Per-process CCR read-through cache limit. |
| `HEADROOM_CCR_TENANT_PREFIX` | Optional tenant prefix for CCR backend metadata. |
| `HEADROOM_ANALYTICS_OTEL_ENABLED` | Enables backend tracing and metrics setup. |
| `HEADROOM_ANALYTICS_OTEL_CONSOLE` | Emits spans/metrics to logs for local proof. |
| `HEADROOM_ANALYTICS_OTEL_METRIC_EXPORT_INTERVAL_MS` | OTel metric export interval. |
| `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` | Phoenix or collector trace endpoint. |
| `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT` | Optional metrics collector endpoint. |

## Data Flow

LiteLLM loads `config/headroom_litellm_callback.py`, which delegates to the
imported library callback and uses a local `agent-90` compression profile.
The adapter captures request correlation, compression measurements, provider
usage, LiteLLM `response_cost` when available, trace context, and raw provider
metadata, then submits a normalized command into a bounded async buffer.

The buffer posts to:

```text
POST /ingest/compression
```

When the LiteLLM payload contains W3C trace context, the HTTP client also
forwards `traceparent` and `tracestate` headers so backend spans can be linked
to the request trace instead of only storing trace IDs in PostgreSQL.

The backend stores source rows for ingestion events, compression requests,
configuration snapshots, executions, chunks, provider calls, token usage,
cache activity, cost calculations, and retrieval events. Stable query fields
are normalized; provider-specific payloads stay in JSONB for later replay.

CCR storage uses the `analytics-postgres` entry point and the supported library
`CompressionStoreBackend` protocol. The `/headroom/ccr/*` paths are internal
compatibility endpoints owned by this backend, not operator routes and not a
Headroom service. They write and read through:

```text
PUT  /headroom/ccr/{hash}
GET  /headroom/ccr/{hash}
POST /headroom/ccr/{hash}/retrievals
```

The generic retrieval API and MCP tool use the same stored chunk records and
record retrieval events:

```text
GET /chunks/{ccr_hash}
POST /mcp/
```

## API Surface

Operational endpoints:

- `GET /health`
- `GET /ready`
- `GET /metrics`
- `GET /dashboard`
- `GET /dashboard/partials/live`
- `GET /dashboard/partials/controls`
- `GET /dashboard/partials/summary`
- `GET /dashboard/partials/activity`
- `GET /dashboard/partials/breakdowns`
- `GET /dashboard/partials/records`
- `GET /dashboard/partials/simulations`

Ingest and retrieval:

- `POST /ingest/compression`
- `GET /chunks/{ccr_hash}`
- `POST /mcp/`

Internal CCR compatibility routes:

- `PUT /headroom/ccr/{hash}`
- `GET /headroom/ccr/{hash}`
- `POST /headroom/ccr/{hash}/retrievals`

Read APIs:

- `GET /stats`
- `GET /stats/breakdown?group_by=provider|model|strategy|tenant|team|status`
- `GET /stats/dashboard`
- `GET /records/compression`
- `GET /records/compression/{request_key}`
- `POST /simulations/runs`
- `GET /simulations/runs`
- `GET /simulations/runs/{simulation_key}`

The stats, breakdown, records, and dashboard endpoints support filters for time
range, provider, model, strategy, tenant, team, status, negative savings, and
data scope where applicable. `data_scope=real` is the default and excludes
rows marked in request metadata as smoke/demo/synthetic/test data. Use
`data_scope=test` for seeded validation rows and `data_scope=all` only for an
explicit mixed-scope investigation. Large record reads are paginated.

## Dashboard

The custom analytics backend owns `/dashboard`. It does not mount, alias, or
proxy another dashboard. LiteLLM stays on port 4000 and the compression library
remains an imported callback/CCR compatibility dependency in that request path.

The dashboard implementation is server-rendered:

- Jinja templates live under
  `src/litellm_proxy_headroom/analytics/adapters/api/templates/dashboard/`.
- Static CSS lives under
  `src/litellm_proxy_headroom/analytics/adapters/api/static/dashboard/` and is
  served from `/dashboard/static`.
- HTMX refreshes use `/dashboard/partials/*` routes with
  `hx-include="#dashboard-filters"` so date and filter state is preserved.
- The live region polls every 15 seconds only when `live=true` and
  `paused=false`; pause removes the polling attributes and resume restores them.

Supported dashboard query controls:

| Query | Purpose |
|---|---|
| `preset` | `15m`, `1h`, `24h`, `7d`, `30d`, `all`, or `custom`. |
| `from`, `to` | ISO datetimes for custom ranges. If either is present, the effective preset is `custom`. |
| `provider`, `model`, `strategy` | Provider/model/compression-strategy filters. |
| `tenant_id`, `team_id` | Tenant and team filters. |
| `status` | Execution status filter such as `succeeded`, `failed`, or `running`. |
| `negative_savings` | `true` for expanded-token executions only, `false` for non-negative only. |
| `data_scope` | `real` for operational rows, `test` for smoke/demo/synthetic/test rows, or `all` for both. |
| `live`, `paused` | Live polling state. |

The first viewport is the operational story: primary proof status, Current
Impact, then Activity And Risk, then Investigation, Recent Records, and
Simulation Replay. Those panels are computed from source rows through the same
read models as `/stats/dashboard`, `/stats/breakdown`, `/records/compression`,
and `/simulations/runs`.

Read the dashboard numbers as recomputed source-row totals:

| Field | Source and meaning |
|---|---|
| `requests` | Distinct `compression_requests` rows among matching executions. |
| `executions` | Matching `compression_executions` rows. |
| `chunks` | `compression_chunks` rows for matching executions. |
| `retrievals` | `chunk_retrieval_events` joined through matching chunks. |
| `original_tokens`, `compressed_tokens`, `tokens_saved` | Sums from `compression_executions`; null measurements become zero in aggregate read models. |
| Provider token usage | `token_usage_breakdowns` rows with `measurement_source='provider_reported'`, joined through matching provider calls. These fields are independent of compression savings. |
| Provider cache hit | Provider-reported `cached_input_tokens / input_tokens`. This reflects prompt-cache reuse seen by the upstream provider, not backend cache events. |
| Billing-equivalent saving | `(estimated_before_input - (provider_uncached_input + provider_cached_input * ANALYTICS_CACHED_INPUT_COST_MULTIPLIER)) / estimated_before_input`. This is a pricing-equivalent input estimate, not a strict raw-token reduction. |
| Provider estimate deltas | Diagnostic only. Estimated-before and estimated-after input token rows minus provider-reported input tokens must not be used as operator-facing value proof. |
| Cost fields | Estimated baseline rows from `cost_calculations` compared with measured `provider_calls.cost_total`. |
| Cache fields | `cache_activities` rows joined to matching executions. |
| Negative savings | Executions where `tokens_saved < 0`. |
| Failures | Executions where `status='failed'`; failed rows can still have provider calls and token usage. |
| Primary usefulness | `usefulness.status` and `usefulness.cache_evidence_scope` state whether the dashboard has a passed direct-vs-proxy proof. One-sided dashboard rows remain `unproven`; proof requires aggregate Codex CLI usage/cost/cache comparison over the whole turn/provider-call sequence. |

Empty states are truthful. If no persisted compression executions match the
filters, the default operational view tells the operator to select test/demo
data only when validating fixtures or widen the range. To create demo evidence
without a paid model call:

```bash
DASHBOARD_STATS_SMOKE_MARKER=dashboard-demo-$(date +%s) \
  uv run python scripts/e2e_dashboard_stats_smoke.py
```

Then open `/dashboard?data_scope=test&preset=all&provider=<printed-provider>`.

Templates must not render prompt text, response text, original chunk content, or
compressed chunk content. Routine dashboard surfaces show identifiers, hashes,
counts, status, provider/model labels, token measurements, costs, and
content-present booleans only.

## Content And Retention

Telemetry must not contain prompt text, model responses, original chunks, or
compressed chunks by default. Compose keeps LiteLLM GenAI message capture off
with `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=no_content`.

Chunk rows support three storage patterns:

- `hash_only`: keep hashes and token measurements without content.
- `inline`: store content directly in PostgreSQL.
- `external_ref`: store references to content managed elsewhere.

The LiteLLM callback uses `hash_only` by default. CCR compatibility entries may
store content because retrieval needs the compressed chunk payload. Routine
record detail APIs expose content-present booleans and hashes, not raw provider
metadata or chunk content.

The schema includes `retention_policies` and
`compression_chunks.retention_expires_at` so content and metadata retention can
be enforced later without changing source history. There is not yet an
automatic purge worker. Until one exists, treat deletion or archival as an
explicit operator task and prefer clearing content columns over removing source
measurement rows needed for recomputation.

## Resilience

LiteLLM callback ingestion is best-effort. It uses:

- bounded in-memory queue
- configurable worker count
- short HTTP timeout
- retry with jitter
- delivery, retry, failure, queue-depth, and drop counters
- shutdown flush

Analytics backend persistence is the source of truth once an event reaches
`POST /ingest/compression`. Ingestion is idempotent by source, event type, and
event key. Provider request/response IDs, CCR hashes, trace IDs, and request
keys provide correlation across callbacks, provider calls, chunks, traces, and
retrievals.

If the backend is down, LiteLLM should continue serving requests and the buffer
will record failures or drops. Critical production behavior should not depend
on analytics delivery succeeding.

## Observability Evidence

Use the observability runbook for command-level proof:

```text
docs/analytics-observability-evidence.md
```

Minimum backend evidence:

```bash
docker compose ps
curl -fsS http://127.0.0.1:8010/ready
uv run alembic current
make analytics-smoke
curl -fsS http://127.0.0.1:8010/metrics | sed -n '1,40p'
```

For Phoenix, open <http://127.0.0.1:6006> and inspect the
`litellm-proxy-headroom` project. Record trace names, parent/child
relationships, timing, service names, and non-sensitive attributes. Do not copy
prompt, response, original chunk, or compressed chunk content into evidence.

Phoenix groups generic OTLP spans by project resource attributes. The Compose
backend sets `PHOENIX_PROJECT_NAME=${PHOENIX_PROJECT_NAME:-litellm-proxy-headroom}`,
and the analytics OTel setup copies that value into
`openinference.project.name`. Without that project name, Phoenix places spans in
the `default` project.

Useful log probes:

```bash
docker compose logs --tail=200 analytics-backend | rg "ingest|ccr|mcp|simulation|trace|span|otel" -i
docker compose logs --tail=200 litellm | rg "headroom|analytics|trace|span|otel" -i
docker compose logs --tail=200 phoenix | rg "trace|span|otlp|error" -i
```

## Validation Order

For analytics plumbing changes, prove runtime behavior before unit tests:

1. Backend readiness and migration head.
2. Runtime ingest/retrieve/stats/metrics smoke with a unique marker.
3. CCR compatibility smoke through the supported backend entry point.
4. LiteLLM callback buffer smoke.
5. MCP retrieval and metrics smoke.
6. Dashboard stats and simulation smokes.
7. `make e2e` for the real LiteLLM-controlled path when ChatGPT auth is
   available.
8. Only then run `uv run pytest`, Ruff, and migration downgrade/upgrade.

For compression usefulness claims, the smoke suite and dashboard stats are not
enough. Use the README's Agent-90 Usefulness Harness with actual
`codex exec --json` direct-vs-proxy runs. Smoke with `gpt-5.4-mini`; judge
practical usefulness with `gpt-5.5`; compare aggregate provider-reported usage,
cost when present, and cached-input behavior across the whole Codex
turn/provider-call sequence.

The synthetic smoke suite is:

```bash
make analytics-smoke
```

The real LiteLLM path remains:

```bash
make e2e
```

## Trade-Offs

- Dashboard APIs are computed from source tables rather than materialized
  aggregates. This keeps results reproducible and makes pricing recalculation
  possible; add materialized read models later only with refresh provenance.
- The CCR compatibility adapter uses a synchronous HTTP client because the
  imported store backend protocol is synchronous. The custom backend itself is
  asyncio-first.
- Automatic retention enforcement, Redis write-through caching, and dashboard
  UI polish are intentionally outside the first useful slice.
