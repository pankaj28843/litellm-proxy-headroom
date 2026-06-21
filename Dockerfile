FROM python:3.12-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.9.11 /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:${PATH}"

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY config ./config

RUN uv sync --frozen --no-dev

EXPOSE 4000

CMD ["uvicorn", "litellm_proxy_headroom.app:app", "--host", "0.0.0.0", "--port", "4000"]
