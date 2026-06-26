# Multi-CLI Proof Contract

This contract keeps CLI support claims comparable across Codex, Claude Code,
OpenCode, GitHub Copilot CLI, Pi, and later wrappers. It is intentionally based
on real CLI commands, first-party account snapshots where available, and
LiteLLM analytics rows, not one-off provider calls.

## Required Proof Fields

Every supported or route-tested CLI proof must record:

| Field | Meaning |
|---|---|
| `cli` | Stable CLI label such as `codex`, `claude`, `opencode`, `copilot`, or `pi`. |
| `wrapper` | Repo wrapper path and managed home, such as `bin/opencode-litellm` and `~/.opencode-headroom`. |
| `support_status` | `supported_useful`, `route_supported_cache_unproven`, `route_supported_not_useful`, `route_gated`, `isolation_only`, or `unsupported`. |
| `marker` | `LITELLM_PROXY_RUN_MARKER` or an explicit time-window fallback when the CLI cannot carry a marker. |
| `compression_mode` | `on`, `off`, `mixed`, `unknown`, or `not_applicable`. Use `off` only for explicit compression-disabled baselines that still route through LiteLLM and record provider rows. |
| `model_scope` | Smoke model, practical model, and any helper/small-model calls observed. |
| `artifact_dir` | Path under `tmp/` or other run artifact root containing command output and stderr. |
| `db_correlation` | `marker`, or `time_window` if marker headers cannot be sent. |
| `client_attribution` | `litellm_proxy_client` from DB rows where available. |
| `request_count` | Count of matched LiteLLM request rows. |
| `provider_reported_call_count` | Count of rows with `measurement_source=provider_reported`. |
| `account_snapshot_status` | `observed`, `unavailable`, or `not_applicable`. For Codex, use `python3 scripts/codex_account_snapshot.py`, which drives `codex app-server --stdio` with `account/rateLimits/read` and `account/usage/read` when available. |
| `five_hour_limit_delta` | Direct/proxy before-after movement for the Codex primary rate-limit window when observed. |
| `weekly_limit_delta` | Direct/proxy before-after movement for the Codex secondary rate-limit window when observed. |
| `credit_delta` | Direct/proxy before-after movement for account credit balance or reset-credit count when observed. |
| `input_tokens` | Aggregate provider-reported input tokens. |
| `cached_input_tokens` | Aggregate provider-reported cached input tokens; `absent` when the provider omits it. |
| `cache_ratio` | `cached_input_tokens / input_tokens`, or `unavailable` when cached input is absent. |
| `output_tokens` | Aggregate provider-reported output tokens. |
| `reasoning_tokens` | Aggregate provider-reported reasoning tokens when available. |
| `total_tokens` | Aggregate provider-reported total tokens. |
| `cost_status` | `observed`, `unavailable`, or `not_applicable`; never estimate missing cost. |
| `cost_total` | Observed cost only when the CLI/provider reports it. |
| `trajectory_comparison` | For Codex A/B proofs, `summary.json.trajectory_comparison` from `scripts/e2e_agent90_usefulness.py`, including completed command counts, command-output size estimates, and whether provider/cache deltas were trajectory-normalized. |
| `minimum_input_token_floor` | For Codex practical quota proof, `summary.json.minimum_input_token_floor`. The active gate requires at least `1,000,000` combined direct-plus-wrapper input tokens over 8-12 resumed `gpt-5.5` user-message turns. |
| `request_shape_evidence` | Optional diagnostic artifact path when request/header/body parity matters. For Codex, use `scripts/mitm_codex_capture.py` full-fidelity local `flows.jsonl` evidence; artifacts may contain observed wire credentials and prompts, so keep them local and out of commits. |

## Current Support Matrix

