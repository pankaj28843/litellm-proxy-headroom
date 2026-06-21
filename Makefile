SHELL := /bin/sh

COMPOSE ?= docker compose
SIBLING_AUTH_DIR ?= ../litellm-proxy/data/chatgpt
CHATGPT_AUTH_FILE ?= auth.json

.DEFAULT_GOAL := up

.PHONY: up init env auth-import build down restart logs ps test lint format-check check config clean

up: init
	$(COMPOSE) up -d --build
	@printf '\nOpen WebUI: http://127.0.0.1:8080\n'
	@printf 'Phoenix:    http://127.0.0.1:6006\n'
	@printf 'LiteLLM:    http://127.0.0.1:4000\n'
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

build:
	$(COMPOSE) build

down:
	$(COMPOSE) down

restart:
	$(COMPOSE) restart $(SERVICE)

logs:
	$(COMPOSE) logs -f $(SERVICE)

ps:
	$(COMPOSE) ps

test:
	uv run pytest -q

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
