# Analytics Observability Evidence

This runbook records the proof contract for analytics work. It is intentionally
E2E-first: for integration changes, collect runtime evidence before adding or
running unit tests.

## Scope

- Custom analytics backend: <http://127.0.0.1:8010>
- LiteLLM public proxy: <http://127.0.0.1:4000>
- Analytics MCP: <http://127.0.0.1:8010/mcp/>
- Open WebUI: <http://127.0.0.1:8080>
- Phoenix: <http://127.0.0.1:6006>
- Analytics PostgreSQL: `127.0.0.1:${ANALYTICS_POSTGRES_PORT:-55432}`

Do not print ChatGPT OAuth files, LiteLLM master keys, request prompts, model
responses, original chunks, or compressed chunks unless a task explicitly opts
into content inspection. The default Compose stack disables GenAI message
content capture with `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=no_content`.

## Evidence Header

Record this context in capsule status snapshots or issue comments:

```bash
date -u +"%Y-%m-%dT%H:%M:%SZ"
git rev-parse --short HEAD
git status --short
docker compose ps
```

Use a unique marker when a smoke script supports it, or copy the marker printed
by the script into the status snapshot.

## Module Shape Evidence

For analytics backend work, include a small module-shape snapshot before
claiming a slice is ready:

```bash
find src/litellm_proxy_headroom/analytics -name '*.py' -print0 | xargs -0 wc -l | sort -n
uv run python - <<'PY'
import ast
from pathlib import Path

for path in Path("src/litellm_proxy_headroom/analytics/domain").glob("*.py"):
    tree = ast.parse(path.read_text())
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    forbidden = ("fastapi", "sqlalchemy", "litellm", "headroom", "redis", "opentelemetry")
    found = [name for name in imports if name.split(".")[0] in forbidden]
    if found:
        raise SystemExit(f"{path}: forbidden imports {found}")
print("domain_import_check=ok")
PY
```

Expected evidence:

- New API, MCP, and OTel behavior is in focused adapter modules.
- `callback.py`, `repositories.py`, and `models.py` do not grow just to carry
  unrelated route, tool, or telemetry behavior.
- Any file over about 250 lines is either a known outlier or has an extraction
  follow-up in the capsule status.

## Backend Health

```bash
curl -fsS http://127.0.0.1:8010/health
curl -fsS http://127.0.0.1:8010/ready
uv run alembic current
```

Expected evidence:

- `/health` returns `status=healthy`.
- `/ready` returns `status=ready` and `database_ready=true`.
- Alembic reports `0001_analytics (head)` until a later migration exists.

## E2E Smoke Gates

Run these before unit tests for analytics changes:

```bash
HEADROOM_ANALYTICS_URL=http://127.0.0.1:8010 \
uv run python scripts/e2e_headroom_ccr_smoke.py

HEADROOM_ANALYTICS_URL=http://127.0.0.1:8010 \
  uv run python scripts/e2e_litellm_buffer_smoke.py

uv run python scripts/e2e_mcp_otel_smoke.py

uv run python scripts/e2e_query_stats_smoke.py

uv run python scripts/e2e_dashboard_stats_smoke.py

uv run python scripts/e2e_simulation_smoke.py

make e2e

uv run python scripts/e2e_analytics_smoke.py
```

Expected evidence:

- CCR smoke prints `headroom_ccr_smoke=ok` with a hash, marker, and retrieval
  count. This proves the Headroom library `CompressionStoreBackend`
  compatibility path, not a Headroom container.
- LiteLLM buffer smoke prints `litellm_buffer_smoke=ok` with submitted and
  delivered counts.
- MCP/OTel smoke prints `mcp_otel_smoke=ok` with a marker, CCR hash, MCP
  retrieval event ID, and retrieval count delta.
- Query/stats smoke prints `query_stats_smoke=ok` with a marker, request key,
  filtered stats values, records total, provider breakdown value, and a
  PostgreSQL spot-check chunk count.
- Dashboard stats smoke prints `dashboard_stats_smoke=ok` with a marker,
  provider, request count, token savings, negative-savings count,
  estimated-vs-provider token delta, cost-savings value, and distribution
  bounds. It uses direct PostgreSQL spot checks for the same provider.
- Simulation smoke prints `simulation_smoke=ok` with a marker, simulation key,
  simulated tokens saved, production tokens saved, duplicate/idempotency flag,
  and database result count. It proves simulation rows are separate from
  production compression executions.
- `make e2e` prints `health_status=200`, `chat_status=200`,
  `analytics_stats_status=200`, and an analytics request-count increase.
- Analytics smoke prints `analytics_smoke=ok` and `duplicate=True`.

## Stats And Metrics

Take snapshots after the smoke commands complete:

