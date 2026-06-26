# Harness Engineering

## Full-Fidelity Network Forensics

Use mitmproxy when the question is "what did this CLI actually send?" rather
than "what did provider usage report?" In this repo that makes MITM a harness
sensor for transport, header, and request-body claims. It is not a primary
usefulness metric.

Use it by default before claiming:

- a CLI used HTTP versus WebSocket for the model path;
- proxy env vars, custom CA, or localhost bypass were honored;
- Codex/LiteLLM preserved `prompt_cache_key`, `previous_response_id`,
  `client_metadata`, `truncation`, `x-codex-turn-state`, model identity, or
  tool shape;
- direct Codex and `./bin/codex-litellm` sent comparable request bodies;
- a source-level hypothesis is the behavior that actually happened at runtime.

The repo-owned path is:

```bash
python3 scripts/mitm_codex_capture.py --lane direct --execute
python3 scripts/mitm_codex_capture.py --lane direct --disable-websockets-for-capture --execute
python3 scripts/mitm_codex_capture.py --lane proxy --no-bypass-localhost --execute
```

The runner starts `uvx --from mitmproxy mitmdump` on localhost, uses a dedicated
mitmproxy config directory under `tmp/codex-mitm/<marker>/`, points Codex at the
generated `mitmproxy-ca-cert.pem` with `CODEX_CA_CERTIFICATE`, and writes
`flows.jsonl` request/response records with observed headers and bodies. Bodies
are stored as base64 and, when UTF-8/JSON decoding succeeds, as decoded text or
JSON too. This is deliberately a dev-local full-fidelity artifact, not
production telemetry.

Do not transform or omit captured fields in this harness. The point is to
inspect exactly what the CLI sent on a developer machine. The runner still must
not read auth token files; if the client sends auth headers, cookies, bearer
tokens, session values, prompts, or response bodies on the wire, `flows.jsonl`
preserves them. Keep these artifacts local and out of commits.

Mode discipline:

- Start with mitmproxy's regular proxy mode through `HTTP_PROXY` and
  `HTTPS_PROXY`; this is the most robust baseline when the client honors proxy
  configuration.
- Use local capture only when a same-device client bypasses explicit proxy
  configuration and the extra host-level capture boundary is intentional.
- Treat transparent, TUN, WireGuard, SOCKS, and system-network changes as
  escalation-only diagnostics. Label them in the artifact and do not make them
  required for normal repo validation.
- HTTPS inspection requires trusting the per-run mitmproxy CA for the captured
  process. The runner passes that CA through `CODEX_CA_CERTIFICATE`; do not add
  global trust unless the task explicitly requires it.

Codex transport caveat: direct Codex currently prefers Responses WebSocket for
OpenAI/ChatGPT providers that support it. Plain HTTPS proxy MITM captures
surrounding ChatGPT HTTP calls, but the default model WebSocket path is not a
complete request-shape capture. Use
`--disable-websockets-for-capture` only as a labeled diagnostic override; it
creates a temporary custom provider that uses HTTP Responses so request-shape
parity can be compared with `./bin/codex-litellm`.

MITM output is diagnostic evidence. It can support header/body/transport claims,
but Codex usefulness still requires account-bracketed `codex exec --json`
direct-vs-proxy proof from `scripts/e2e_agent90_usefulness.py`.

For real Codex quota usefulness, the proof must be a longer resumed session,
not a one-turn smoke. Use `gpt-5.5`, 8-12 user-message turns per lane, the same
yolo-equivalent execution mode for direct and wrapper lanes, and a minimum
combined direct-plus-wrapper input-token floor of `1,000,000`:

```bash
uv run python scripts/e2e_agent90_usefulness.py \
  --marker codex-gpt55-resumed-$(date -u +%Y%m%dT%H%M%SZ) \
  --model gpt-5.5 \
  --session-turns 12 \
  --task-lines 1800 \
  --min-combined-input-tokens 1000000 \
  --account-snapshot-settle-seconds 240 \
  --account-snapshot-attempts 4 \
  --query-db \
  --yolo \
  --execute
```

The harness starts each lane with `codex exec --json` and continues with
`codex exec resume --json <thread_id>` using the exact `thread.started` id from
that lane. Codex reports resumed-session usage cumulatively, so lane summaries
use the latest `turn.completed` usage event rather than summing every resumed
turn. The `minimum_input_token_floor` field in `summary.json` must pass before
the run can be treated as a practical quota proof. MITM should then be used to
explain whether `./bin/codex-litellm` changed request shape in a way that could
plausibly penalize quota burn: model identity, reasoning/verbosity, headers,
cache keys, continuation fields, body size, tool shape, and local LiteLLM
routing.

Review checklist for MITM artifacts:

- `plan.json` records lane, command, model, prompt hash, proxy env shape, CA
  path, and whether the HTTP diagnostic override was used.
- `result.json` records return code and flow count.
- `flows.jsonl` contains observed methods, hosts, paths, header names and
  values, and full request/response bodies as base64 plus decoded text/JSON
  when possible.
- No artifact should come from reading Codex/ChatGPT token files. Observed
  wire credentials and prompts are expected in `flows.jsonl`; keep artifacts
  local and do not commit them.
- Findings name the observed transport. If direct Codex WebSocket was not
  captured, the note must say so and avoid treating the HTTP diagnostic override
  as default direct behavior.

External reference points used for this contract:

- mitmproxy's introduction documents HTTP/HTTPS/WebSocket interception,
  `mitmdump`, flow saving, replay, reverse proxy mode, and generated TLS
  certificates: https://docs.mitmproxy.org/stable/
- mitmproxy's proxy-mode docs identify regular proxy mode as the robust
  starting point, local capture for same-device applications, and transparent or
  other modes as advanced options: https://docs.mitmproxy.org/stable/concepts/modes/
- mitmproxy's "how it works" docs explain why explicit HTTPS interception
  requires the proxy to become a trusted CA for the client process:
  https://docs.mitmproxy.org/stable/concepts/how-mitmproxy-works/
