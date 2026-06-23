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
  environment variables, package paths, and compatibility route names are
  library-adapter details, not product surfaces.
- Do not monkeypatch or mutate LiteLLM or Headroom internals, route tables,
  callbacks, clients, or package files just because it is possible.
- Keep custom code small, explicit, and locally owned.
- Any unavoidable compatibility shim must be isolated, named as a shim, covered by a regression test, and justified in docs before it is expanded.
- Usefulness comes before unit tests. For integration fixes, first prove the real workflow works with runtime evidence: the relevant localhost endpoint, Compose service, browser network capture, logs, or trace output. Add or update unit/config tests after that evidence, not as the primary proof.

## Command Notes

- Do not use the `rtk` command prefix in this repo unless the user explicitly reverses that instruction.
- Do not run Headroom CLI commands such as `headroom proxy`, `headroom wrap`,
  `headroom init`, or `headroom mcp install`.
- Do not print or inspect ChatGPT/Codex auth token contents.
