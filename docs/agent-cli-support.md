# Agent CLI Support Levels

This repo supports coding-agent CLIs only through repo-owned managed homes and
documented provider/config surfaces. Native/default CLI config is read-only
input unless a later slice proves a reversible overlay is required.

Version/source-surface: `docsearch` tenants `openai-codex-docs`
(`https://developers.openai.com`), `opencode` (`https://opencode.ai`),
`github-copilot` (`https://docs.github.com`), live GitHub Copilot CLI BYOK docs
(`https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/use-byok-models`),
`litellm` (`https://docs.litellm.ai`),
and `anthropic-claude-docs`
(`https://claude.com`, `https://platform.claude.com`,
`https://www.anthropic.com`); local versions checked on 2026-06-25 are
`codex-cli 0.142.0`, LiteLLM lockfile `1.89.3`, Claude Code `2.1.179`, OpenCode `1.1.34`, GitHub
Copilot CLI `1.0.65` after `copilot update` from `1.0.64`, and Pi coding
agent `0.80.2` after `npm install -g @earendil-works/pi-coding-agent@0.80.2`
from `0.75.0`. Source clones were inspected under `tmp/cli-source` for Codex,
OpenCode, Claude Code, Copilot CLI, and Pi. Gap: LiteLLM documents Claude Code
non-Anthropic routing through `/v1/messages`, but the current deployment's
available ChatGPT-backed `gpt-5.x` aliases still reject Claude Code's
system-message request shape.

## Support Matrix

| CLI | Level | Managed home | Provider route | Proof requirement |
|---|---|---:|---|---|
| Codex CLI | Supported and proven | `~/.codex-headroom` | Generated Codex TOML provider using LiteLLM `/v1` Responses-compatible base URL and `OPENAI_API_KEY` from `LITELLM_MASTER_KEY`. | Actual `codex exec --json` series, smoke model `gpt-5.4-mini`, primary practical model `gpt-5.5`, aggregate provider usage/cache/DB proof, observed cost only when reported. |
| Claude Code | Isolated wrapper, provider route gated | `~/.claude-headroom` | `ANTHROPIC_BASE_URL` plus both `ANTHROPIC_AUTH_TOKEN` and `ANTHROPIC_API_KEY` from `LITELLM_MASTER_KEY`, `--setting-sources project`, generated MCP config, strict MCP isolation, and local `X-LiteLLM-Proxy-*` headers via `ANTHROPIC_CUSTOM_HEADERS`. Current ChatGPT-backed LiteLLM aliases reject Claude Code system-message shape. | Latest real `gpt-5.4-mini` smoke marker `claude-smoke-currentroute-20260625T0202` reached LiteLLM as client `claude`, then failed with `System messages are not allowed` before provider usage. First prove an Anthropic-compatible or otherwise Claude-compatible LiteLLM route, then run a real Claude Code call series and aggregate provider/cache rows. |
| OpenCode | Route supported, cache usefulness unproven | `~/.opencode-headroom` | Official OpenCode docs support custom OpenAI-compatible providers through `@ai-sdk/openai-compatible`, `options.baseURL`, `options.headers`, model entries, and `{env:...}` or `{file:...}` secret references. | Matching real `gpt-5.5` OpenCode series compared normal `agent-90` with `OPENCODE_LITELLM_COMPRESSION_MODE=off`. Normal mode used `1232` fewer total provider tokens, but cached input was absent, observed cost was unavailable, and local compression saved `0` tokens, so no cache/cost usefulness claim is made. |
| GitHub Copilot CLI | Route supported, cache usefulness unproven | `~/.copilot-headroom` | Copilot CLI 1.0.65 documents local BYOK through `COPILOT_PROVIDER_BASE_URL`, `COPILOT_PROVIDER_TYPE=openai`, `COPILOT_PROVIDER_BEARER_TOKEN`, `COPILOT_PROVIDER_WIRE_API=responses`, and model env vars, but still no request-header surface. | Real `gpt-5.4-mini` post-upgrade smoke routed through LiteLLM by narrow time-window DB correlation. Earlier three-call `gpt-5.5` practical series on 1.0.64 also routed through LiteLLM. Cached input is absent and cost is unavailable, so no cache/cost usefulness claim is made. |
| Pi coding agent | Route supported, `agent-90` not useful on latest practical proof | `~/.pi-headroom` | Pi 0.80.2 documents `PI_CODING_AGENT_DIR` and custom `models.json` providers with `baseUrl`, `api: openai-responses`, `$LITELLM_MASTER_KEY`, custom headers, and model entries. | Matching real `gpt-5.5` Pi series compared normal `agent-90` with `PI_LITELLM_COMPRESSION_MODE=off`. Normal compression used `27327` more total provider tokens, `35044.40` more billing-equivalent input tokens, and had a cache-ratio delta of `-0.159897`; observed cost remains unavailable. |

For the latest local runtime artifact pointers behind these labels, see
[Multi-CLI Proof Contract](multi-cli-proof-contract.md#latest-evidence-pointers).

## Wrapper Contract

- Generated files live in the managed home or test temp homes, never native
  `~/.codex`, `~/.claude`, `~/.config/opencode`, `~/.copilot`, or
  `~/.pi/agent` by default.
- Secrets stay in environment variables, auth stores, or explicit file refs.
  Generated config must not contain token contents.
- URLs are validated before artifacts are written: `http(s)` only, hostname
  required, no credentials, query strings, or fragments.
- Wrapper scripts should be Python and use structured serializers/parsers for
  TOML/JSON where practical.
- Codex, Claude Code, OpenCode, and Pi wrappers support proof-only
  `*_LITELLM_COMPRESSION_MODE=off`. This sends local
  `X-LiteLLM-Proxy-Compression: off`, keeps provider routing through LiteLLM,
  and records skipped compression rows for aggregate on/off comparison.
  Copilot is excluded until its CLI exposes a documented request-header
  surface.
- Support claims require actual CLI usage series. One-off smoke commands can
  prove routing, but they do not prove useful aggregate cache behavior.

## Implementation Order

1. Keep Codex regression proof green while adding other CLIs.
2. Prove OpenCode routing because the documented custom OpenAI-compatible
   provider surface matches LiteLLM directly.
3. Keep Claude Code gated until LiteLLM has an Anthropic-compatible or
   otherwise Claude-compatible route that accepts the CLI's request shape.
4. Use compression-off baselines where direct provider baselines are
   unavailable, then compare aggregate `gpt-5.5` provider usage across real CLI
   series.
5. Keep OpenCode and Copilot at route-supported/cache-unproven until
   direct-vs-proxy or a documented on/off practical proof shows useful
   aggregate cache behavior. Copilot remains time-window-correlated until a
   request-header or equivalent marker surface appears. Pi is route-supported
   but not useful on the latest on/off proof, so do not claim Pi compression
   value until a new practical comparison reverses that.