| CLI | Status | Current proof |
|---|---|---|
| Codex CLI | `supported_useful`; mutable-output account shareability and provider/cache savings proven | Latest proof `codex-savings-direct-first-20260626T142720Z` used 12 resumed `gpt-5.5` turns per lane, direct first then proxy, yolo-equivalent mode, first-party account snapshots, 240s account settle per lane, and `1,247,878` combined input tokens. Account snapshots passed as `proxy_not_worse`: direct primary `36 -> 37`, proxy primary `37 -> 37`, weekly `21 -> 21` for both, reset credits `2`, and daily tokens `11,351,861`. Provider/cache diagnostics passed: proxy cached input `+26,624`, newly processed input `-26,448`, billing-equivalent input `-23,785.6`, and cache-ratio delta `+0.042409`; cost unavailable. Raw proxy total tokens were `+176` and are a warning because billing-equivalent input improved. Proxy DB rows recorded 24 provider calls, 23 successful mutable-output executions, and `189,504` local tokens saved. |
| Claude Code | `route_gated` | Latest real `gpt-5.4-mini` Claude Code smoke marker `claude-smoke-currentroute-20260625T0202` reached LiteLLM as client `claude`, but the current ChatGPT-backed model group still fails with `System messages are not allowed` before provider-reported usage/cache/cost. |
| OpenCode | `route_supported_cache_unproven` | Real `opencode run --format json` smoke, practical route proof, and same-route on/off `gpt-5.5` series routed through LiteLLM with marker-correlated provider rows. Normal mode used `1232` fewer total provider tokens than compression-off, but cached input was absent in both, observed cost was unavailable, and local compression saved `0` tokens. |
| GitHub Copilot CLI | `route_supported_cache_unproven` | After upgrading Copilot CLI to `1.0.65`, `bin/copilot-litellm` still uses the documented local BYOK provider env vars to route through LiteLLM. A post-upgrade smoke and current three-call `gpt-5.5` practical series reached `/v1/responses`; the CLI still lacks a documented request-header surface, so proof remains time-window correlated and cached input/cost remain unavailable. |
| Pi coding agent | `route_supported_not_useful` | After rebuilding LiteLLM with compression-mode support, matching real Pi `gpt-5.5` practical series compared normal `agent-90` against compression-off. Normal compression was worse by `27327` total provider tokens, `35044.40` billing-equivalent input tokens, and `-0.159897` cache-ratio delta; observed cost remains unavailable. |

## Compression-Off Baselines

For CLIs that cannot expose a direct provider baseline comparable to Codex,
marker-capable wrappers can run a proof-only LiteLLM baseline with compression
disabled per request:

- Codex: `CODEX_LITELLM_COMPRESSION_MODE=off`
- Claude Code: `CLAUDE_LITELLM_COMPRESSION_MODE=off` once its route is no
  longer gated
- OpenCode: `OPENCODE_LITELLM_COMPRESSION_MODE=off`
- Pi: `PI_LITELLM_COMPRESSION_MODE=off`

The wrappers normalize common disabled values to `off` and send
`X-LLM-Proxy-Compression: off`. The callback records
`litellm_proxy_compression_mode=off` and a skipped compression execution with
reason `compression_disabled_by_proxy_header`, while still preserving route
behavior and provider usage rows. This is a measurement baseline, not a default
operating mode.

GitHub Copilot CLI does not currently document a local custom-header surface
for BYOK provider requests. Copilot route proof therefore remains time-window
correlated unless a later CLI/source version exposes headers. The wrapper can
optionally map `COPILOT_LITELLM_MAX_PROMPT_TOKENS` and
`COPILOT_LITELLM_MAX_OUTPUT_TOKENS` to Copilot's documented BYOK model-limit
env vars for controlled experiments, but those knobs do not prove cache
usefulness by themselves.

## Latest Evidence Pointers

The runtime files below are ignored artifacts, not source-controlled fixtures.
They are listed so an operator can inspect the exact local proof behind the
current support labels.

