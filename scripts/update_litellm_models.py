from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG = Path("config/litellm.yaml")
DEFAULT_CODEX_COMMAND = ("codex", "debug", "models")
CLAUDE_GPT55_ALIASES = (
    ("sonnet", "Claude Sonnet alias via GPT-5.5"),
    ("opus", "Claude Opus alias via GPT-5.5"),
    ("fable", "Claude Fable alias via GPT-5.5"),
    ("claude-sonnet-5", "Claude Sonnet 5 alias via GPT-5.5"),
    ("claude-opus-5", "Claude Opus 5 alias via GPT-5.5"),
    ("claude-sonnet-4-6", "Claude Sonnet 4.6 alias via GPT-5.5"),
    ("claude-opus-4-6", "Claude Opus 4.6 alias via GPT-5.5"),
    ("claude-sonnet-4-5", "Claude Sonnet 4.5 alias via GPT-5.5"),
    ("claude-opus-4-5", "Claude Opus 4.5 alias via GPT-5.5"),
    ("claude-fable-5", "Claude Fable 5 alias via GPT-5.5"),
)


class IndentedSafeDumper(yaml.SafeDumper):
    def increase_indent(self, flow: bool = False, indentless: bool = False):
        return super().increase_indent(flow=flow, indentless=False)


def load_codex_models(
    command: tuple[str, ...] = DEFAULT_CODEX_COMMAND,
) -> dict[str, Any]:
    result = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def generated_model_list(codex_models: dict[str, Any]) -> list[dict[str, Any]]:
    models = codex_models.get("models")
    if not isinstance(models, list):
        raise ValueError("Expected codex debug models JSON to contain a models list")

    model_list: list[dict[str, Any]] = []
    seen: set[str] = set()

    for model in models:
        if not isinstance(model, dict):
            continue

        slug = model.get("slug")
        if not isinstance(slug, str) or not slug:
            continue

        if slug in seen:
            continue
        seen.add(slug)

        if model.get("supported_in_api") is not True:
            print(f"Skipping {slug}: supported_in_api is not true", file=sys.stderr)
            continue

        model_info: dict[str, Any] = {
            "mode": "responses",
            "codex_slug": slug,
        }

        display_name = model.get("display_name")
        if isinstance(display_name, str) and display_name:
            model_info["display_name"] = display_name

        default_reasoning_level = model.get("default_reasoning_level")
        if isinstance(default_reasoning_level, str) and default_reasoning_level:
            model_info["codex_default_reasoning_level"] = default_reasoning_level

        model_list.append(
            {
                "model_name": slug,
                "model_info": model_info,
                "litellm_params": {
                    "model": f"chatgpt/{slug}",
                },
            }
        )

        if slug == "gpt-5.5":
            for alias, display_name in CLAUDE_GPT55_ALIASES:
                model_list.append(
                    {
                        "model_name": alias,
                        "model_info": {
                            "mode": "responses",
                            "alias_for": "gpt-5.5",
                            "display_name": display_name,
                            "codex_default_reasoning_level": "xhigh",
                        },
                        "litellm_params": {"model": "chatgpt/gpt-5.5"},
                    }
                )

    if not model_list:
        raise ValueError("No API-supported Codex models found")

    return model_list


def update_config(config_path: Path, codex_models: dict[str, Any]) -> None:
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError(f"{config_path} must contain a YAML mapping")

    config["model_list"] = generated_model_list(codex_models)
    config_path.write_text(
        yaml.dump(config, Dumper=IndentedSafeDumper, sort_keys=False),
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Repopulate config/litellm.yaml model_list from codex debug models.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help="LiteLLM YAML config to update.",
    )
    parser.add_argument(
        "--models-json",
        type=Path,
        help="Use a saved codex debug models JSON file instead of invoking codex.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.models_json:
        codex_models = json.loads(args.models_json.read_text(encoding="utf-8"))
    else:
        codex_models = load_codex_models()

    update_config(args.config, codex_models)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
