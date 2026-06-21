from pathlib import Path

import yaml


def test_litellm_config_uses_chatgpt_provider_and_headroom_callback() -> None:
    config = yaml.safe_load(Path("config/litellm.yaml").read_text(encoding="utf-8"))

    [model] = config["model_list"]
    assert model["model_name"] == "chatgpt"
    assert model["litellm_params"]["model"].startswith("chatgpt/")
    assert config["litellm_settings"]["callbacks"] == [
        "headroom_litellm_callback.HeadroomCallback"
    ]