| CLI | Marker/status | Evidence |
|---|---|---|
| Codex CLI | `supported_useful`; mutable-output account shareability and provider/cache savings proven | `tmp/agent90-usefulness/codex-savings-direct-first-20260626T142720Z/summary.json` and `tmp/codex-savings-report/codex-savings-direct-first-20260626T142720Z/report.html` show the latest proof. Minimum input floor passed with `1,247,878` combined input tokens. Account snapshots passed as `proxy_not_worse`: direct primary `36 -> 37`, proxy primary `37 -> 37`, weekly `21 -> 21` for both, reset credits `2`, and daily account tokens `11,351,861`. Provider/cache diagnostics passed on cached input `+26,624`, newly processed input `-26,448`, billing-equivalent input `-23,785.6`, and cache-ratio delta `+0.042409`; raw proxy total `+176` is a warning because billing-equivalent input improved. Cost unavailable. Proxy DB rows recorded 24 provider calls, 23 successful mutable-output executions, and `189,504` local tokens saved. MITM marker `codex-mitm-mutable-output-gpt55-20260626T134521Z` records direct HTTP diagnostic, proxy inbound, and LiteLLM outbound request-shape traces for the same request-shape family. Default-wrapper proof `tmp/codex-savings-report/codex-savings-proxyfirst-20260626T121000Z/report.html` remains account-capacity positive but provider-negative. Passthrough-off report `tmp/codex-savings-report/codex-savings-passthrough-off-20260626T130210Z/report.html` proves passthrough-off is not useful because provider/cache diagnostics failed and outbound MITM showed `client_metadata`/`prompt_cache_key` removal. |
| Claude Code | `route_gated` | `tmp/claude-route-proof/claude-smoke-currentroute-20260625T0202/proof.json` normalizes the latest real Claude Code smoke. Marker-correlated DB rows record two failed `/v1/chat/completions` requests for client `claude`, model `gpt-5.4-mini`, no provider-reported usage/cache/cost, and the CLI output reports `System messages are not allowed`. Earlier marker-attribution artifact: `tmp/claude-route-proof/claude-smoke-wrapperheaders-20260625T0320/stdout.jsonl`. |
| OpenCode | `route_supported_cache_unproven` | `tmp/multi-cli-proof/opencode-compression-comparison-20260625T014154Z.json` compares matching real OpenCode practical series: normal marker `opencode-compression-on-20260625T014154Z` versus compression-off marker `opencode-compression-off-20260625T014015Z`. Normal aggregate: input `49964`, total `50297`, cached input absent. Compression-off aggregate: input `51084`, total `51529`, cached input absent. Normal mode used `1232` fewer total provider tokens, but cost remained unavailable and normal local compression saved `0` tokens. Earlier route proof: `tmp/multi-cli-proof/opencode-practical-20260624T1950/proof.json`. |
| GitHub Copilot CLI | `route_supported_cache_unproven` | `tmp/copilot-route-proof/copilot-smoke-20260625T0150-upgrade/proof.json` normalizes the real post-upgrade `gpt-5.4-mini` smoke on Copilot CLI `1.0.65`; one `/v1/responses` provider row by time window, input `15735`, cached input `absent`, output `40`, reasoning `21`, total `15775`, cost `unavailable`. Current practical artifact: `tmp/copilot-route-proof/copilot-practical-20260625T020226Z/proof.json` from three real `gpt-5.5` Copilot CLI 1.0.65 BYOK runs; aggregate input `47216`, cached input `absent`, output `266`, reasoning `48`, total `47482`, cost `unavailable`. Earlier 1.0.64 practical artifact: `tmp/copilot-route-proof/copilot-practical-20260624T182754Z/proof.json`. |
| Pi coding agent | `route_supported_cache_unproven` | `tmp/pi-route-proof/pi-practical-20260625T002659Z/proof.json` normalizes three real `gpt-5.5` Pi CLI runs after upgrade to `0.80.2`; aggregate input `93352`, cached input `9216`, cache ratio `0.098723`, output `296`, reasoning `67`, total `93648`, cost `unavailable`. Smoke artifact: `tmp/pi-route-proof/pi-smoke-20260625T002534Z/proof.json`. |
| Pi coding agent | `route_supported_not_useful` | `tmp/pi-route-proof/pi-compression-comparison-20260625T012648Z.json` compares valid same-deploy Pi practical series: normal marker `pi-compression-on-20260625T012648Z` versus compression-off marker `pi-compression-off-20260625T012150Z`. Normal `agent-90` aggregate: input `99831`, cached `9216`, total `101161`, cache ratio `0.092316`. Compression-off aggregate: input `73081`, cached `18432`, total `73834`, cache ratio `0.252213`. Cost unavailable. Invalid pre-rebuild artifact `tmp/pi-route-proof/pi-compression-off-20260625T011758Z/invalid-old-callback.txt` is excluded. |

## Query Shape

