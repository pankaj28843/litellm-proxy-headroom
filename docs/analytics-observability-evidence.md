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

Headroom is in scope only as imported library code behind the LiteLLM callback
and CCR-compatible adapter. Do not run Headroom CLI, `headroom proxy`, Headroom
MCP, Headroom dashboard, or any Headroom service while gathering evidence for
this repo.

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

- These smoke gates prove analytics plumbing and read-model recomputability;
  they do not prove compression usefulness. Use the Codex CLI A/B harness for
  usefulness claims.
- CCR smoke prints `headroom_ccr_smoke=ok` with a hash, marker, and retrieval
  count. This proves the Headroom library `CompressionStoreBackend` adapter
  path, not a Headroom container.
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
  bounds. It uses `data_scope=test` and direct PostgreSQL spot checks for the
  same provider.
- Simulation smoke prints `simulation_smoke=ok` with a marker, simulation key,
  simulated tokens saved, production tokens saved, duplicate/idempotency flag,
  and database result count. It proves simulation rows are separate from
  production compression executions.
- `make e2e` prints `health_status=200`, `chat_status=200`,
  `analytics_stats_status=200`, and an analytics request-count increase.
- Analytics smoke prints `analytics_smoke=ok` and `duplicate=True`.

## Dashboard Evidence

The dashboard proof is runtime-first. Seed source rows, verify HTTP/read-model
surfaces, then inspect the browser.

Repeatable seed command:

```bash
DASHBOARD_STATS_SMOKE_MARKER=dashboard-browser-smoke-$(date +%s) \
  uv run python scripts/e2e_dashboard_stats_smoke.py
```

Representative completed seed from the dashboard implementation pass:

```text
marker=dashboard-browser-smoke-1782222979
provider=dashboard-provider-1782222979
model=dashboard-model-1782222979
strategy=dashboard-strategy-1782222979
tenant=dashboard-tenant-1782222979
team=dashboard-team-1782222979
```

The smoke script verifies `/ready`, `POST /ingest/compression`,
`GET /chunks/{ccr_hash}`, `POST /simulations/runs`, `/stats/dashboard`,
`/stats/breakdown`, `/records/compression`, `/dashboard`,
`/dashboard/partials/live`, `/dashboard/partials/records`,
`/dashboard/partials/simulations`, `/dashboard/static/dashboard.css`,
`/simulations/runs`, `/metrics`, and a PostgreSQL recomputation spot check.

Endpoint spot checks for the same marker:

```bash
curl -fsS 'http://127.0.0.1:8010/dashboard?data_scope=test&provider=dashboard-provider-1782222979&model=dashboard-model-1782222979&strategy=dashboard-strategy-1782222979&tenant_id=dashboard-tenant-1782222979&team_id=dashboard-team-1782222979&preset=all&live=true'

curl -fsS 'http://127.0.0.1:8010/dashboard/partials/live?data_scope=test&provider=dashboard-provider-1782222979&model=dashboard-model-1782222979&strategy=dashboard-strategy-1782222979&tenant_id=dashboard-tenant-1782222979&team_id=dashboard-team-1782222979&preset=all'

curl -fsS 'http://127.0.0.1:8010/dashboard?preset=all&provider=dashboard-provider-1782222979-no-match&live=false'
```

Expected evidence:

- The dashboard HTML contains the compact filter panel, active filter chips,
  primary usefulness status, Current Diagnostics, Local token delta, Recent Records,
  Simulation Replay, and a Pause/Resume action.
- Seeded dashboard smoke rows appear only when `data_scope=test` or
  `data_scope=all` is selected. The default `data_scope=real` view must not use
  smoke/demo rows to inflate value panels.
- The proof-status banner says primary usefulness is unproven unless a passed
  direct-vs-proxy Codex CLI proof exists. Cache evidence must be scoped to the
  whole Codex turn/provider-call sequence, not one selected provider call.
- Partial responses return `200` and contain only the partial region, not a
  full `<!doctype html>` document.
- Empty-state filters render "No persisted compression executions match these
  filters" rather than fake totals.
- Rendered HTML and templates do not expose prompt text, response text,
  original chunk content, or compressed chunk content.

Browser evidence from the completed pass is under:

```text
tmp/dashboard-evidence/dashboard-browser-smoke-1782222979/
```

Important artifacts:

- `browser-evidence.json`: CSS responses all `200`; HTMX partial responses
  `200`; filter inclusion preserved provider/model/strategy/tenant/team query
  values; pause removed live polling; resume restored polling; console and page
  error counts were `0` in the clean browser run; desktop width was 1440 with
  no page overflow; mobile width was 390 with no page overflow.
- `desktop-initial.png`, `desktop-paused.png`, `desktop-resumed.png`, and
  `mobile-initial.png`: screenshots for desktop, state changes, and mobile.
- `design-audit.md`: UI audit with fixed mobile records and favicon findings.
- `visual-reasoning/report.json` and `visual-reasoning/rework-brief.md`:
  initial image review.
- `visual-reasoning-cdp-headed-compact/report.json` and
  `visual-reasoning-cdp-headed-compact/rework-brief.md`: compactness pass after
  the filter-panel rework.

For headed cdp proof, reuse the existing analytics dashboard tab when possible:

```bash
cdp pages --browser-mode headed --json
cdp --browser-mode headed screenshot \
  --target <analytics-dashboard-tab-id> \
  --out tmp/dashboard-evidence/<marker>/cdp-headed-compact-desktop.png \
  --json
cdp --browser-mode headed console --errors \
  --target <analytics-dashboard-tab-id> \
  --wait 2s \
  --limit 0 \
  --json
```

