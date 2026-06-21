from pathlib import Path

import yaml


def test_compose_wires_litellm_to_persistent_chatgpt_auth_and_phoenix() -> None:
    compose = yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))

    litellm = compose["services"]["litellm"]

    assert "./data/chatgpt:/data/chatgpt" in litellm["volumes"]
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


def test_compose_keeps_user_facing_services_bound_to_localhost() -> None:
    compose = yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))

    assert "127.0.0.1:4000:4000" in compose["services"]["litellm"]["ports"]
    assert "127.0.0.1:6006:6006" in compose["services"]["phoenix"]["ports"]
    assert "127.0.0.1:8080:8080" in compose["services"]["open-webui"]["ports"]


def test_compose_configures_openwebui_for_litellm_and_otel() -> None:
    compose = yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))

    open_webui = compose["services"]["open-webui"]["environment"]

    assert open_webui["OPENAI_API_BASE_URL"] == "http://litellm:4000/v1"
    assert open_webui["ENABLE_FORWARD_USER_INFO_HEADERS"] == "true"
    assert open_webui["ENABLE_OTEL"] == "true"
    assert open_webui["ENABLE_OTEL_TRACES"] == "true"
    assert open_webui["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://phoenix:4317"
