# LiteLLM Proxy Headroom

Minimal uv-managed LiteLLM proxy setup with ChatGPT as the provider and
Headroom compression enabled.

## Run

Install dependencies:

```bash
uv sync
```

Run the zero-custom-code LiteLLM proxy:

```bash
uv run litellm --config config/litellm.yaml --host 127.0.0.1 --port 4000
```

The LiteLLM config uses ChatGPT's `chatgpt/` provider route and a config-local
callback shim, `config/headroom_litellm_callback.py`, which re-exports
Headroom's installed `HeadroomCallback` for LiteLLM's proxy callback loader.
On a machine without existing ChatGPT device credentials, LiteLLM prompts for
the ChatGPT OAuth device flow during startup.

Run the optional ASGI wrapper:

```bash
uv run uvicorn litellm_proxy_headroom.app:app --host 127.0.0.1 --port 4000
```

## Check

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

ChatGPT subscription authentication is handled by LiteLLM's documented OAuth
device flow. Do not commit OAuth tokens, API keys, or LiteLLM master keys.
