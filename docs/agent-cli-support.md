# Agent CLI Support Levels

This repo supports coding-agent CLIs only through repo-owned managed homes and
documented provider/config surfaces. Native/default CLI config is read-only
input unless a later slice proves a reversible overlay is required.

Version/source-surface: `docsearch` tenants `openai-codex-docs`
(`https://developers.openai.com`), `opencode` (`https://opencode.ai`),
`github-copilot` (`https://docs.github.com`), and `anthropic-claude-docs`
(`https://claude.com`, `https://platform.claude.com`,
`https://www.anthropic.com`); local versions checked on 2026-06-24 are
`codex-cli 0.142.0`, Claude Code `2.1.179`, OpenCode `1.1.34`, and GitHub
Copilot CLI `1.0.7`. Gap: Claude Code CLI base-url behavior is grounded mostly
in local `claude --help`, because the indexed Anthropic tenant is stronger on
SDK/MCP docs than CLI provider routing.

## Support Matrix

| CLI | Level | Managed home | Provider route | Proof requirement |
|---|---|---:|---|---|
| Codex CLI | Supported and proven | `~/.codex-headroom` | Generated Codex TOML provider using LiteLLM `/v1` Responses-compatible base URL and `OPENAI_API_KEY` from `LITELLM_MASTER_KEY`. | Actual `codex exec --json` series, smoke model `gpt-5.4-mini`, primary practical model `gpt-5.5`, aggregate provider usage/cache/DB proof, observed cost only when reported. |
| Claude Code | Isolated wrapper, provider route gated | `~/.claude-headroom` | `ANTHROPIC_BASE_URL` plus both `ANTHROPIC_AUTH_TOKEN` and `ANTHROPIC_API_KEY` from `LITELLM_MASTER_KEY`, `--setting-sources project`, generated MCP config, and strict MCP isolation. Current ChatGPT-backed LiteLLM aliases reject Claude Code system-message shape. | First prove an Anthropic-compatible LiteLLM model route satisfies Claude Code, then run a real Claude Code call series and aggregate provider/cache rows. |
| OpenCode | Smoke route proven, practical proof pending | `~/.opencode-headroom` | Official OpenCode docs support custom OpenAI-compatible providers through `@ai-sdk/openai-compatible`, `options.baseURL`, model entries, and `{env:...}` or `{file:...}` secret references. | Run real practical `opencode run --format json` series with `gpt-5.5`, then aggregate provider/cache rows. |
| GitHub Copilot CLI | Isolation feasible, BYOK route unsupported | `~/.copilot-headroom` planned | Local help and GitHub docs expose `COPILOT_HOME`/`--config-dir`, MCP/custom-agent config, and hosted `--model` choices. They do not expose a local OpenAI-compatible base URL/API-key provider override. | Record unsupported boundary unless GitHub documents a hosted or enterprise BYOK model surface that exposes the target model through Copilot CLI. |

## Wrapper Contract

- Generated files live in the managed home or test temp homes, never native
  `~/.codex`, `~/.claude`, `~/.config/opencode`, or `~/.copilot` by default.
- Secrets stay in environment variables, auth stores, or explicit file refs.
  Generated config must not contain token contents.
- URLs are validated before artifacts are written: `http(s)` only, hostname
  required, no credentials, query strings, or fragments.
- Wrapper scripts should be Python and use structured serializers/parsers for
  TOML/JSON where practical.
- Support claims require actual CLI usage series. One-off smoke commands can
  prove routing, but they do not prove useful aggregate cache behavior.

## Implementation Order

1. Keep Codex regression proof green while adding other CLIs.
2. Prove OpenCode routing because the documented custom OpenAI-compatible
   provider surface matches LiteLLM directly.
3. Keep Claude Code gated until LiteLLM has an Anthropic-compatible route that
   accepts the CLI's request shape.
4. Keep Copilot CLI at isolation/feasibility until a documented BYOK route
   exists for the target model.
