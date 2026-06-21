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

## Docker Compose

The default operator path is:

```bash
make
```

That creates `.env` from `.env.example` if needed, creates local runtime
directories, builds the LiteLLM image, and starts:

- LiteLLM: <http://127.0.0.1:4000>
- Open WebUI: <http://127.0.0.1:8080>
- Phoenix: <http://127.0.0.1:6006>
- Phoenix PostgreSQL on the private Compose network

Useful targets:

```bash
make auth-import        # copy existing sibling ChatGPT OAuth file if present
make logs SERVICE=litellm
make ps
make down
make check
```

ChatGPT OAuth is persistent via `./data/chatgpt:/data/chatgpt` and
`CHATGPT_TOKEN_DIR=/data/chatgpt`. If the sibling
`../litellm-proxy/data/chatgpt/auth.json` exists, `make auth-import` copies it
without printing its contents.

The compose stack sends LiteLLM OTel v2 traces to Phoenix using the
`arize_phoenix` callback and keeps prompt/response capture disabled with
`OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=no_content`. Open WebUI is
configured to forward user/chat/message metadata headers to LiteLLM and export
OTEL traces to Phoenix over OTLP/gRPC.
