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
`https://www.anthropic.com`); local versions checked on 2026-06-26 are
`codex-cli 0.142.2`, LiteLLM lockfile `1.89.3`, Claude Code `2.1.179`, OpenCode `1.1.34`, GitHub
Copilot CLI `1.0.65` after `copilot update` from `1.0.64`, and Pi coding
agent `0.80.2` after `npm install -g @earendil-works/pi-coding-agent@0.80.2`
from `0.75.0`. Source clones were inspected under `tmp/cli-source` for Codex,
OpenCode, Claude Code, Copilot CLI, and Pi at commits Codex `8005292`,
OpenCode `3730125`, Claude Code `0bd9543`, Copilot CLI `214d530`, and Pi
`371adcf`. LiteLLM documents Claude Code non-Anthropic routing through
`/v1/messages`; this deployment exposes Claude-facing aliases such as `sonnet`
and rewrites Claude Code's Anthropic `system` field before routing them to the
ChatGPT-backed `gpt-5.5` deployment. `gh release list --repo github/copilot-cli` on
2026-06-26 showed stable `v1.0.65` plus prerelease `v1.0.66-0`; the current
Copilot proof remains scoped to installed stable `1.0.65`. The installed
stable runtime package reports build commit `372738a`; the extracted
`github-copilot-1.0.66-0-darwin-arm64.tgz` prerelease package reports build
commit `47f3f13`. Both expose the same documented BYOK surface needed here:
`COPILOT_PROVIDER_WIRE_API=responses` for GPT-5 series models,
`COPILOT_PROVIDER_TRANSPORT=http|websockets`, model ID/wire-model split, and
manual prompt/output token-limit overrides.

## Support Matrix

| CLI | Level | Managed home | Provider route | Proof requirement |
|---|---|---:|---|---|
| Codex CLI | `supported_useful`; mutable-output account shareability and provider/cache savings proven | `~/.codex-headroom` | Generated Codex TOML provider using LiteLLM `/v1` Responses-compatible base URL and `OPENAI_API_KEY` from `LITELLM_MASTER_KEY`. The wrapper pins a static Codex model catalog when available so Codex does not refresh LiteLLM's OpenAI-shaped `/models` response, and the callback preserves Codex cache-sensitive Responses fields, including native `model`, through LiteLLM's ChatGPT adapter by default. Mutable `function_call_output` compression remains disabled by default and must be enabled explicitly for the proven experiment mode. | Latest mutable-on proof `codex-savings-direct-first-20260626T142720Z` used 12 resumed `gpt-5.5` turns per lane, direct first then proxy, yolo-equivalent mode, 240s account settle per lane, and `1,247,878` combined input tokens. Account snapshots passed as `proxy_not_worse`: direct primary `36 -> 37`, proxy primary `37 -> 37`, weekly `21 -> 21` for both, reset credits `2`, daily tokens `11,351,861`. Provider/cache diagnostics passed: proxy cached input `+26,624`, newly processed input `-26,448`, billing-equivalent input `-23,785.6`, cache-ratio delta `+0.042409`, cost unavailable; raw proxy total `+176` is a warning because billing-equivalent input improved. Proxy DB rows recorded 24 provider calls, 23 successful mutable-output executions, and `189,504` local tokens saved. Report: `tmp/codex-savings-report/codex-savings-direct-first-20260626T142720Z/report.html`. Passthrough-off marker `codex-savings-passthrough-off-20260626T130210Z` is a failed diagnostic, not a supported savings mode. |
| Claude Code | Route configured, cache usefulness unproven | `~/.claude-headroom` | `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN` from `LITELLM_MASTER_KEY`, `--setting-sources project`, generated MCP config, strict MCP isolation, and local `X-LLM-Proxy-*` headers via `ANTHROPIC_CUSTOM_HEADERS`. Claude Code defaults to the public `sonnet` alias with `--effort xhigh`; LiteLLM maps `sonnet`, `opus`, `fable`, and selected `claude-*` aliases to `chatgpt/gpt-5.5`. | Route proof must use a real `gpt-5.5`/xhigh Claude Code smoke or series through `bin/claude-litellm`, then aggregate provider/cache rows before making usefulness claims. |
| OpenCode | Route supported, cache usefulness unproven | `~/.opencode-headroom` | Official OpenCode docs support custom OpenAI-compatible providers through `@ai-sdk/openai-compatible`, `options.baseURL`, `options.headers`, model entries, and `{env:...}` or `{file:...}` secret references. | Matching real `gpt-5.5` OpenCode series compared normal `agent-90` with `OPENCODE_LITELLM_COMPRESSION_MODE=off`. Normal mode used `1232` fewer total provider tokens, but cached input was absent, observed cost was unavailable, and local compression saved `0` tokens, so no cache/cost usefulness claim is made. |
| GitHub Copilot CLI | Route supported, cache usefulness unproven | `~/.copilot-headroom` | Copilot CLI 1.0.65 documents local BYOK through `COPILOT_PROVIDER_BASE_URL`, `COPILOT_PROVIDER_TYPE=openai`, `COPILOT_PROVIDER_BEARER_TOKEN`, `COPILOT_PROVIDER_WIRE_API=responses`, model env vars, optional provider token-limit envs, and HTTP/WebSocket transport, but still no request-header surface. The extracted 1.0.66-0 prerelease bundle keeps the same gpt-5.5 BYOK contract, so the wrapper remains conservative: HTTP by default, WebSocket only through `COPILOT_LITELLM_TRANSPORT=websockets`, and prompt/output token-limit overrides only when explicitly configured. | Current route proof uses the Copilot CLI `gpt-4.1` selector with `gpt-5.5`/xhigh as the LiteLLM provider route. Cached input and cost remain unavailable, so no cache/cost usefulness claim is made. |
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
  `X-LLM-Proxy-Compression: off`, keeps provider routing through LiteLLM,
  and records skipped compression rows for aggregate on/off comparison.
  Copilot is excluded until its CLI exposes a documented request-header
  surface.
- Support claims require actual CLI usage series. One-off smoke commands can
  prove routing, but they do not prove useful account quota behavior.
- Codex practical usefulness now requires a longer resumed `gpt-5.5` proof:
  8-12 user-message turns per lane, yolo-equivalent mode on both direct and
  wrapper lanes, first-party account snapshots, and at least `1,000,000`
  combined direct-plus-wrapper input tokens. MITM artifacts are required for
  request-shape claims about quota-penalizing wrapper behavior.

## Implementation Order

1. Keep Codex route and analytics regression proof green while adding other
   CLIs; preserve the current not-cache-useful status until a fresh account
   and provider proof after meaningful route or callback changes passes.
2. Prove OpenCode routing because the documented custom OpenAI-compatible
   provider surface matches LiteLLM directly.
3. Keep Claude Code on the `sonnet` alias route and require real
   `gpt-5.5`/xhigh smoke evidence before changing usefulness status.
4. Use compression-off baselines where direct provider baselines are
   unavailable, then compare aggregate `gpt-5.5` provider usage across real CLI
   series as diagnostics. For Codex, use account snapshots as the primary
   usefulness metric.
5. Keep OpenCode and Copilot at route-supported/cache-unproven until
   direct-vs-proxy or a documented on/off practical proof shows useful
   account or cache behavior. Copilot remains time-window-correlated until a
   request-header or equivalent marker surface appears. Pi is route-supported
   but not useful on the latest on/off proof, so do not claim Pi compression
   value until a new practical comparison reverses that.
