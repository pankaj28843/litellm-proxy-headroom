# Repository Instructions

## Minimal Integration Rule

This repo should stay a thin deployment wrapper around LiteLLM, Headroom, Open WebUI, and Phoenix.

- Prefer documented extension points: LiteLLM YAML config, FastAPI/ASGI routes and mounts, Docker Compose service configuration, and environment variables.
- Do not monkeypatch or mutate LiteLLM or Headroom internals, route tables, callbacks, clients, or package files just because it is possible.
- Keep custom code small, explicit, and locally owned. Prefer running `headroom proxy` at its documented root for `/dashboard`, `/health`, `/stats`, and `/stats-history`; do not add LiteLLM wrapper aliases for those paths.
- The Headroom dashboard is useful only when it observes the same request path used by Open WebUI: Headroom must proxy to the ChatGPT-authenticated LiteLLM service that loads `config/litellm.yaml`. Do not run a separate dashboard or proxy instance disconnected from that path.
- Any unavoidable compatibility shim must be isolated, named as a shim, covered by a regression test, and justified in docs before it is expanded.
- Usefulness comes before unit tests. For integration fixes, first prove the real workflow works with runtime evidence: the relevant localhost endpoint, Compose service, browser network capture, logs, or trace output. Add or update unit/config tests after that evidence, not as the primary proof.

## Command Notes

- Do not use the `rtk` command prefix in this repo unless the user explicitly reverses that instruction.
- Do not print or inspect ChatGPT/Codex auth token contents.
