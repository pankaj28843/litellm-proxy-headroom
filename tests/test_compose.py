from pathlib import Path

import yaml


def _compose() -> dict:
    return yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))


def _dockerfile_froms(path: str) -> list[str]:
    froms = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.startswith("FROM "):
            froms.append(line.split()[1])
    return froms


def test_compose_wires_litellm_to_persistent_chatgpt_auth_and_phoenix() -> None:
    compose = _compose()
    litellm = compose["services"]["litellm"]

    assert "./data/chatgpt:/data/chatgpt" in litellm["volumes"]
    assert litellm["environment"]["CONFIG_FILE_PATH"] == "/app/config/litellm.yaml"
    assert litellm["environment"]["CHATGPT_TOKEN_DIR"] == "/data/chatgpt"
    assert litellm["environment"]["CHATGPT_DEFAULT_INSTRUCTIONS"] == (
        "${CHATGPT_DEFAULT_INSTRUCTIONS:- }"
    )
    assert litellm["environment"]["LITELLM_OTEL_V2"] == "true"
    assert (
        litellm["environment"]["PHOENIX_COLLECTOR_HTTP_ENDPOINT"]
        == "http://phoenix:6006/v1/traces"
    )
    assert litellm["environment"]["OTEL_SPAN_ATTRIBUTE_COUNT_LIMIT"] == "512"
    assert (
        litellm["environment"]["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"]
        == "no_content"
    )
    assert litellm["environment"]["HEADROOM_ANALYTICS_URL"] == (
        "http://analytics-backend:8010"
    )
    assert litellm["environment"]["HEADROOM_CCR_BACKEND"] == "analytics-postgres"
    assert (
        litellm["environment"]["HEADROOM_SAVINGS_PROFILE"]
        == "${HEADROOM_SAVINGS_PROFILE:-agent-90}"
    )
    assert litellm["command"] == [
        "uvicorn",
        "litellm_proxy_headroom.litellm_app:app",
        "--host",
        "0.0.0.0",
        "--port",
        "4000",
    ]
    assert litellm["ports"] == [
        "${LITELLM_VM_BIND_HOST:-10.20.30.1}:${LITELLM_PROXY_PORT:-24040}:4000",
    ]
    assert "http://127.0.0.1:4000/v1/models" in litellm["healthcheck"]["test"][-1]
    assert "LITELLM_MASTER_KEY" in litellm["healthcheck"]["test"][-1]


def test_default_stack_does_not_run_headroom_proxy_or_mcp_containers() -> None:
    services = _compose()["services"]

    assert "headroom" not in services
    assert "headroom-mcp" not in services
    assert all("dashboard" not in name for name in services)


def test_compose_uses_latest_stable_official_postgres_images() -> None:
    services = _compose()["services"]

    assert services["analytics-db"]["image"] == "postgres:18"
    assert services["phoenix-db"]["image"] == "postgres:18"
    assert services["analytics-db"]["volumes"] == [
        "analytics_pgdata:/var/lib/postgresql"
    ]
    assert services["phoenix-db"]["volumes"] == ["phoenix_pgdata:/var/lib/postgresql"]


def test_compose_uses_vendor_phoenix_image_and_repo_images_for_owned_code() -> None:
    services = _compose()["services"]
    app_image = "litellm-proxy-headroom-app:latest"
    app_build = {"context": ".", "dockerfile": "Dockerfile"}

    assert services["analytics-db"]["image"] == "postgres:18"
    assert services["phoenix-db"]["image"] == "postgres:18"
    assert services["analytics-backend"]["image"] == app_image
    assert services["analytics-migrations"]["image"] == app_image
    assert services["litellm"]["image"] == app_image
    assert services["analytics-backend"]["build"] == app_build
    assert services["analytics-migrations"]["build"] == app_build
    assert services["litellm"]["build"] == app_build
    assert services["phoenix"]["image"] == "arizephoenix/phoenix:version-17.15.0"
    assert "build" not in services["phoenix"]


def test_repo_dockerfiles_use_approved_python_base_images() -> None:
    approved_base = "ghcr.io/astral-sh/uv:python3.14-bookworm-slim"

    assert _dockerfile_froms("Dockerfile") == [approved_base]
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    assert "COPY --from=" not in dockerfile
    assert "--mount=type=cache,target=/root/.cache/uv" in dockerfile
    assert "uv sync --frozen --no-dev --no-install-project" in dockerfile
    assert "UV_COMPILE_BYTECODE=1" not in dockerfile
    assert dockerfile.index("uv sync --frozen --no-dev --no-install-project") < (
        dockerfile.index("COPY README.md ./README.md")
    )
    assert dockerfile.index("uv sync --frozen --no-dev --no-install-project") < (
        dockerfile.index("COPY src ./src")
    )
    assert "COPY alembic.ini ./alembic.ini" in dockerfile
    assert "COPY alembic ./alembic" in dockerfile