The implementation pass reused target
`EBFC9AEDDE03E4B5D6A803F96B2C60C9`. The headed default profile reported one
runtime exception reading `global`; the clean browser evidence did not reproduce
it, so it is recorded as headed profile/runtime noise unless future evidence
ties it to dashboard code.

Phoenix notes: the dashboard work did not change OTel semantics. Phoenix
evidence is still useful for LiteLLM and analytics backend spans when OTel is
enabled, but the dashboard proof itself is HTTP, PostgreSQL, browser, network,
console, and screenshot evidence.

Auth and cost caveats: the dashboard seed is synthetic and does not call a paid
model. `make e2e` uses the real LiteLLM path and should run only when ChatGPT
OAuth/auth is available; never print token contents while checking that state.

## Stats And Metrics

Take snapshots after the smoke commands complete:

```bash
# Default operational scope; smoke rows should not inflate this view.
curl -fsS http://127.0.0.1:8010/stats

# Explicit test scope for seeded smoke rows.
curl -fsS 'http://127.0.0.1:8010/stats?data_scope=test&provider=openai&model=gpt-smoke'
curl -fsS 'http://127.0.0.1:8010/stats/breakdown?data_scope=test&group_by=provider'
curl -fsS 'http://127.0.0.1:8010/stats/dashboard?data_scope=test&provider=openai'
curl -fsS 'http://127.0.0.1:8010/records/compression?data_scope=test&limit=10'
curl -fsS 'http://127.0.0.1:8010/simulations/runs?limit=10'
curl -fsS http://127.0.0.1:8010/metrics | sed -n '1,40p'
```

Expected evidence:

- In explicit test scope, `requests`, `executions`, `chunks`, and
  `tokens_saved` move after ingest.
- The default operational `/stats` view does not count smoke/demo rows.
- In explicit test scope, `retrievals` moves after CCR retrieval.
- `/metrics` exposes Prometheus text counters matching `/stats`.
- `/records/compression` returns paginated execution-level summaries. Detail
  reads expose content presence booleans and raw-metadata presence booleans,
  not raw chunk content or provider raw metadata by default.
- `/stats/dashboard` returns recomputable dashboard read models: token totals,
  savings distributions, latency distributions, provider estimate diagnostics,
  measured-vs-estimated cost, cache activity, retrieval frequency,
  negative-savings/cost-increase signals, and primary usefulness status.
- `/simulations/runs` returns stored simulation records. Simulation results
  link back to source request/execution/chunk IDs and do not mutate production
  source rows.
- Metric labels stay low-cardinality. Do not add request IDs, chunk hashes,
  trace IDs, raw model responses, or tenant-specific free text as labels.

Interpretation rules:

- Query/read-model endpoints default to `data_scope=real`; smoke/demo rows are
  test data and need `data_scope=test` or `data_scope=all`.
- `requests` counts distinct `compression_requests` rows among matching
  executions; `executions` counts matching `compression_executions` rows.
- `chunks` counts `compression_chunks` rows for those executions.
- `retrievals` counts `chunk_retrieval_events` joined through those chunks.
- Compression token totals are summed from execution rows. Failed agent-wrapper
  attempts or Responses events without a compression measurement can have null
  per-record token measurements; aggregate APIs coalesce those nulls to zero.
- Provider usage totals come from `token_usage_breakdowns` rows with
  `measurement_source='provider_reported'`. They are the provider-call view of
  the request and should not be treated as compression savings.
- Provider cache hit comes from provider-reported `cached_input_tokens` divided
  by provider-reported input tokens. Billing-equivalent input uses
  `provider_uncached_input + provider_cached_input * ANALYTICS_CACHED_INPUT_COST_MULTIPLIER`
  and defaults the multiplier to `0.10`. Treat the result as a one-sided
  billing-input diagnostic, not a savings or capacity claim. The multiplier
  matches current OpenAI GPT-5.x text cached-input ratios as of 2026-06-23, but
  evidence should record the configured value for the run.
- `negative_savings_executions` means `tokens_saved < 0`; it is a risk signal,
  not a failure status.
- Dashboard cost fields compare measured `provider_calls.cost_total` with
  estimated `cost_calculations.total_cost`. Missing measured cost stays `null`.
- Estimated-vs-provider token deltas are diagnostics only. They do not prove
  savings or usefulness.
- Primary usefulness needs actual direct Codex vs `./bin/codex-litellm`
  `codex exec --json` evidence. Judge aggregate lane totals and provider cache
  hit over the whole Codex turn/provider-call sequence, not a single call.

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

Phoenix project routing is part of the evidence. Phoenix docs state that traces
go to a `default` project when no project is specified, and generic OTLP
exporters can set the project through resource attributes. This stack sets
`PHOENIX_PROJECT_NAME` for the analytics backend and maps it to
`openinference.project.name`, so LiteLLM and analytics spans should appear in
the `litellm-proxy-headroom` project rather than only in `default`.

Current evidence expectations:

- LiteLLM traces arrive through the existing OTel configuration.
- Open WebUI does not export OTel traces by default; its health checks and
  sqlite connection spans are treated as Phoenix noise.
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
docker compose logs --tail=200 analytics-backend | rg "litellm.proxy.analytics|compression|retrieval|provider|persistence|mcp" -C 2
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
