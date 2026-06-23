# Analytics Backend

This backend is the owned ingress for Headroom compression analytics. LiteLLM
remains the OpenAI-compatible control plane on port 4000; Headroom runs as a
library/callback/CCR backend inside that path; the analytics backend owns HTTP
ingest, CCR retrieval, MCP, stats, metrics, dashboard-ready APIs, simulations,
and PostgreSQL persistence on port 8010.

There is no default Headroom proxy or Headroom MCP container. Do not add one
unless a future requirement proves the owned LiteLLM plus backend path cannot
cover the behavior through supported extension points.

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
Open WebUI -> LiteLLM proxy -> Headroom callback/library
                              -> bounded analytics HTTP buffer
                              -> analytics backend
                              -> PostgreSQL

Headroom CompressionStoreBackend -> analytics backend CCR endpoints
MCP clients                      -> analytics backend /mcp/
Phoenix                          <- LiteLLM and analytics OTel exporters
```

Core boundaries:

- `analytics/domain`: compression, provider usage, economics, chunks,
  simulation, and ports. No framework or adapter imports.
- `analytics/application`: commands, services, read models, buffering, and
  simulation schemas.
- `analytics/adapters`: FastAPI, FastMCP, LiteLLM, Headroom, PostgreSQL, and
  OpenTelemetry adapters.

## Configuration

Important local variables:

| Variable | Purpose |
|---|---|
| `ANALYTICS_DATABASE_URL` | SQLAlchemy asyncio URL for the analytics PostgreSQL database. |
| `ANALYTICS_BACKEND_PORT` | Host port for the backend, default `8010`. |
| `HEADROOM_ANALYTICS_URL` | Host-side analytics backend URL for scripts and local LiteLLM runs. Compose injects the container URL for LiteLLM. |
| `HEADROOM_ANALYTICS_TIMEOUT_SECONDS` | Short callback HTTP timeout. Keep this low so analytics does not block model requests. |
| `HEADROOM_ANALYTICS_BUFFER_SIZE` | Bounded callback queue depth. Full queues drop analytics and increment buffer counters. |
| `HEADROOM_ANALYTICS_BUFFER_WORKERS` | Async delivery workers for callback ingestion. |
| `HEADROOM_ANALYTICS_MAX_ATTEMPTS` | Delivery attempts before an event is counted failed. |
| `HEADROOM_ANALYTICS_PENDING_LIMIT` | Maximum in-memory LiteLLM request captures waiting for a response callback. |
| `HEADROOM_CCR_BACKEND` | Headroom CCR entry point. Use `analytics-postgres`. |
| `HEADROOM_CCR_ANALYTICS_TIMEOUT_SECONDS` | Timeout for the Headroom CCR HTTP backend. |
| `HEADROOM_CCR_LOCAL_CACHE_ENTRIES` | Per-process CCR read-through cache limit. |
| `HEADROOM_CCR_TENANT_PREFIX` | Optional tenant prefix for CCR backend metadata. |
| `HEADROOM_ANALYTICS_OTEL_ENABLED` | Enables backend tracing and metrics setup. |
| `HEADROOM_ANALYTICS_OTEL_CONSOLE` | Emits spans/metrics to logs for local proof. |
| `HEADROOM_ANALYTICS_OTEL_METRIC_EXPORT_INTERVAL_MS` | OTel metric export interval. |
| `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` | Phoenix or collector trace endpoint. |
| `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT` | Optional metrics collector endpoint. |

## Data Flow

LiteLLM loads `config/headroom_litellm_callback.py`, which delegates to
Headroom's LiteLLM callback and uses a local `agent-90` compression profile.
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

Headroom-compatible CCR storage uses the `analytics-postgres` entry point and
the supported `CompressionStoreBackend` protocol. It writes and reads through:

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
- `PUT /headroom/ccr/{hash}`
- `GET /headroom/ccr/{hash}`
- `POST /headroom/ccr/{hash}/retrievals`
- `POST /mcp/`

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
range, provider, model, strategy, tenant, team, status, and negative savings
where applicable. Large record reads are paginated.

## Dashboard

The custom analytics backend owns `/dashboard`. It does not mount, alias, or
proxy Headroom's dashboard. LiteLLM stays on port 4000 and Headroom remains a
library/callback/CCR backend in that request path.

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
| `live`, `paused` | Live polling state. |

The first viewport is the operational story: Current Impact, then Activity And
Risk, then Investigation, Recent Records, and Simulation Replay. Those panels
are computed from source rows through the same read models as `/stats/dashboard`,
`/stats/breakdown`, `/records/compression`, and `/simulations/runs`.

Empty states are truthful. If no persisted compression executions match the
filters, the page tells the operator to run the dashboard smoke seed or widen
the range. To create demo evidence without a paid model call:

```bash
DASHBOARD_STATS_SMOKE_MARKER=dashboard-demo-$(date +%s) \
  uv run python scripts/e2e_dashboard_stats_smoke.py
```

Then open `/dashboard?preset=all&provider=<printed-provider>`.

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

The LiteLLM callback uses `hash_only` by default. Headroom CCR entries store
content because retrieval needs the compressed chunk payload. Routine record
detail APIs expose content-present booleans and hashes, not raw provider
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

Useful log probes:

```bash
docker compose logs --tail=200 analytics-backend | rg "ingest|ccr|mcp|simulation|trace|span|otel" -i
docker compose logs --tail=200 litellm | rg "headroom|analytics|trace|span|otel" -i
docker compose logs --tail=200 phoenix | rg "trace|span|otlp|error" -i
```

## Validation Order

For analytics changes, prove usefulness before unit tests:

1. Backend readiness and migration head.
2. Runtime ingest/retrieve/stats/metrics smoke with a unique marker.
3. Headroom CCR smoke through the supported backend entry point.
4. LiteLLM callback buffer smoke.
5. MCP retrieval and metrics smoke.
6. Dashboard stats and simulation smokes.
7. `make e2e` for the real LiteLLM-controlled path when ChatGPT auth is
   available.
8. Only then run `uv run pytest`, Ruff, and migration downgrade/upgrade.

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
- The Headroom CCR adapter uses a synchronous HTTP client because Headroom's
  current store backend protocol is synchronous. The custom backend itself is
  asyncio-first.
- Automatic retention enforcement, Redis write-through caching, and dashboard
  UI polish are intentionally outside the first useful slice.