def test_litellm_service_does_not_masquerade_as_headroom_wrap_stack() -> None:
    environment = _compose()["services"]["litellm"]["environment"]

    assert "HEADROOM_AGENT_TYPE" not in environment
    assert "HEADROOM_STACK" not in environment


def test_compose_keeps_user_facing_services_private_except_litellm_vm_bind() -> None:
    services = _compose()["services"]

    assert services["litellm"]["ports"] == [
        "${LITELLM_VM_BIND_HOST:-10.20.30.1}:${LITELLM_PROXY_PORT:-24040}:4000",
    ]
    assert (
        "127.0.0.1:${ANALYTICS_BACKEND_PORT:-28010}:8010"
        in services["analytics-backend"]["ports"]
    )
    assert "127.0.0.1:${PHOENIX_HOST_PORT:-26006}:6006" in services["phoenix"][
        "ports"
    ]
    assert (
        "127.0.0.1:${ANALYTICS_POSTGRES_PORT:-55432}:5432"
        in services["analytics-db"]["ports"]
    )


def test_compose_runs_custom_analytics_backend_as_ingress() -> None:
    backend = _compose()["services"]["analytics-backend"]

    assert backend["depends_on"]["analytics-db"]["condition"] == "service_healthy"
    assert backend["depends_on"]["phoenix"]["condition"] == "service_healthy"
    assert backend["command"] == [
        "uvicorn",
        "litellm_proxy_headroom.analytics.adapters.api.app:create_app",
        "--factory",
        "--host",
        "0.0.0.0",
        "--port",
        "8010",
    ]
    assert backend["environment"]["HEADROOM_ANALYTICS_OTEL_ENABLED"] == "true"
    assert backend["environment"]["OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"] == (
        "http://phoenix:6006/v1/traces"
    )
    assert "http://127.0.0.1:8010/ready" in backend["healthcheck"]["test"][-1]
    assert "LITELLM_PROXY_ANALYTICS_OTEL_EXCLUDED_URLS" in backend["environment"]


def test_compose_configures_phoenix_vendor_image_with_postgres() -> None:
    phoenix = _compose()["services"]["phoenix"]

    assert phoenix["image"] == "arizephoenix/phoenix:version-17.15.0"
    assert phoenix["depends_on"]["phoenix-db"]["condition"] == "service_healthy"
    assert phoenix["environment"]["PHOENIX_HOST"] == "0.0.0.0"
    assert phoenix["environment"]["PHOENIX_PORT"] == "6006"
    assert phoenix["environment"]["PHOENIX_GRPC_PORT"] == "4317"
    assert phoenix["environment"]["PHOENIX_SQL_DATABASE_URL"] == (
        "postgresql://phoenix:${PHOENIX_DB_PASSWORD:"
        "?set PHOENIX_DB_PASSWORD in .env}@phoenix-db:5432/phoenix"
    )
    assert "http://127.0.0.1:6006/" in phoenix["healthcheck"]["test"][-1]
    assert "PHOENIX_POSTGRES_HOST" not in phoenix["environment"]


def test_compose_waits_for_runtime_health_before_litellm_start() -> None:
    litellm = _compose()["services"]["litellm"]

    assert litellm["depends_on"]["analytics-backend"]["condition"] == (
        "service_healthy"
    )
    assert litellm["depends_on"]["phoenix"]["condition"] == "service_healthy"


def test_compose_runs_analytics_migrations_as_one_shot_container() -> None:
    migrations = _compose()["services"]["analytics-migrations"]

    assert migrations["image"] == "litellm-proxy-headroom-app:latest"
    assert migrations["build"] == {"context": ".", "dockerfile": "Dockerfile"}
    assert migrations["profiles"] == ["migrate"]
    assert migrations["restart"] == "no"
    assert migrations["depends_on"]["analytics-db"]["condition"] == "service_healthy"
    assert migrations["command"] == ["alembic", "upgrade", "head"]
    assert migrations["environment"]["ANALYTICS_DATABASE_URL"] == (
        "postgresql+asyncpg://${ANALYTICS_POSTGRES_USER:-analytics}:"
        "${ANALYTICS_POSTGRES_PASSWORD:-analytics}@analytics-db:5432/"
        "${ANALYTICS_POSTGRES_DB:-analytics}"
    )


def test_make_up_runs_analytics_migration_container_before_stack_start() -> None:
    makefile = Path("Makefile").read_text(encoding="utf-8")

    build_image = "\t$(COMPOSE) build $(APP_BUILD_SERVICE)"
    run_migrations = "\t$(COMPOSE) run --rm analytics-migrations"
    start_stack = "\t$(COMPOSE) up -d --no-build --wait --wait-timeout 240"
    assert build_image in makefile
    assert run_migrations in makefile
    assert start_stack in makefile
    assert makefile.index(build_image) < makefile.index(run_migrations)
    assert makefile.index(run_migrations) < makefile.index(start_stack)
    assert "\t$(COMPOSE) up -d --build --wait --wait-timeout 240" not in makefile
    assert "\tuv run alembic upgrade head" not in makefile
