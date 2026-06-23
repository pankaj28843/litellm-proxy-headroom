from pathlib import Path

import yaml


def test_compose_wires_litellm_to_persistent_chatgpt_auth_and_phoenix() -> None:
    compose = yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))

    litellm = compose["services"]["litellm"]

    assert "./data/chatgpt:/data/chatgpt" in litellm["volumes"]
    assert litellm["environment"]["CONFIG_FILE_PATH"] == "/app/config/litellm.yaml"
    assert litellm["environment"]["CHATGPT_TOKEN_DIR"] == "/data/chatgpt"
    assert litellm["environment"]["LITELLM_OTEL_V2"] == "true"
    assert (
        litellm["environment"]["PHOENIX_COLLECTOR_HTTP_ENDPOINT"]
        == "http://phoenix:6006/v1/traces"
    )
    assert (
        litellm["environment"]["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"]
        == "no_content"
    )
    assert "./data/headroom:/data/headroom" in litellm["volumes"]
    assert litellm["command"] == [
        "litellm",
        "--config",
        "/app/config/litellm.yaml",
        "--host",
        "0.0.0.0",
        "--port",
        "4000",
    ]
    assert litellm["expose"] == ["4000"]


def test_compose_runs_headroom_as_the_public_proxy() -> None:
    compose = yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))

    headroom = compose["services"]["headroom"]

    assert headroom["environment"]["HEADROOM_WORKSPACE_DIR"] == "/data/headroom"
    assert headroom["environment"]["OPENAI_TARGET_API_URL"] == "http://litellm:4000"
    assert "127.0.0.1:4000:4000" in headroom["ports"]
    assert "./data/headroom:/data/headroom" in headroom["volumes"]


def test_compose_keeps_user_facing_services_bound_to_localhost() -> None:
    compose = yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))

    assert "127.0.0.1:4000:4000" in compose["services"]["headroom"]["ports"]
    assert "127.0.0.1:6006:6006" in compose["services"]["phoenix"]["ports"]
    assert "127.0.0.1:8080:8080" in compose["services"]["open-webui"]["ports"]


def test_compose_configures_openwebui_for_litellm_and_otel() -> None:
    compose = yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))

    open_webui = compose["services"]["open-webui"]["environment"]

    assert open_webui["OPENAI_API_BASE_URL"] == "http://headroom:4000/v1"
    assert open_webui["ENABLE_FORWARD_USER_INFO_HEADERS"] == "true"
    assert open_webui["ENABLE_OTEL"] == "true"
    assert open_webui["ENABLE_OTEL_TRACES"] == "true"
    assert open_webui["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://phoenix:4317"


def test_compose_includes_headroom_mcp_stdio_service() -> None:
    compose = yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))

    mcp = compose["services"]["headroom-mcp"]

    assert mcp["profiles"] == ["mcp"]
    assert mcp["environment"]["HEADROOM_WORKSPACE_DIR"] == "/data/headroom"
    assert mcp["command"] == [
        "headroom",
        "mcp",
        "serve",
        "--proxy-url",
        "http://headroom:4000",
    ]
    assert "./data/headroom:/data/headroom" in mcp["volumes"]


def test_default_stack_has_one_headroom_proxy_container_and_no_dashboard_service() -> (
    None
):
    compose = yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]

    assert all("dashboard" not in name for name in services)

    default_headroom_services = [
        name
        for name, service in services.items()
        if name.startswith("headroom") and not service.get("profiles")
    ]
    assert default_headroom_services == ["headroom"]

    headroom = services["headroom"]
    assert headroom["environment"]["OPENAI_TARGET_API_URL"] == "http://litellm:4000"
    assert "127.0.0.1:4000:4000" in headroom["ports"]
