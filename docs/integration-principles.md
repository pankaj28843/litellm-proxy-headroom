# Integration Principles

This repository should stay a deployment harness, not a fork of LiteLLM or
Headroom.

## Architecture

Run upstream applications at their documented roots:

- `headroom proxy` is the public localhost service on port 4000.
- Headroom exposes `/dashboard`, `/health`, `/stats`, `/stats-history`, and
  `/v1/*` at root.
- The raw LiteLLM proxy runs as an internal upstream and owns
  `config/litellm.yaml`, provider configuration, callbacks, and Phoenix tracing.
- Headroom points at LiteLLM with `OPENAI_TARGET_API_URL=http://litellm:4000`.
- Open WebUI points at Headroom with
  `OPENAI_API_BASE_URL=http://headroom:4000/v1`.
- Headroom MCP uses the documented `headroom mcp serve --proxy-url
  http://headroom:4000` command and the same Headroom workspace data volume.

The Headroom dashboard must read data from the same Headroom proxy that handles
real Open WebUI traffic and forwards to the ChatGPT-authenticated LiteLLM
upstream. A dashboard-only Headroom instance, or a Headroom proxy pointed at a
different upstream/auth path, is duplicate work and should not be treated as a
valid solution.

## Upgrade Rule

Do not patch Headroom templates, mutate LiteLLM route tables, or add wrapper
aliases for absolute dashboard calls such as `/health`, `/stats`, and
`/stats-history`. Those paths are native to Headroom when the proxy runs at
root.

Do not remove the internal LiteLLM service unless a replacement is validated for
the same behavior: loading `config/litellm.yaml`, using the persisted ChatGPT
OAuth auth file, accepting the Open WebUI API key as a local proxy key, and
preserving Phoenix tracing callbacks.

Usefulness comes before unit tests. Validate the deployed behavior first with
runtime evidence: dashboard load, `/health`, `/stats`, `/stats-history`,
`/v1/models` through the Open WebUI-facing path, browser network captures, logs,
or Phoenix traces. Unit/config tests are secondary guards after the real path is
known to work.

If a future integration issue appears, exhaust documented configuration,
Compose topology, and environment variables before adding code. Any unavoidable
shim must be isolated, tested, and documented with the upstream behavior it is
bridging.