```bash
curl -fsS http://127.0.0.1:8010/stats
curl -fsS 'http://127.0.0.1:8010/stats?provider=openai&model=gpt-smoke'
curl -fsS 'http://127.0.0.1:8010/stats/breakdown?group_by=provider'
curl -fsS 'http://127.0.0.1:8010/stats/dashboard?provider=openai'
curl -fsS 'http://127.0.0.1:8010/records/compression?limit=10'
curl -fsS 'http://127.0.0.1:8010/simulations/runs?limit=10'
curl -fsS http://127.0.0.1:8010/metrics | sed -n '1,40p'
```

Expected evidence:

- `requests`, `executions`, `chunks`, and `tokens_saved` move after ingest.
- `retrievals` moves after CCR retrieval.
- `/metrics` exposes Prometheus text counters matching `/stats`.
- `/records/compression` returns paginated execution-level summaries. Detail
  reads expose content presence booleans and raw-metadata presence booleans,
  not raw chunk content or provider raw metadata by default.
- `/stats/dashboard` returns recomputable dashboard read models: token totals,
  savings distributions, latency distributions, provider estimate deltas,
  measured-vs-estimated cost, cache activity, retrieval frequency, and
  negative-savings/cost-increase signals.
- `/simulations/runs` returns stored simulation records. Simulation results
  link back to source request/execution/chunk IDs and do not mutate production
  source rows.
- Metric labels stay low-cardinality. Do not add request IDs, chunk hashes,
  trace IDs, raw model responses, or tenant-specific free text as labels.

## Logs

Use logs to connect requests to backend behavior:

```bash
docker compose logs --tail=120 analytics-backend
docker compose logs --tail=120 litellm
```

Useful backend signals:

- `POST /ingest/compression HTTP/1.1" 200 OK`
- `PUT /headroom/ccr/{hash} HTTP/1.1" 200 OK`
- `GET /headroom/ccr/{hash} HTTP/1.1" 200 OK`
- `POST /headroom/ccr/{hash}/retrievals HTTP/1.1" 200 OK`
- `POST /mcp/ HTTP/1.1" 200 OK`

If a route returns `422`, capture the route and status first, then inspect the
adapter DTO shape. Keep the failed smoke output in the capsule status if it
changes the implementation.

## PostgreSQL Spot Checks

Prefer counts and identifiers over content columns:

```bash
docker compose exec -T analytics-db psql \
  -U "${ANALYTICS_POSTGRES_USER:-analytics}" \
  -d "${ANALYTICS_POSTGRES_DB:-analytics}" \
  -c "select source,event_type,status,count(*) from analytics_ingestion_events group by 1,2,3 order by 1,2,3;"

docker compose exec -T analytics-db psql \
  -U "${ANALYTICS_POSTGRES_USER:-analytics}" \
  -d "${ANALYTICS_POSTGRES_DB:-analytics}" \
  -c "select ccr_hash,count(*) from analytics_chunk_retrieval_events group by 1 order by count(*) desc limit 10;"
```

Do not select `original_content` or `compressed_content` for routine evidence.

## Phoenix

Open <http://127.0.0.1:6006> and inspect the configured project
`litellm-proxy-headroom`.

Current evidence expectations:

- LiteLLM/Open WebUI traces arrive through the existing OTel configuration.
- Content capture remains disabled by default.
- Analytics backend traces arrive through the configured OTel exporter when
  backend OTel is enabled.

When backend OTel is added, capture a screenshot or written note showing the
LiteLLM request trace with child compression/persistence/retrieval spans, but
do not include prompt, response, or chunk content.

## Backend OTel Evidence

After Slice 05 adds backend OTel instrumentation, collect one of these evidence
sets for every relevant smoke run.

Console or log exporter evidence:

```bash
docker compose logs --tail=200 analytics-backend | rg "headroom.analytics|compression|retrieval|provider|persistence|mcp" -C 2
```

Collector/Phoenix evidence:

```bash
docker compose logs --tail=200 phoenix
docker compose logs --tail=200 litellm | rg "trace|span|otel|phoenix" -i -C 2
docker compose logs --tail=200 analytics-backend | rg "trace|span|otel|phoenix" -i -C 2
```

Expected evidence:

- An analytics ingest/persistence span is correlated with the LiteLLM request
  trace when trace context is available.
- A chunk retrieval span exists for API or MCP retrieval.
- Metrics exist for compression duration, provider tokens, cache reads/writes,
  persistence latency/failures, buffer depth, and MCP retrieval latency.
- No prompt text, response text, original chunk content, or compressed chunk
  content appears in trace attributes, span events, metric labels, or logs by
  default.

If Phoenix UI evidence is used, save only the trace/span names, timing,
relationship, and non-sensitive attributes in the status snapshot. Screenshots
are optional; written trace notes are acceptable when they include the smoke
marker and timestamp.

## Artifact Discipline

Do not rely on terminal scrollback. Copy the command, exact output, timestamp,
and any marker/hash into the active plan-capsule status snapshot after a
meaningful validation pass.
