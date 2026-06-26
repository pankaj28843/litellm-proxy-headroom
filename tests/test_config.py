from pathlib import Path

import yaml


def _litellm_config() -> dict:
    return yaml.safe_load(Path("config/litellm.yaml").read_text(encoding="utf-8"))


def test_litellm_config_uses_generated_chatgpt_models_and_headroom_callback() -> None:
    config = _litellm_config()

    model_list = config["model_list"]
    model_names = [model["model_name"] for model in model_list]

    assert model_names == [
        "gpt-5.5",
        "gpt-5.4",
        "gpt-5.4-mini",
        "codex-auto-review",
    ]
    for model in model_list:
        assert model["model_info"]["mode"] == "responses"
        assert model["model_info"]["codex_slug"] == model["model_name"]
        assert model["litellm_params"]["model"] == f"chatgpt/{model['model_name']}"

    assert config["litellm_settings"]["callbacks"] == [
        "headroom_litellm_callback.headroom_callback",
        "arize_phoenix",
    ]


def test_litellm_config_keeps_internal_attribution_headers_local() -> None:
    config = _litellm_config()

    general_settings = config.get("general_settings", {})
    assert general_settings.get("forward_client_headers_to_llm_api") is not True


def test_litellm_config_keeps_spend_tags_to_openwebui_ids() -> None:
    config = _litellm_config()

    headers = config["litellm_settings"]["extra_spend_tag_headers"]
    assert headers == ["x-openwebui-chat-id", "x-openwebui-message-id"]
