from pathlib import Path

import yaml


def _compose() -> dict:
    return yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))


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
        "litellm",
        "--config",
        "/app/config/litellm.yaml",
        "--host",
        "0.0.0.0",
        "--port",
        "4000",
    ]
    assert litellm["ports"] == ["127.0.0.1:4000:4000"]


def test_default_stack_does_not_run_headroom_proxy_or_mcp_containers() -> None:
    services = _compose()["services"]

    assert "headroom" not in services
    assert "headroom-mcp" not in services
    assert all("dashboard" not in name for name in services)


def test_litellm_service_does_not_masquerade_as_headroom_wrap_stack() -> None:
    environment = _compose()["services"]["litellm"]["environment"]

    assert "HEADROOM_AGENT_TYPE" not in environment
    assert "HEADROOM_STACK" not in environment


def test_compose_keeps_user_facing_services_bound_to_localhost() -> None:
    services = _compose()["services"]

    assert "127.0.0.1:4000:4000" in services["litellm"]["ports"]
    assert (
        "127.0.0.1:${ANALYTICS_BACKEND_PORT:-8010}:8010"
        in services["analytics-backend"]["ports"]
    )
    assert "127.0.0.1:6006:6006" in services["phoenix"]["ports"]
    assert "127.0.0.1:8080:8080" in services["open-webui"]["ports"]


def test_compose_configures_openwebui_for_litellm_and_otel() -> None:
    open_webui = _compose()["services"]["open-webui"]["environment"]

    assert open_webui["OPENAI_API_BASE_URL"] == "http://litellm:4000/v1"
    assert open_webui["ENABLE_FORWARD_USER_INFO_HEADERS"] == "true"
    assert open_webui["ENABLE_OTEL"] == "true"
    assert open_webui["ENABLE_OTEL_TRACES"] == "true"
    assert open_webui["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://phoenix:4317"


def test_compose_runs_custom_analytics_backend_as_ingress() -> None:
    backend = _compose()["services"]["analytics-backend"]

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
