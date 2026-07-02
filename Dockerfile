# syntax=docker/dockerfile:1.7
FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim

ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:${PATH}"

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY README.md ./README.md
COPY src ./src
COPY config ./config
COPY alembic.ini ./alembic.ini
COPY alembic ./alembic

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

CMD ["litellm", "--config", "/app/config/litellm.yaml", "--host", "0.0.0.0", "--port", "4000"]
