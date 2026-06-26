# Repository Instructions

## Minimal Integration Rule

This repo should stay a thin deployment wrapper around LiteLLM, Open WebUI,
Phoenix, and the repo-owned analytics backend. Headroom is a library dependency
only, not an operator-facing service in this repository.

- Prefer documented extension points: LiteLLM YAML config, LiteLLM callbacks,
  FastAPI/ASGI routes and mounts owned by this repo, Docker Compose service
  configuration, and environment variables.
- Use Headroom only through imported library surfaces owned by local adapters:
  the LiteLLM compression callback path and the CCR-compatible
  `CompressionStoreBackend` adapter.
- Do not add, run, wrap, proxy through, mount, route to, or document as an
  operator path any Headroom CLI, `headroom proxy`, Headroom MCP server,
  Headroom dashboard, Headroom API service, or Headroom Compose container.
- Do not expose Headroom in operator-facing names for dashboards, MCP servers
  or tools, Prometheus metrics, wrapper commands, README workflows, or status
  text. Use LiteLLM/analytics/compression names instead. Existing `HEADROOM_*`
  environment variables, package paths, and CCR adapter route names are
  library-adapter details, not product surfaces.
- Do not monkeypatch or mutate LiteLLM or Headroom internals, route tables,
  callbacks, clients, or package files just because it is possible.
- Keep custom code small, explicit, and locally owned.
- This repository has no deployment compatibility contract. It is developed and
  tested on one local machine, so do not keep backward-compatibility shims,
  legacy headers, legacy routes, legacy env vars, aliases, or fallback behavior.
  When a repo-owned name changes, update all code, docs, tests, and scripts to
  the new name in the same change and delete the old path.
- Usefulness comes before unit tests. For integration fixes, first prove the real workflow works with runtime evidence: the relevant localhost endpoint, Compose service, browser network capture, logs, or trace output. Add or update unit/config tests after that evidence, not as the primary proof.

## Primary Usefulness Rule

- Do not treat dashboard totals, estimated tokenizer deltas, smoke/demo rows, or
  one selected provider call as proof that compression is useful.
- For Codex/LiteLLM usefulness work, smoke-test only with `gpt-5.4-mini`.
  Practical proof uses `gpt-5.5`.
- Primary proof must use actual `codex exec --json` turns on both direct Codex
  and `./bin/codex-litellm`, bracketed by first-party Codex account snapshots
  when available. Use `codex app-server --stdio` with JSON-RPC
  `account/rateLimits/read` and `account/usage/read` to measure real five-hour
  quota, weekly quota, credits, reset credits, and account token activity
  without reading auth token files. Prefer the repo helper
  `python3 scripts/codex_account_snapshot.py` for repeatable snapshots.
- Aggregate provider-reported usage across the whole Codex turn/provider-call
  sequence (input, cached input, output, reasoning, total tokens, and observed
  cost when present) is secondary diagnostic evidence. It explains quota
  movement; it does not replace quota/credit depletion proof when account
  snapshots are available.
- When account snapshots or observed lane cost are unavailable, mark those
  fields unavailable and unestimated in artifacts and docs.
- If direct-vs-proxy account-depletion proof is absent or failing, docs and
  analytics surfaces must say usefulness is unproven or not useful.

## Network Forensics Rule

- When a Codex/LiteLLM behavior claim depends on what a CLI actually sent on
  the wire, do not rely on config inference or source reading alone. Use
  full-fidelity local mitmproxy evidence, or mark the transport/body/header
  claim unobserved.
- Use full-fidelity local mitmproxy captures when a CLI/proxy claim depends on
  actual request shape, header propagation, transport choice, or body field
  parity.
- The repo-owned capture path is `python3 scripts/mitm_codex_capture.py`; it
  runs `uvx --from mitmproxy mitmdump` with a dedicated config directory and
  `CODEX_CA_CERTIFICATE`, and writes local JSONL with observed headers and
  bodies. It must not read auth token files. Flow artifacts may contain
  credentials that the client sent on the wire, so keep them local and do not
  commit them.
- Start with regular proxy mode via `HTTP_PROXY`/`HTTPS_PROXY`. Use local
  capture, transparent capture, or system-network changes only as explicitly
  labeled diagnostics when regular proxy mode cannot observe the client.
- Treat MITM evidence as diagnostic runtime evidence. It can prove what a
  client sent on the observed transport; it does not prove quota usefulness or
  replace Codex account snapshots.
- Current Codex direct traffic prefers Responses WebSocket when the selected
  provider supports it. Plain `HTTPS_PROXY` mitmproxy captures surrounding
  ChatGPT HTTP traffic, but not that default model WebSocket path. Use an
  explicitly labeled HTTP-for-capture diagnostic provider only when the goal is
  to compare HTTP request shape with `./bin/codex-litellm`.

## Command Notes

- Do not use the `rtk` command prefix in this repo unless the user explicitly reverses that instruction.
- Do not run Headroom CLI commands such as `headroom proxy`, `headroom wrap`,
  `headroom init`, or `headroom mcp install`.
- Do not print or inspect ChatGPT/Codex auth token contents.
