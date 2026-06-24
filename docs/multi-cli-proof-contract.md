# Multi-CLI Proof Contract

This contract keeps CLI support claims comparable across Codex, Claude Code,
OpenCode, GitHub Copilot CLI, and later wrappers. It is intentionally based on
real CLI commands and LiteLLM analytics rows, not one-off provider calls.

## Required Proof Fields

Every supported or route-tested CLI proof must record:

| Field | Meaning |
|---|---|
| `cli` | Stable CLI label such as `codex`, `claude`, `opencode`, or `copilot`. |
| `wrapper` | Repo wrapper path and managed home, such as `bin/opencode-litellm` and `~/.opencode-headroom`. |
| `support_status` | `supported_useful`, `route_supported_cache_unproven`, `route_gated`, `isolation_only`, or `unsupported`. |
| `marker` | `LITELLM_PROXY_RUN_MARKER` or an explicit time-window fallback when the CLI cannot carry a marker. |
| `model_scope` | Smoke model, practical model, and any helper/small-model calls observed. |
| `artifact_dir` | Path under `tmp/` or other run artifact root containing command output and stderr. |
| `db_correlation` | `marker`, or `time_window` if marker headers cannot be sent. |
| `client_attribution` | `litellm_proxy_client` from DB rows where available. |
| `request_count` | Count of matched LiteLLM request rows. |
| `provider_reported_call_count` | Count of rows with `measurement_source=provider_reported`. |
| `input_tokens` | Aggregate provider-reported input tokens. |
| `cached_input_tokens` | Aggregate provider-reported cached input tokens; `absent` when the provider omits it. |
| `cache_ratio` | `cached_input_tokens / input_tokens`, or `unavailable` when cached input is absent. |
| `output_tokens` | Aggregate provider-reported output tokens. |
| `reasoning_tokens` | Aggregate provider-reported reasoning tokens when available. |
| `total_tokens` | Aggregate provider-reported total tokens. |
| `cost_status` | `observed`, `unavailable`, or `not_applicable`; never estimate missing cost. |
| `cost_total` | Observed cost only when the CLI/provider reports it. |

## Current Support Matrix

| CLI | Status | Current proof |
|---|---|---|
| Codex CLI | `supported_useful` | Actual Codex CLI `gpt-5.5` practical series proved LiteLLM route usefulness after provider session-affinity/cache fixes. Cost remains unavailable when Codex JSON omits it. |
| Claude Code | `route_gated` | Real `gpt-5.4-mini` Claude Code smoke and `--bare` smoke reached LiteLLM and analytics MCP, but failed with `System messages are not allowed` on the current ChatGPT-backed model group. |
| OpenCode | `route_supported_cache_unproven` | Real `opencode run --format json` smoke and practical `gpt-5.5` series routed through LiteLLM with marker-correlated provider rows. Practical aggregate had input `51648`, cached input absent, output `674`, reasoning `314`, total `52322`, cost unavailable. |
| GitHub Copilot CLI | `isolation_only` | `bin/copilot-litellm` isolates `COPILOT_HOME`/`--config-dir` under `~/.copilot-headroom`; current docs/help do not expose a local OpenAI-compatible BYOK route. |

## Query Shape

Use this DB shape for marker-correlated proof rows:

```sql
select
  cr.request_metadata->>'litellm_proxy_run_marker' as marker,
  cr.request_metadata->>'litellm_proxy_client' as client,
  cr.request_metadata->>'litellm_proxy_project' as project,
  cr.incoming_route,
  pc.model,
  pc.status as provider_status,
  tub.measurement_source,
  sum(tub.input_tokens) as input_tokens,
  sum(tub.cached_input_tokens) as cached_input_tokens,
  sum(tub.output_tokens) as output_tokens,
  sum(tub.reasoning_tokens) as reasoning_tokens,
  sum(tub.total_tokens) as total_tokens,
  sum(pc.cost_total) as cost_total
from compression_requests cr
join compression_executions ce on ce.request_id = cr.id
left join provider_calls pc on pc.execution_id = ce.id
left join token_usage_breakdowns tub on tub.provider_call_id = pc.id
where cr.request_metadata->>'litellm_proxy_run_marker' = '<marker>'
group by marker, client, project, cr.incoming_route, pc.model, pc.status,
  tub.measurement_source;
```

If a CLI cannot carry marker headers, use a narrow start/end UTC window and
state `db_correlation=time_window`.
