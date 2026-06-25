# Integration Principles

This repository should stay a deployment harness around an owned LiteLLM proxy,
Open WebUI, Phoenix, the custom analytics backend, and a small imported
compression-library integration. Headroom is an implementation dependency, not
an operator-facing service in this repository.

## Architecture

Run upstream applications at their documented roots:

- The owned LiteLLM proxy is the public localhost OpenAI-compatible service on
  port 4000. It owns
  `config/litellm.yaml`, provider configuration, callbacks, and Phoenix tracing.
- Open WebUI points directly at LiteLLM with
  `OPENAI_API_BASE_URL=http://litellm:4000/v1`.
- The custom analytics backend is the localhost analytics surface on port 8010
  for `/dashboard`, `/health`, `/ready`, `/stats`, `/stats/breakdown`,
  `/stats/dashboard`, `/records/compression`, `/simulations/runs`, `/metrics`,
  storage-backed CCR compatibility endpoints, and `/mcp/`.
- Headroom is not a Compose service, CLI, proxy, dashboard, MCP server, or route
  owner. Keep it as an installed library for the LiteLLM callback and
  `CompressionStoreBackend` compatibility adapter.

Do not add or run Headroom CLI, `headroom proxy`, Headroom MCP, Headroom
dashboard, Headroom API service, route aliases to Headroom surfaces, or
Headroom Compose containers. If a future requirement cannot be met by
LiteLLM configuration, this repo's analytics backend, or local library
adapters, handle that as a separate architecture decision instead of expanding
Headroom's footprint here.

## Upgrade Rule

Do not patch Headroom templates, mutate LiteLLM route tables, or monkeypatch
LiteLLM/Headroom internals. Use LiteLLM YAML callbacks, documented library
callback and CCR backend extension points, this repo's FastAPI routes, FastMCP,
Compose configuration, and environment variables.

Do not reintroduce a Headroom proxy service. The validated runtime topology is
the owned LiteLLM proxy loading `config/litellm.yaml`, using the persisted
ChatGPT OAuth auth file, accepting the Open WebUI API key as a local proxy key,
preserving Phoenix tracing callbacks, sending analytics to the backend, and
serving retrieval through the custom MCP endpoint.

Usefulness comes before unit tests. Validate the deployed behavior first with
runtime evidence: LiteLLM `/health`, `/v1/models` and chat completions through
the Open WebUI-facing path, analytics `/stats` and `/metrics`, backend `/mcp/`,
PostgreSQL spot checks, logs, and Phoenix/OTel traces. Unit/config tests are
secondary guards after the real path is known to work.

For Codex compression usefulness specifically, the primary proof is actual
`codex exec --json` usage through direct Codex and `./bin/codex-litellm`.
`gpt-5.4-mini` is for smoke validation only; practical proof uses `gpt-5.5`.
Judge aggregate provider-reported input, cached input, output, reasoning, total
tokens, and cost when present across the whole Codex turn/provider-call
sequence. A dashboard row, estimated tokenizer delta, smoke/demo fixture, or
single provider call is not enough to claim usefulness.

For non-Codex CLIs whose documented surfaces do not expose a comparable direct
provider lane, use real CLI series with marker-correlated LiteLLM rows and, when
supported, a per-request compression-off baseline via local
`X-LiteLLM-Proxy-Compression: off`. That baseline must keep the same LiteLLM
provider route and only disable Headroom compression transforms; it is a proof
mode, not an operator-facing service or replacement for direct provider proof
where direct proof exists.

If a future integration issue appears, exhaust documented configuration,
Compose topology, and environment variables before adding code. Any unavoidable
shim must be isolated, tested, and documented with the upstream behavior it is
bridging.

## Current LiteLLM Callback Shim

`config/headroom_litellm_callback.py` is intentionally a narrow compatibility
shim for Headroom v0.27.0. LiteLLM proxy loads the config callback as a class,
while Headroom's upstream LiteLLM integration is an instance-based
`CustomLogger`; the shim keeps one lazy instance and delegates the LiteLLM hook
methods to it.

The shim's only behavior change is local compression profile selection. When
`HEADROOM_API_KEY` is not set, the callback uses Headroom's built-in
`agent-90` profile via `CompressConfig(savings_profile="agent-90")`. Do not add
new environment variables for this profile unless Headroom exposes a documented
callback configuration surface that needs them.

The default Compose stack intentionally does not run any Headroom service. The
owned LiteLLM proxy serves `/v1/*`; the analytics backend serves the custom
dashboard, filtered stats, records, metrics, CCR compatibility, and MCP.

## Current Analytics Topology

The analytics backend is the ingress for compression activity, CCR storage,
retrieval accounting, stats, metrics, and dashboard-ready APIs. LiteLLM and
library compatibility adapters talk to it over bounded HTTP; they do not write
directly to PostgreSQL.

The current runtime keeps LiteLLM as the public localhost OpenAI-compatible
endpoint. Compression runs inside the LiteLLM callback path as a library
integration; the analytics backend owns storage, retrieval accounting, stats,
metrics, dashboard-ready APIs, and MCP.

Dashboard-ready query APIs and simulation outputs are read models over source
tables. Dashboard stats must remain recomputable from request, execution,
provider-call, token-usage, cache, chunk, and retrieval rows rather than
becoming mutable aggregate totals. Simulation results must be stored separately
from production executions and only link back to source request/execution/chunk
IDs.

Do not implement a Headroom proxy extension for analytics. The supported
integration boundary is the callback plus CCR-compatible backend adapter, both
used as library integrations.

## Analytics Implementation Discipline

Keep analytics code modular and adapter-oriented:

- Domain and application modules must stay independent of LiteLLM, Headroom,
  FastAPI, SQLAlchemy, Redis, MCP, provider SDKs, and OpenTelemetry.
- Add new API, MCP, OTel, and provider-integration behavior in focused adapter
  modules. Do not grow `callback.py`, `repositories.py`, or `models.py` for
  unrelated behavior just because those files are already central.
- Prefer cohesive modules with a narrow reason to change. If a new analytics
  file grows past about 250 lines, record why in the active capsule status or
  split it before moving on.
- Integration changes need runtime proof first: a smoke path with a marker,
  backend HTTP evidence, PostgreSQL spot checks, stats/metrics deltas, and
  OTel/Phoenix evidence when telemetry is touched. Unit tests come after that
  behavior is useful end to end.
- Analytics value claims must stay provider-reported and proof-scoped:
  smoke/demo rows require explicit `data_scope=test`, estimated provider deltas
  are diagnostics, and dashboard value panels must say unproven or not useful
  when the aggregate Codex A/B proof is absent or failing.

## Codex Model Refresh

`config/litellm.yaml`'s `model_list` is generated by
`scripts/update_litellm_models.py`, which invokes `codex debug models` and maps
each API-supported `slug` to a LiteLLM Responses-mode model entry:
`model_name: <slug>` and `litellm_params.model: chatgpt/<slug>`.

Before changing the mapping, inspect the live JSON schema with `jq`, for
example:

```bash
codex debug models > /tmp/codex-models.json
jq -r 'type, (if type=="object" then keys else empty end)' /tmp/codex-models.json
jq -r '.models[0] | keys_unsorted[]' /tmp/codex-models.json
```
