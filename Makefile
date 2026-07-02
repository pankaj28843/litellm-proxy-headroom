SHELL := /bin/sh

COMPOSE ?= docker compose
APP_BUILD_SERVICE ?= litellm
SIBLING_AUTH_DIR ?= ../litellm-proxy/data/chatgpt
CHATGPT_AUTH_FILE ?= auth.json

.DEFAULT_GOAL := up

.PHONY: up init env auth-import migrate build down restart logs ps mcp models test e2e analytics-smoke codex-session-report codex-session-html codex-session-story lint format-check check config clean

up: init
	$(COMPOSE) build $(APP_BUILD_SERVICE)
	$(COMPOSE) run --rm analytics-migrations
	$(COMPOSE) up -d --no-build --wait --wait-timeout 240
	@. ./.env; printf '\nPhoenix:   http://127.0.0.1:%s\n' "$${PHOENIX_HOST_PORT:-26006}"
	@. ./.env; printf 'LiteLLM:   http://%s:%s\n' "$${LITELLM_VM_BIND_HOST:-10.20.30.1}" "$${LITELLM_PROXY_PORT:-24040}"
	@. ./.env; printf 'Analytics: http://127.0.0.1:%s\n' "$${ANALYTICS_BACKEND_PORT:-28010}"
	@. ./.env; printf 'MCP:       http://127.0.0.1:%s/mcp/\n' "$${ANALYTICS_BACKEND_PORT:-28010}"
	@printf '\nIf ChatGPT auth is missing, run: make logs SERVICE=litellm\n'

init: env
	mkdir -p data/chatgpt logs tmp

env:
	@if [ ! -f .env ]; then cp .env.example .env; printf 'created .env from .env.example\n'; fi

auth-import: init
	@if [ ! -f "$(SIBLING_AUTH_DIR)/$(CHATGPT_AUTH_FILE)" ]; then \
		printf 'missing %s/%s\n' "$(SIBLING_AUTH_DIR)" "$(CHATGPT_AUTH_FILE)" >&2; \
		exit 1; \
	fi
	cp "$(SIBLING_AUTH_DIR)/$(CHATGPT_AUTH_FILE)" "data/chatgpt/$(CHATGPT_AUTH_FILE)"
	chmod 0600 "data/chatgpt/$(CHATGPT_AUTH_FILE)"
	@printf 'imported ChatGPT auth into data/chatgpt/%s\n' "$(CHATGPT_AUTH_FILE)"

migrate: init
	$(COMPOSE) build $(APP_BUILD_SERVICE)
	$(COMPOSE) run --rm analytics-migrations

build:
	$(COMPOSE) build $(APP_BUILD_SERVICE)

down:
	$(COMPOSE) down

restart:
	$(COMPOSE) restart $(SERVICE)

logs:
	$(COMPOSE) logs -f $(SERVICE)

ps:
	$(COMPOSE) ps

mcp: init
	@. ./.env; printf 'Analytics MCP endpoint: http://127.0.0.1:%s/mcp/\n' "$${ANALYTICS_BACKEND_PORT:-28010}"

models:
	uv run python scripts/update_litellm_models.py

test:
	uv run pytest -q

e2e:
	@if [ -f .env ]; then set -a; . ./.env; set +a; fi; \
	uv run python scripts/e2e_chatgpt_headroom.py

analytics-smoke:
	@if [ -f .env ]; then set -a; . ./.env; set +a; fi; \
	HEADROOM_ANALYTICS_URL=$${HEADROOM_ANALYTICS_URL:-http://127.0.0.1:28010}; \
	ANALYTICS_BACKEND_URL=$${ANALYTICS_BACKEND_URL:-http://127.0.0.1:28010}; \
	export HEADROOM_ANALYTICS_URL ANALYTICS_BACKEND_URL; \
	uv run python scripts/e2e_analytics_smoke.py && \
	uv run python scripts/e2e_headroom_ccr_smoke.py && \
	uv run python scripts/e2e_litellm_buffer_smoke.py && \
	uv run python scripts/e2e_mcp_otel_smoke.py && \
	uv run python scripts/e2e_query_stats_smoke.py && \
	uv run python scripts/e2e_dashboard_stats_smoke.py && \
	uv run python scripts/e2e_simulation_smoke.py

codex-session-report:
	uv run python scripts/report_recent_codex_session.py \
		--client "$${CODEX_SESSION_CLIENT:-codex}" \
		--hours "$${CODEX_SESSION_HOURS:-1}" \
		$${CODEX_SESSION_OUT_DIR:+--out-dir "$${CODEX_SESSION_OUT_DIR}"}

codex-session-html:
	@if [ -z "$${CODEX_SESSION_REPORT_JSON}" ]; then \
		printf 'set CODEX_SESSION_REPORT_JSON=tmp/.../report.json\n' >&2; \
		exit 1; \
	fi
	uv run python scripts/render_codex_session_html.py "$${CODEX_SESSION_REPORT_JSON}"

codex-session-story:
	@OUT_DIR="$${CODEX_SESSION_OUT_DIR:-tmp/codex-proxy-session-report/manual}"; \
	uv run python scripts/report_recent_codex_session.py \
		--client "$${CODEX_SESSION_CLIENT:-codex}" \
		--hours "$${CODEX_SESSION_HOURS:-1}" \
		--out-dir "$${OUT_DIR}" && \
	uv run python scripts/render_codex_session_html.py "$${OUT_DIR}/report.json"

lint:
	uv run ruff check .

format-check:
	uv run ruff format --check .

check: test lint format-check config

config: env
	$(COMPOSE) config >/tmp/litellm-proxy-headroom-compose.yml
	@printf 'compose config rendered to /tmp/litellm-proxy-headroom-compose.yml\n'

clean:
	rm -rf .pytest_cache .ruff_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
