import importlib.util
from pathlib import Path

import yaml


def _load_update_script():
    spec = importlib.util.spec_from_file_location(
        "update_litellm_models",
        Path("scripts/update_litellm_models.py"),
    )
    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generated_model_list_maps_codex_slugs_to_chatgpt_models() -> None:
    module = _load_update_script()

    model_list = module.generated_model_list(
        {
            "models": [
                {
                    "slug": "gpt-5.5",
                    "display_name": "GPT-5.5",
                    "default_reasoning_level": "xhigh",
                    "supported_in_api": True,
                },
                {
                    "slug": "hidden-model",
                    "display_name": "Hidden Model",
                    "supported_in_api": False,
                },
                {
                    "slug": "gpt-5.4-mini",
                    "display_name": "GPT-5.4-Mini",
                    "default_reasoning_level": "medium",
                    "supported_in_api": True,
                },
            ]
        }
    )

    assert model_list == [
        {
            "model_name": "gpt-5.5",
            "model_info": {
                "mode": "responses",
                "codex_slug": "gpt-5.5",
                "display_name": "GPT-5.5",
                "codex_default_reasoning_level": "xhigh",
            },
            "litellm_params": {"model": "chatgpt/gpt-5.5"},
        },
        {
            "model_name": "sonnet",
            "model_info": {
                "mode": "responses",
                "alias_for": "gpt-5.5",
                "display_name": "Claude Sonnet alias via GPT-5.5",
                "codex_default_reasoning_level": "xhigh",
            },
            "litellm_params": {"model": "chatgpt/gpt-5.5"},
        },
        {
            "model_name": "opus",
            "model_info": {
                "mode": "responses",
                "alias_for": "gpt-5.5",
                "display_name": "Claude Opus alias via GPT-5.5",
                "codex_default_reasoning_level": "xhigh",
            },
            "litellm_params": {"model": "chatgpt/gpt-5.5"},
        },
        {
            "model_name": "fable",
            "model_info": {
                "mode": "responses",
                "alias_for": "gpt-5.5",
                "display_name": "Claude Fable alias via GPT-5.5",
                "codex_default_reasoning_level": "xhigh",
            },
            "litellm_params": {"model": "chatgpt/gpt-5.5"},
        },
        {
            "model_name": "claude-sonnet-5",
            "model_info": {
                "mode": "responses",
                "alias_for": "gpt-5.5",
                "display_name": "Claude Sonnet 5 alias via GPT-5.5",
                "codex_default_reasoning_level": "xhigh",
            },
            "litellm_params": {"model": "chatgpt/gpt-5.5"},
        },
        {
            "model_name": "claude-opus-5",
            "model_info": {
                "mode": "responses",
                "alias_for": "gpt-5.5",
                "display_name": "Claude Opus 5 alias via GPT-5.5",
                "codex_default_reasoning_level": "xhigh",
            },
            "litellm_params": {"model": "chatgpt/gpt-5.5"},
        },
        {
            "model_name": "claude-sonnet-4-6",
            "model_info": {
                "mode": "responses",
                "alias_for": "gpt-5.5",
                "display_name": "Claude Sonnet 4.6 alias via GPT-5.5",
                "codex_default_reasoning_level": "xhigh",
            },
            "litellm_params": {"model": "chatgpt/gpt-5.5"},
        },
        {
            "model_name": "claude-opus-4-6",
            "model_info": {
                "mode": "responses",
                "alias_for": "gpt-5.5",
                "display_name": "Claude Opus 4.6 alias via GPT-5.5",
                "codex_default_reasoning_level": "xhigh",
            },
            "litellm_params": {"model": "chatgpt/gpt-5.5"},
        },
        {
            "model_name": "claude-sonnet-4-5",
            "model_info": {
                "mode": "responses",
                "alias_for": "gpt-5.5",
                "display_name": "Claude Sonnet 4.5 alias via GPT-5.5",
                "codex_default_reasoning_level": "xhigh",
            },
            "litellm_params": {"model": "chatgpt/gpt-5.5"},
        },
        {
            "model_name": "claude-opus-4-5",
            "model_info": {
                "mode": "responses",
                "alias_for": "gpt-5.5",
                "display_name": "Claude Opus 4.5 alias via GPT-5.5",
                "codex_default_reasoning_level": "xhigh",
            },
            "litellm_params": {"model": "chatgpt/gpt-5.5"},
        },
        {
            "model_name": "claude-fable-5",
            "model_info": {
                "mode": "responses",
                "alias_for": "gpt-5.5",
                "display_name": "Claude Fable 5 alias via GPT-5.5",
                "codex_default_reasoning_level": "xhigh",
            },
            "litellm_params": {"model": "chatgpt/gpt-5.5"},
        },
        {
            "model_name": "gpt-5.4-mini",
            "model_info": {
                "mode": "responses",
                "codex_slug": "gpt-5.4-mini",
                "display_name": "GPT-5.4-Mini",
                "codex_default_reasoning_level": "medium",
            },
            "litellm_params": {"model": "chatgpt/gpt-5.4-mini"},
        },
    ]


def test_update_config_replaces_only_model_list(tmp_path) -> None:
    module = _load_update_script()
    config_path = tmp_path / "litellm.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "model_list": [
                    {
                        "model_name": "old",
                        "litellm_params": {"model": "chatgpt/old"},
                    }
                ],
                "litellm_settings": {"callbacks": ["headroom"]},
                "general_settings": {"master_key": "os.environ/KEY"},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    module.update_config(
        config_path,
        {
            "models": [
                {
                    "slug": "gpt-5.4",
                    "display_name": "GPT-5.4",
                    "supported_in_api": True,
                }
            ]
        },
    )

    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    assert config["model_list"] == [
        {
            "model_name": "gpt-5.4",
            "model_info": {
                "mode": "responses",
                "codex_slug": "gpt-5.4",
                "display_name": "GPT-5.4",
            },
            "litellm_params": {"model": "chatgpt/gpt-5.4"},
        }
    ]
    assert config["litellm_settings"] == {"callbacks": ["headroom"]}
    assert config["general_settings"] == {"master_key": "os.environ/KEY"}
