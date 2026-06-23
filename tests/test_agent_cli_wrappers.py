import json
import os
import subprocess
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _write_fake_cli(path: Path) -> None:
    path.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
python3 - "$@" <<'PY'
import json
import os
import sys
from pathlib import Path

capture = {
    "args": sys.argv[1:],
    "codex_home": os.environ.get("CODEX_HOME"),
    "openai_api_key_present": bool(os.environ.get("OPENAI_API_KEY")),
    "anthropic_base_url": os.environ.get("ANTHROPIC_BASE_URL"),
    "anthropic_auth_token_present": bool(os.environ.get("ANTHROPIC_AUTH_TOKEN")),
    "gateway_model_discovery": os.environ.get("CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY"),
}
Path(os.environ["FAKE_CLI_CAPTURE"]).write_text(json.dumps(capture))
PY
""",
    )
    path.chmod(0o755)


def _base_env(fake_bin: Path, capture_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}{os.pathsep}{env.get('PATH', '')}",
            "FAKE_CLI_CAPTURE": str(capture_path),
            "LITELLM_MASTER_KEY": "sk-test-wrapper-key",
        }
    )
    return env


def test_wrapper_scripts_have_valid_bash_syntax() -> None:
    for script in ("bin/codex-litellm", "bin/claude-litellm"):
        subprocess.run(
            ["bash", "-n", str(REPO_ROOT / script)],
            check=True,
            cwd=REPO_ROOT,
        )


def test_codex_wrapper_generates_responses_provider_config(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_cli(fake_bin / "codex")
    capture_path = tmp_path / "capture.json"
    codex_home = tmp_path / "codex-home"

    env = _base_env(fake_bin, capture_path)
    env["CODEX_LITELLM_HOME"] = str(codex_home)

    subprocess.run(
        [str(REPO_ROOT / "bin/codex-litellm"), "exec", "health marker"],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )

    capture = json.loads(capture_path.read_text())
    assert capture["args"] == ["--profile", "litellm", "exec", "health marker"]
    assert capture["codex_home"] == str(codex_home)
    assert capture["openai_api_key_present"] is True

    base_config = tomllib.loads((codex_home / "config.toml").read_text())
    profile_config = tomllib.loads((codex_home / "litellm.config.toml").read_text())

    assert base_config["mcp_servers"]["analytics"]["url"] == (
        "http://127.0.0.1:8010/mcp/"
    )
    assert profile_config["model"] == "gpt-5.4-mini"
    assert profile_config["model_provider"] == "litellm"
    provider = profile_config["model_providers"]["litellm"]
    assert provider == {
        "name": "Local LiteLLM",
        "base_url": "http://127.0.0.1:4000/v1",
        "env_key": "OPENAI_API_KEY",
        "wire_api": "responses",
    }
    assert "sk-test-wrapper-key" not in (codex_home / "config.toml").read_text()
    assert "sk-test-wrapper-key" not in (codex_home / "litellm.config.toml").read_text()


def test_claude_wrapper_generates_mcp_config_and_gateway_env(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_cli(fake_bin / "claude")
    capture_path = tmp_path / "capture.json"
    state_dir = tmp_path / "claude-state"

    env = _base_env(fake_bin, capture_path)
    env["CLAUDE_LITELLM_STATE_DIR"] = str(state_dir)

    subprocess.run(
        [str(REPO_ROOT / "bin/claude-litellm"), "--print", "health marker"],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )

    capture = json.loads(capture_path.read_text())
    assert capture["anthropic_base_url"] == "http://127.0.0.1:4000"
    assert capture["anthropic_auth_token_present"] is True
    assert capture["gateway_model_discovery"] == "1"
    assert capture["args"] == [
        "--setting-sources",
        "project",
        "--mcp-config",
        str(state_dir / "mcp.json"),
        "--strict-mcp-config",
        "--allowedTools",
        "mcp__analytics__*",
        "--model",
        "gpt-5.4-mini",
        "--print",
        "health marker",
    ]

    mcp_config = json.loads((state_dir / "mcp.json").read_text())
    assert mcp_config == {
        "mcpServers": {
            "analytics": {
                "type": "http",
                "url": "http://127.0.0.1:8010/mcp/",
            }
        }
    }
    assert "sk-test-wrapper-key" not in (state_dir / "mcp.json").read_text()