Use this DB shape for marker-correlated proof rows:

```sql
select
  cr.request_key,
  pc.id as provider_call_id,
  pc.provider_call_key,
  pc.litellm_call_id,
  pc.provider_response_id,
  cr.created_at,
  cr.request_metadata->>'litellm_proxy_run_marker' as marker,
  cr.request_metadata->>'litellm_proxy_client' as client,
  cr.request_metadata->>'litellm_proxy_project' as project,
  cr.request_metadata->>'litellm_proxy_compression_mode' as compression_mode,
  cr.incoming_route,
  pc.model,
  pc.status as provider_status,
  tub.measurement_source,
  tub.input_tokens,
  tub.cached_input_tokens,
  tub.output_tokens,
  tub.reasoning_tokens,
  tub.total_tokens,
  pc.cost_total
from compression_requests cr
left join compression_executions ce on ce.request_id = cr.id
left join provider_calls pc on pc.request_id = cr.id
left join token_usage_breakdowns tub on tub.provider_call_id = pc.id
where cr.request_metadata->>'litellm_proxy_run_marker' = '<marker>'
order by cr.created_at, pc.created_at;
```

If a CLI cannot carry marker headers, use a narrow start/end UTC window and
state `db_correlation=time_window`.

## Request-Shape Forensics

When a route claim depends on what the CLI actually sent, use full-fidelity
local network forensics rather than dashboard totals or code inference alone.
MITM artifacts are required for claims about observed transport, header
propagation, or body field parity unless the claim is explicitly marked
source-inferred or unobserved. For Codex:

```bash
python3 scripts/mitm_codex_capture.py --lane direct --execute
python3 scripts/mitm_codex_capture.py --lane proxy --no-bypass-localhost --execute
```

Direct Codex defaults to Responses WebSocket when the selected provider supports
it, and that model path is not fully visible through plain `HTTPS_PROXY`
mitmproxy capture. Use `--disable-websockets-for-capture` only as a diagnostic
HTTP override and label the artifact accordingly. These artifacts prove request
shape on the observed transport; they do not replace account snapshot proof.

For the active Codex usefulness goal, request-shape MITM is mandatory whenever a
claim says the LiteLLM/Headroom wrapper did or did not add quota-penalizing
behavior. Compare observed wrapper traces against direct HTTP diagnostic traces
for model/reasoning parity, custom headers, request-body byte size, input item
count, tool shape, `prompt_cache_key`, `previous_response_id`, `truncation`,
`client_metadata`, and localhost/proxy routing. The pass/fail usefulness
classification still comes from the account-bracketed resumed-session proof:
8-12 `gpt-5.5` user-message turns per lane and
`minimum_input_token_floor.ok=true` at `1,000,000` combined input tokens.

## Collector Workflow

Use `scripts/collect_multi_cli_proof.py` after real CLI calls have already
produced artifacts and LiteLLM DB rows. The collector does not run agent CLIs;
it only normalizes exported per-call DB rows into the proof fields above.

Example using an exported DB JSON file:

```bash
python3 scripts/collect_multi_cli_proof.py \
  --cli opencode \
  --wrapper bin/opencode-litellm \
  --managed-home '~/.opencode-headroom' \
  --support-status route_supported_cache_unproven \
  --marker opencode-practical-20260624T1950 \
  --compression-mode on \
  --model-scope practical:gpt-5.5 \
  --model-scope helper:gpt-5.4-mini \
  --artifact-dir tmp/opencode-route-proof/opencode-practical-20260624T1950 \
  --db-correlation marker \
  --db-rows-json tmp/multi-cli-proof/opencode-practical-20260624T1950/db-rows.json \
  --note 'practical series routed through LiteLLM; cached input absent; cost unavailable' \
  --out tmp/multi-cli-proof/opencode-practical-20260624T1950/proof.json
```

The first real collector artifact was generated for
`opencode-practical-20260624T1950` and reported input `51648`, cached input
`absent`, output `674`, reasoning `314`, total `52322`, and cost
`unavailable`.

Use `--cost-status-override unavailable` when a CLI does not report observed
cost but LiteLLM rows contain a provider-call cost placeholder. Missing cost
must stay explicit and unestimated.
