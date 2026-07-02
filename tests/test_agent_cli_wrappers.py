import json
import os
import subprocess
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WRAPPER_TEST_ENV_PREFIXES = (
    "CODEX_LITELLM_",
    "CLAUDE_LITELLM_",
    "OPENCODE_LITELLM_",
    "COPILOT_LITELLM_",
    "PI_LITELLM_",
    "COPILOT_PROVIDER_",
)
WRAPPER_TEST_ENV_NAMES = {
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_CUSTOM_HEADERS",
    "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY",
    "CLAUDE_CONFIG_DIR",
    "CODEX_HOME",
    "COPILOT_AGENT_REQUEST_HEADERS",
    "COPILOT_AUTO_UPDATE",
    "COPILOT_HOME",
    "COPILOT_MODEL",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENCODE_CONFIG",
    "OPENCODE_CONFIG_DIR",
    "PI_CODING_AGENT_DIR",
    "XDG_CACHE_HOME",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
}


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
    "home": os.environ.get("HOME"),
    "args": sys.argv[1:],
    "codex_home": os.environ.get("CODEX_HOME"),
    "codex_litellm_client": os.environ.get("CODEX_LITELLM_CLIENT"),
    "codex_litellm_project": os.environ.get("CODEX_LITELLM_PROJECT"),
    "codex_litellm_reasoning_effort": os.environ.get("CODEX_LITELLM_REASONING_EFFORT"),
    "codex_litellm_model_verbosity": os.environ.get("CODEX_LITELLM_MODEL_VERBOSITY"),
    "codex_litellm_compression_mode": os.environ.get("CODEX_LITELLM_COMPRESSION_MODE"),
    "codex_litellm_responses_provider_passthrough": os.environ.get(
        "CODEX_LITELLM_RESPONSES_PROVIDER_PASSTHROUGH"
    ),
    "openai_api_key_present": bool(os.environ.get("OPENAI_API_KEY")),
    "openai_base_url": os.environ.get("OPENAI_BASE_URL"),
    "anthropic_base_url": os.environ.get("ANTHROPIC_BASE_URL"),
    "anthropic_custom_headers": os.environ.get("ANTHROPIC_CUSTOM_HEADERS"),
    "claude_config_dir": os.environ.get("CLAUDE_CONFIG_DIR"),
    "claude_litellm_compression_mode": os.environ.get("CLAUDE_LITELLM_COMPRESSION_MODE"),
    "anthropic_auth_token_present": bool(os.environ.get("ANTHROPIC_AUTH_TOKEN")),
    "anthropic_api_key_present": bool(os.environ.get("ANTHROPIC_API_KEY")),
    "opencode_config": os.environ.get("OPENCODE_CONFIG"),
    "opencode_config_dir": os.environ.get("OPENCODE_CONFIG_DIR"),
    "opencode_litellm_client": os.environ.get("OPENCODE_LITELLM_CLIENT"),
    "opencode_litellm_project": os.environ.get("OPENCODE_LITELLM_PROJECT"),
    "opencode_litellm_compression_mode": os.environ.get("OPENCODE_LITELLM_COMPRESSION_MODE"),
    "copilot_home": os.environ.get("COPILOT_HOME"),
    "copilot_auto_update": os.environ.get("COPILOT_AUTO_UPDATE"),
    "copilot_model": os.environ.get("COPILOT_MODEL"),
    "copilot_provider_base_url": os.environ.get("COPILOT_PROVIDER_BASE_URL"),
    "copilot_provider_type": os.environ.get("COPILOT_PROVIDER_TYPE"),
    "copilot_provider_api_key_present": bool(os.environ.get("COPILOT_PROVIDER_API_KEY")),
    "copilot_provider_bearer_token_present": bool(os.environ.get("COPILOT_PROVIDER_BEARER_TOKEN")),
    "copilot_provider_wire_api": os.environ.get("COPILOT_PROVIDER_WIRE_API"),
    "copilot_provider_transport": os.environ.get("COPILOT_PROVIDER_TRANSPORT"),
    "copilot_provider_model_id": os.environ.get("COPILOT_PROVIDER_MODEL_ID"),
    "copilot_provider_wire_model": os.environ.get("COPILOT_PROVIDER_WIRE_MODEL"),
    "copilot_provider_reasoning_effort": os.environ.get(
        "COPILOT_PROVIDER_REASONING_EFFORT"
    ),
    "copilot_provider_max_prompt_tokens": os.environ.get(
        "COPILOT_PROVIDER_MAX_PROMPT_TOKENS"
    ),
    "copilot_provider_max_output_tokens": os.environ.get(
        "COPILOT_PROVIDER_MAX_OUTPUT_TOKENS"
    ),
    "copilot_agent_request_headers": os.environ.get("COPILOT_AGENT_REQUEST_HEADERS"),
    "copilot_litellm_compression_mode": os.environ.get(
        "COPILOT_LITELLM_COMPRESSION_MODE"
    ),
    "pi_coding_agent_dir": os.environ.get("PI_CODING_AGENT_DIR"),
    "pi_litellm_client": os.environ.get("PI_LITELLM_CLIENT"),
    "pi_litellm_project": os.environ.get("PI_LITELLM_PROJECT"),
    "pi_litellm_compression_mode": os.environ.get("PI_LITELLM_COMPRESSION_MODE"),
    "xdg_config_home": os.environ.get("XDG_CONFIG_HOME"),
    "xdg_data_home": os.environ.get("XDG_DATA_HOME"),
    "xdg_cache_home": os.environ.get("XDG_CACHE_HOME"),
    "gateway_model_discovery": os.environ.get("CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY"),
}
Path(os.environ["FAKE_CLI_CAPTURE"]).write_text(json.dumps(capture))
PY
""",
    )
    path.chmod(0o755)


def _base_env(fake_bin: Path, capture_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    for key in list(env):
        if key in WRAPPER_TEST_ENV_NAMES or key.startswith(WRAPPER_TEST_ENV_PREFIXES):
            env.pop(key, None)
    env.update(
        {
            "PATH": f"{fake_bin}{os.pathsep}{env.get('PATH', '')}",
            "FAKE_CLI_CAPTURE": str(capture_path),
            "CODEX_LITELLM_INHERIT_NATIVE_CONFIG": "0",
            "CODEX_LITELLM_LINK_NATIVE_STATE": "0",
            "CLAUDE_LITELLM_LINK_NATIVE_STATE": "0",
            "COPILOT_LITELLM_LINK_NATIVE_STATE": "0",
            "LITELLM_MASTER_KEY": "sk-test-wrapper-key",
            "OPENCODE_LITELLM_LINK_NATIVE_STATE": "0",
            "PI_LITELLM_LINK_NATIVE_STATE": "0",
        }
    )
    return env


def test_wrapper_scripts_have_valid_syntax() -> None:
    subprocess.run(
        ["python3", "-m", "py_compile", str(REPO_ROOT / "bin/codex-litellm")],
        check=True,
        cwd=REPO_ROOT,
    )
    subprocess.run(
        ["python3", "-m", "py_compile", str(REPO_ROOT / "bin/claude-litellm")],
        check=True,
        cwd=REPO_ROOT,
    )
    subprocess.run(
        ["python3", "-m", "py_compile", str(REPO_ROOT / "bin/opencode-litellm")],
        check=True,
        cwd=REPO_ROOT,
    )
    subprocess.run(
        ["python3", "-m", "py_compile", str(REPO_ROOT / "bin/copilot-litellm")],
        check=True,
        cwd=REPO_ROOT,
    )
    subprocess.run(
        ["python3", "-m", "py_compile", str(REPO_ROOT / "bin/pi-litellm")],
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
    env["CODEX_LITELLM_MODEL"] = "gpt-5.4"
    env["CODEX_LITELLM_PROJECT"] = "custom-project"
    env["CODEX_LITELLM_REASONING_EFFORT"] = "high"
    env["CODEX_LITELLM_MODEL_VERBOSITY"] = "low"
    env["CODEX_LITELLM_COMPRESSION_MODE"] = "disabled"

    subprocess.run(
        [str(REPO_ROOT / "bin/codex-litellm"), "exec", "health marker"],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )

    capture = json.loads(capture_path.read_text())
    assert capture["args"] == ["exec", "health marker"]
    assert capture["codex_home"] == str(codex_home)
    assert capture["codex_litellm_client"] == "codex"
    assert capture["codex_litellm_project"] == "custom-project"
    assert capture["codex_litellm_reasoning_effort"] == "high"
    assert capture["codex_litellm_model_verbosity"] == "low"
    assert capture["codex_litellm_compression_mode"] == "off"
    assert capture["openai_api_key_present"] is True
    assert capture["openai_base_url"] == "http://10.20.30.1:24040/v1"

    base_config = tomllib.loads((codex_home / "config.toml").read_text())
    profile_config = tomllib.loads((codex_home / "litellm.config.toml").read_text())

    assert base_config["mcp_servers"]["analytics"]["url"] == (
        "http://10.20.30.1:28010/mcp/"
    )
    assert set(base_config["mcp_servers"]) == {"analytics"}
    assert "headroom" not in base_config["mcp_servers"]
    assert profile_config["model"] == "gpt-5.4"
    assert profile_config["model_reasoning_effort"] == "high"
    assert profile_config["model_verbosity"] == "low"
    assert profile_config["model_provider"] == "litellm"
    assert profile_config["openai_base_url"] == "http://10.20.30.1:24040/v1"
    provider = profile_config["model_providers"]["litellm"]
    assert provider == {
        "name": "Local LiteLLM",
        "base_url": "http://10.20.30.1:24040/v1",
        "env_key": "OPENAI_API_KEY",
        "wire_api": "responses",
        "supports_websockets": False,
        "env_http_headers": {
            "X-LLM-Proxy-Client": "CODEX_LITELLM_CLIENT",
            "X-LLM-Proxy-Project": "CODEX_LITELLM_PROJECT",
            "X-LLM-Proxy-Run": "LITELLM_PROXY_RUN_MARKER",
            "X-LLM-Proxy-Compression": "CODEX_LITELLM_COMPRESSION_MODE",
        },
    }
    assert "sk-test-wrapper-key" not in (codex_home / "config.toml").read_text()
    assert "sk-test-wrapper-key" not in (codex_home / "litellm.config.toml").read_text()


def test_codex_wrapper_persists_managed_model_preferences(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_cli(fake_bin / "codex")
    capture_path = tmp_path / "capture.json"
    codex_home = tmp_path / "codex-home"

    env = _base_env(fake_bin, capture_path)
    env["CODEX_LITELLM_HOME"] = str(codex_home)
    env["CODEX_LITELLM_MODEL"] = "gpt-5.4-mini"
    env["CODEX_LITELLM_REASONING_EFFORT"] = "low"
    env["CODEX_LITELLM_MODEL_VERBOSITY"] = "high"

    subprocess.run(
        [str(REPO_ROOT / "bin/codex-litellm"), "exec", "first"],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )

    env.pop("CODEX_LITELLM_MODEL")
    env.pop("CODEX_LITELLM_REASONING_EFFORT")
    env.pop("CODEX_LITELLM_MODEL_VERBOSITY")
    subprocess.run(
        [str(REPO_ROOT / "bin/codex-litellm"), "exec", "second"],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )

    profile_config = tomllib.loads((codex_home / "litellm.config.toml").read_text())
    preferences = json.loads((codex_home / "litellm-preferences.json").read_text())

    assert profile_config["model"] == "gpt-5.4-mini"
    assert profile_config["model_reasoning_effort"] == "low"
    assert profile_config["model_verbosity"] == "high"
    assert preferences == {
        "model": "gpt-5.4-mini",
        "model_reasoning_effort": "low",
        "model_verbosity": "high",
    }


def test_codex_wrapper_can_send_provider_passthrough_experiment_header(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_cli(fake_bin / "codex")
    capture_path = tmp_path / "capture.json"
    codex_home = tmp_path / "codex-home"

    env = _base_env(fake_bin, capture_path)
    env["CODEX_LITELLM_HOME"] = str(codex_home)
    env["CODEX_LITELLM_RESPONSES_PROVIDER_PASSTHROUGH"] = "disabled"

    subprocess.run(
        [str(REPO_ROOT / "bin/codex-litellm"), "exec", "health marker"],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )

    capture = json.loads(capture_path.read_text())
    profile_config = tomllib.loads((codex_home / "litellm.config.toml").read_text())
    provider = profile_config["model_providers"]["litellm"]

    assert capture["codex_litellm_responses_provider_passthrough"] == "off"
    assert (
        provider["env_http_headers"]["X-LLM-Proxy-Responses-Provider-Passthrough"]
        == "CODEX_LITELLM_RESPONSES_PROVIDER_PASSTHROUGH"
    )


def test_codex_wrapper_uses_configured_litellm_base_url(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_cli(fake_bin / "codex")
    capture_path = tmp_path / "capture.json"
    codex_home = tmp_path / "codex-home"

    env = _base_env(fake_bin, capture_path)
    env["CODEX_LITELLM_HOME"] = str(codex_home)
    env["CODEX_LITELLM_BASE_URL"] = "http://127.0.0.1:4100"

    subprocess.run(
        [str(REPO_ROOT / "bin/codex-litellm"), "exec", "health marker"],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )

    capture = json.loads(capture_path.read_text())
    profile_config = tomllib.loads((codex_home / "litellm.config.toml").read_text())
    provider = profile_config["model_providers"]["litellm"]

    assert capture["openai_base_url"] == "http://127.0.0.1:4100/v1"
    assert profile_config["openai_base_url"] == "http://127.0.0.1:4100/v1"
    assert provider["base_url"] == "http://127.0.0.1:4100/v1"


def test_codex_wrapper_can_enable_litellm_websockets(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_cli(fake_bin / "codex")
    capture_path = tmp_path / "capture.json"
    codex_home = tmp_path / "codex-home"

    env = _base_env(fake_bin, capture_path)
    env["CODEX_LITELLM_HOME"] = str(codex_home)
    env["CODEX_LITELLM_SUPPORTS_WEBSOCKETS"] = "true"

    subprocess.run(
        [str(REPO_ROOT / "bin/codex-litellm"), "exec", "health marker"],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )

    profile_config = tomllib.loads((codex_home / "litellm.config.toml").read_text())
    provider = profile_config["model_providers"]["litellm"]

    assert provider["supports_websockets"] is True


def test_codex_wrapper_uses_configured_analytics_mcp_url(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_cli(fake_bin / "codex")
    capture_path = tmp_path / "capture.json"
    codex_home = tmp_path / "codex-home"

    env = _base_env(fake_bin, capture_path)
    env["CODEX_LITELLM_HOME"] = str(codex_home)
    env["CODEX_LITELLM_ANALYTICS_URL"] = "http://127.0.0.1:8110"

    subprocess.run(
        [str(REPO_ROOT / "bin/codex-litellm"), "exec", "health marker"],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )

    base_config = tomllib.loads((codex_home / "config.toml").read_text())

    assert base_config["mcp_servers"]["analytics"]["url"] == (
        "http://127.0.0.1:8110/mcp/"
    )


def test_codex_wrapper_can_disable_analytics_mcp(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_cli(fake_bin / "codex")
    capture_path = tmp_path / "capture.json"
    codex_home = tmp_path / "codex-home"

    env = _base_env(fake_bin, capture_path)
    env["CODEX_LITELLM_HOME"] = str(codex_home)
    env["CODEX_LITELLM_DISABLE_ANALYTICS_MCP"] = "1"
    env["CODEX_LITELLM_ANALYTICS_URL"] = "http://user:secret@127.0.0.1:8110"

    subprocess.run(
        [str(REPO_ROOT / "bin/codex-litellm"), "exec", "health marker"],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )

    base_config = tomllib.loads((codex_home / "config.toml").read_text())

    assert "mcp_servers" not in base_config


def test_codex_wrapper_inherits_safe_native_config_for_parity(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_cli(fake_bin / "codex")
    capture_path = tmp_path / "capture.json"
    codex_home = tmp_path / "codex-home"
    native_home = tmp_path / ".codex"
    native_home.mkdir()
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (native_home / "config.toml").write_text(
        f'''
model_provider = "headroom"
openai_base_url = "http://127.0.0.1:18788/v1"
model = "gpt-5.5"
model_reasoning_effort = "xhigh"
model_verbosity = "medium"
approval_policy = "never"
sandbox_mode = "danger-full-access"
personality = "pragmatic"
service_tier = "default"
api_key = "must-not-copy"
model_catalog_json = "/tmp/must-not-copy-model-catalog.json"

[features]
unified_exec = true
shell_snapshot = true
goals = true
js_repl = false

[projects."{project_dir}"]
trust_level = "trusted"

[mcp_servers.node_repl.env]
TOKEN = "native-mcp-token"

[model_providers.headroom]
name = "OpenAI via Headroom proxy"
base_url = "http://127.0.0.1:18788/v1"
supports_websockets = true
requires_openai_auth = true
'''.lstrip()
    )

    env = _base_env(fake_bin, capture_path)
    env["HOME"] = str(tmp_path)
    env["CODEX_LITELLM_HOME"] = str(codex_home)
    env["CODEX_LITELLM_INHERIT_NATIVE_CONFIG"] = "1"

    subprocess.run(
        [str(REPO_ROOT / "bin/codex-litellm"), "exec", "health marker"],
        check=True,
        cwd=project_dir,
        env=env,
    )

    capture = json.loads(capture_path.read_text())
    base_config_text = (codex_home / "config.toml").read_text()
    profile_config_text = (codex_home / "litellm.config.toml").read_text()
    base_config = tomllib.loads(base_config_text)
    profile_config = tomllib.loads(profile_config_text)

    assert capture["codex_litellm_reasoning_effort"] is None
    assert profile_config["model"] == "gpt-5.5"
    assert profile_config["model_reasoning_effort"] == "xhigh"
    assert profile_config["model_verbosity"] == "medium"
    assert profile_config["model_provider"] == "litellm"
    assert profile_config["openai_base_url"] == "http://10.20.30.1:24040/v1"
    assert profile_config["model_providers"]["litellm"]["base_url"] == (
        "http://10.20.30.1:24040/v1"
    )

    assert base_config["approval_policy"] == "never"
    assert base_config["sandbox_mode"] == "danger-full-access"
    assert base_config["personality"] == "pragmatic"
    assert base_config["service_tier"] == "default"
    assert base_config["features"] == {
        "goals": True,
        "js_repl": False,
        "shell_snapshot": True,
        "unified_exec": True,
    }
    assert base_config["projects"][str(project_dir)]["trust_level"] == "trusted"
    assert set(base_config["mcp_servers"]) == {"analytics", "node_repl"}
    assert base_config["mcp_servers"]["node_repl"]["env"]["TOKEN"] == (
        "native-mcp-token"
    )
    assert 'model_provider = "headroom"' not in base_config_text
    assert 'openai_base_url = "http://127.0.0.1:18788/v1"' not in base_config_text
    assert "model_providers.headroom" not in base_config_text
    assert "must-not-copy-model-catalog" not in base_config_text
    assert "must-not-copy-model-catalog" not in profile_config_text
    assert profile_config["model_providers"]["litellm"]["supports_websockets"] is False
    assert "requires_openai_auth = true" not in profile_config_text
    assert 'api_key = "must-not-copy"' not in base_config_text
    assert "must-not-copy" not in profile_config_text


def test_codex_wrapper_excludes_native_model_catalog_cache(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_cli(fake_bin / "codex")
    capture_path = tmp_path / "capture.json"
    native_home = tmp_path / ".codex"
    native_home.mkdir()
    native_cache = native_home / "models_cache.json"
    native_cache.write_text('{"models":[{"slug":"gpt-5.5"}]}')
    codex_home = tmp_path / "codex-home"

    env = _base_env(fake_bin, capture_path)
    env["HOME"] = str(tmp_path)
    env["CODEX_LITELLM_HOME"] = str(codex_home)

    subprocess.run(
        [str(REPO_ROOT / "bin/codex-litellm"), "exec", "health marker"],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )

    base_config = tomllib.loads((codex_home / "config.toml").read_text())
    profile_config = tomllib.loads((codex_home / "litellm.config.toml").read_text())

    assert "model_catalog_json" not in base_config
    assert "model_catalog_json" not in profile_config
    assert not (codex_home / "models_cache.json").exists()


def test_codex_wrapper_rejects_empty_model_catalog_override(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_cli(fake_bin / "codex")
    capture_path = tmp_path / "capture.json"
    codex_home = tmp_path / "codex-home"
    empty_catalog = tmp_path / "empty-catalog.json"
    empty_catalog.write_text('{"models":[]}')

    env = _base_env(fake_bin, capture_path)
    env["CODEX_LITELLM_HOME"] = str(codex_home)
    env["CODEX_LITELLM_MODEL_CATALOG_JSON"] = str(empty_catalog)

    result = subprocess.run(
        [str(REPO_ROOT / "bin/codex-litellm"), "exec", "health marker"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "CODEX_LITELLM_MODEL_CATALOG_JSON" in result.stderr
    assert not capture_path.exists()
    assert not (codex_home / "config.toml").exists()


def test_codex_wrapper_defaults_to_managed_home_and_links_native_state(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_cli(fake_bin / "codex")
    capture_path = tmp_path / "capture.json"
    native_home = tmp_path / ".codex"
    managed_home = tmp_path / ".codex-headroom"
    native_sessions = native_home / "sessions"
    native_sessions.mkdir(parents=True)
    native_auth = native_home / "auth.json"
    native_auth.write_text('{"kind":"native-state"}')
    (native_home / "config.toml").write_text('model = "gpt-5.5"\n')

    stale_sessions = managed_home / "sessions"
    stale_sessions.mkdir(parents=True)
    (stale_sessions / "old.jsonl").write_text("{}\n")

    env = _base_env(fake_bin, capture_path)
    env["HOME"] = str(tmp_path)
    env["CODEX_LITELLM_INHERIT_NATIVE_CONFIG"] = "1"
    env.pop("CODEX_LITELLM_HOME", None)
    env.pop("CODEX_LITELLM_LINK_NATIVE_STATE", None)

    subprocess.run(
        [str(REPO_ROOT / "bin/codex-litellm"), "exec", "health marker"],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )

    capture = json.loads(capture_path.read_text())
    assert capture["codex_home"] == str(managed_home)
    assert (managed_home / "config.toml").is_file()
    assert not (managed_home / "config.toml").is_symlink()
    assert (managed_home / "sessions").is_symlink()
    assert (managed_home / "sessions").resolve(strict=True) == native_sessions
    assert (managed_home / "auth.json").is_symlink()
    assert (managed_home / "auth.json").resolve(strict=True) == native_auth

    backups = list(managed_home.glob(".sessions.codex-headroom-local-backup.*"))
    assert len(backups) == 1
    assert (backups[0] / "old.jsonl").read_text() == "{}\n"


def test_codex_wrapper_env_overrides_inherited_native_model_settings(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_cli(fake_bin / "codex")
    capture_path = tmp_path / "capture.json"
    codex_home = tmp_path / "codex-home"
    native_home = tmp_path / ".codex"
    native_home.mkdir()
    (native_home / "config.toml").write_text(
        """
model = "gpt-5.5"
model_reasoning_effort = "xhigh"
model_verbosity = "medium"
""".lstrip()
    )

    env = _base_env(fake_bin, capture_path)
    env["HOME"] = str(tmp_path)
    env["CODEX_LITELLM_HOME"] = str(codex_home)
    env["CODEX_LITELLM_INHERIT_NATIVE_CONFIG"] = "1"
    env["CODEX_LITELLM_MODEL"] = "gpt-5.4-mini"
    env["CODEX_LITELLM_REASONING_EFFORT"] = "low"
    env["CODEX_LITELLM_MODEL_VERBOSITY"] = "high"

    subprocess.run(
        [str(REPO_ROOT / "bin/codex-litellm"), "exec", "health marker"],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )

    profile_config = tomllib.loads((codex_home / "litellm.config.toml").read_text())
    assert profile_config["model"] == "gpt-5.4-mini"
    assert profile_config["model_reasoning_effort"] == "low"
    assert profile_config["model_verbosity"] == "high"


def test_codex_wrapper_rejects_litellm_base_url_with_credentials(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_cli(fake_bin / "codex")
    capture_path = tmp_path / "capture.json"
    codex_home = tmp_path / "codex-home"

    env = _base_env(fake_bin, capture_path)
    env["CODEX_LITELLM_HOME"] = str(codex_home)
    env["CODEX_LITELLM_BASE_URL"] = "http://user:secret@127.0.0.1:4100"

    result = subprocess.run(
        [str(REPO_ROOT / "bin/codex-litellm"), "exec", "health marker"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "CODEX_LITELLM_BASE_URL" in result.stderr
    assert not capture_path.exists()
    assert not (codex_home / "config.toml").exists()


def test_codex_wrapper_rejects_analytics_url_with_credentials(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_cli(fake_bin / "codex")
    capture_path = tmp_path / "capture.json"
    codex_home = tmp_path / "codex-home"

    env = _base_env(fake_bin, capture_path)
    env["CODEX_LITELLM_HOME"] = str(codex_home)
    env["CODEX_LITELLM_ANALYTICS_URL"] = "http://user:secret@127.0.0.1:8110"

    result = subprocess.run(
        [str(REPO_ROOT / "bin/codex-litellm"), "exec", "health marker"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "CODEX_LITELLM_ANALYTICS_URL" in result.stderr
    assert not capture_path.exists()
    assert not (codex_home / "config.toml").exists()


def test_codex_wrapper_rejects_unsupported_reasoning_effort(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_cli(fake_bin / "codex")
    capture_path = tmp_path / "capture.json"
    codex_home = tmp_path / "codex-home"

    env = _base_env(fake_bin, capture_path)
    env["CODEX_LITELLM_HOME"] = str(codex_home)
    env["CODEX_LITELLM_REASONING_EFFORT"] = "extreme"

    result = subprocess.run(
        [str(REPO_ROOT / "bin/codex-litellm"), "exec", "health marker"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "CODEX_LITELLM_REASONING_EFFORT" in result.stderr
    assert not capture_path.exists()
    assert not (codex_home / "config.toml").exists()


def test_codex_wrapper_rejects_unsupported_model_verbosity(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_cli(fake_bin / "codex")
    capture_path = tmp_path / "capture.json"
    codex_home = tmp_path / "codex-home"

    env = _base_env(fake_bin, capture_path)
    env["CODEX_LITELLM_HOME"] = str(codex_home)
    env["CODEX_LITELLM_MODEL_VERBOSITY"] = "verbose"

    result = subprocess.run(
        [str(REPO_ROOT / "bin/codex-litellm"), "exec", "health marker"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "CODEX_LITELLM_MODEL_VERBOSITY" in result.stderr
    assert not capture_path.exists()
    assert not (codex_home / "config.toml").exists()


def test_codex_wrapper_defaults_project_header_from_cwd(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_cli(fake_bin / "codex")
    capture_path = tmp_path / "capture.json"
    codex_home = tmp_path / "codex-home"
    project_dir = tmp_path / "sample project"
    project_dir.mkdir()

    env = _base_env(fake_bin, capture_path)
    env["CODEX_LITELLM_HOME"] = str(codex_home)
    env.pop("CODEX_LITELLM_PROJECT", None)

    subprocess.run(
        [str(REPO_ROOT / "bin/codex-litellm"), "exec", "health marker"],
        check=True,
        cwd=project_dir,
        env=env,
    )

    capture = json.loads(capture_path.read_text())
    assert capture["codex_litellm_project"] == "sample project"


def test_codex_wrapper_refuses_native_codex_home(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_cli(fake_bin / "codex")
    capture_path = tmp_path / "capture.json"
    native_codex_home = tmp_path / ".codex"

    env = _base_env(fake_bin, capture_path)
    env["HOME"] = str(tmp_path)
    env["CODEX_LITELLM_HOME"] = str(native_codex_home)

    result = subprocess.run(
        [str(REPO_ROOT / "bin/codex-litellm"), "exec", "health marker"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "native Codex home" in result.stderr
    assert not capture_path.exists()
    assert not (native_codex_home / "config.toml").exists()


def test_codex_wrapper_refuses_profile_override_that_can_bypass_litellm(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_cli(fake_bin / "codex")
    capture_path = tmp_path / "capture.json"
    codex_home = tmp_path / "codex-home"

    env = _base_env(fake_bin, capture_path)
    env["CODEX_LITELLM_HOME"] = str(codex_home)

    result = subprocess.run(
        [
            str(REPO_ROOT / "bin/codex-litellm"),
            "--profile",
            "native",
            "exec",
            "health marker",
        ],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "refusing --profile native" in result.stderr
    assert not capture_path.exists()
    assert not (codex_home / "config.toml").exists()


def test_codex_wrapper_allows_explicit_generated_litellm_profile(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_cli(fake_bin / "codex")
    capture_path = tmp_path / "capture.json"
    codex_home = tmp_path / "codex-home"

    env = _base_env(fake_bin, capture_path)
    env["CODEX_LITELLM_HOME"] = str(codex_home)

    subprocess.run(
        [
            str(REPO_ROOT / "bin/codex-litellm"),
            "--profile",
            "litellm",
            "exec",
            "health marker",
        ],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )

    capture = json.loads(capture_path.read_text())
    assert capture["args"] == ["--profile", "litellm", "exec", "health marker"]
    profile_config = tomllib.loads((codex_home / "litellm.config.toml").read_text())
    assert profile_config["model_provider"] == "litellm"


def test_codex_wrapper_profile_override_requires_explicit_escape_hatch(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_cli(fake_bin / "codex")
    capture_path = tmp_path / "capture.json"
    codex_home = tmp_path / "codex-home"

    env = _base_env(fake_bin, capture_path)
    env["CODEX_LITELLM_HOME"] = str(codex_home)
    env["CODEX_LITELLM_ALLOW_PROFILE_OVERRIDE"] = "1"

    subprocess.run(
        [
            str(REPO_ROOT / "bin/codex-litellm"),
            "--profile=debug",
            "exec",
            "health marker",
        ],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )

    capture = json.loads(capture_path.read_text())
    assert capture["args"] == ["--profile=debug", "exec", "health marker"]


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
    assert capture["anthropic_base_url"] == "http://10.20.30.1:24040"
    assert capture["anthropic_auth_token_present"] is True
    assert capture["anthropic_api_key_present"] is False
    assert capture["claude_config_dir"] == str(state_dir)
    assert capture["home"] == str(state_dir)
    assert capture["anthropic_custom_headers"] == "\n".join(
        [
            "X-LLM-Proxy-Client: claude",
            "X-LLM-Proxy-Project: litellm-proxy-headroom",
        ]
    )
    assert capture["gateway_model_discovery"] == "1"
    assert capture["args"] == [
        "--setting-sources",
        "",
        "--mcp-config",
        str(state_dir / "mcp.json"),
        "--strict-mcp-config",
        "--allowedTools",
        "mcp__analytics__*",
        "--model",
        "sonnet",
        "--effort",
        "xhigh",
        "--print",
        "health marker",
    ]

    mcp_config = json.loads((state_dir / "mcp.json").read_text())
    assert mcp_config == {
        "mcpServers": {
            "analytics": {
                "type": "http",
                "url": "http://10.20.30.1:28010/mcp/",
            }
        }
    }
    managed_config = json.loads((state_dir / ".claude.json").read_text())
    assert managed_config == {
        "hasCompletedOnboarding": True,
        "litellmProxy": {
            "backend_model": "gpt-5.5",
            "effort": "xhigh",
            "model": "sonnet",
            "reasoning_effort": "xhigh",
        },
        "projects": {},
    }
    assert "sk-test-wrapper-key" not in (state_dir / "mcp.json").read_text()
    assert "apiKeyHelper" not in (state_dir / ".claude.json").read_text()


def test_claude_wrapper_persists_managed_preferences_and_links_native_state(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_cli(fake_bin / "claude")
    capture_path = tmp_path / "capture.json"
    state_dir = tmp_path / "claude-state"
    native_state = tmp_path / ".claude"
    native_state.mkdir()
    native_auth = native_state / ".credentials.json"
    native_auth.write_text('{"native":true}')
    native_sessions = native_state / "sessions"
    native_sessions.mkdir()
    (native_sessions / "session.jsonl").write_text("{}\n")
    (native_state / "cache").mkdir()
    (native_state / "cache" / "gateway-models.json").write_text(
        '{"baseUrl":"http://10.20.30.1:11435"}'
    )
    (native_state / "settings.json").write_text(
        '{"env":{"ANTHROPIC_BASE_URL":"http://10.20.30.1:11435"}}'
    )
    stale_managed_config = state_dir / ".claude.json"
    state_dir.mkdir()
    stale_managed_config.write_text('{"apiKeyHelper":"stale"}')
    stale_settings = state_dir / "settings.json"
    stale_settings.write_text('{"apiKeyHelper":"stale"}')
    stale_config_dir = state_dir / ".config" / "litellm-proxy"
    stale_config_dir.mkdir(parents=True)
    (stale_config_dir / "env").write_text("LITELLM_MASTER_KEY=stale\n")
    stale_helper_dir = state_dir / "bin"
    stale_helper_dir.mkdir()
    (stale_helper_dir / "get-litellm-master-key.sh").write_text("#!/bin/sh\n")
    stale_backup_dir = state_dir / "backups"
    stale_backup_dir.mkdir()
    (stale_backup_dir / "settings.json.bak").write_text('{"apiKeyHelper":"stale"}')

    env = _base_env(fake_bin, capture_path)
    env["HOME"] = str(tmp_path)
    env["CLAUDE_LITELLM_STATE_DIR"] = str(state_dir)
    env.pop("CLAUDE_LITELLM_LINK_NATIVE_STATE", None)

    subprocess.run(
        [
            str(REPO_ROOT / "bin/claude-litellm"),
            "--model",
            "gpt-5.4-mini",
            "--effort",
            "high",
            "--permission-mode",
            "dontAsk",
            "--print",
            "first",
        ],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )

    subprocess.run(
        [str(REPO_ROOT / "bin/claude-litellm"), "--print", "second"],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )

    capture = json.loads(capture_path.read_text())
    preferences = json.loads((state_dir / "litellm-preferences.json").read_text())

    assert capture["args"] == [
        "--setting-sources",
        "",
        "--mcp-config",
        str(state_dir / "mcp.json"),
        "--strict-mcp-config",
        "--allowedTools",
        "mcp__analytics__*",
        "--model",
        "gpt-5.4-mini",
        "--effort",
        "high",
        "--permission-mode",
        "dontAsk",
        "--print",
        "second",
    ]
    assert preferences == {
        "effort": "high",
        "model": "gpt-5.4-mini",
        "permission_mode": "dontAsk",
    }
    assert capture["claude_config_dir"] == str(state_dir)
    assert capture["home"] == str(state_dir)
    managed_config = json.loads(stale_managed_config.read_text())
    assert managed_config["litellmProxy"] == {
        "backend_model": "gpt-5.5",
        "effort": "high",
        "model": "gpt-5.4-mini",
        "reasoning_effort": "high",
    }
    assert "apiKeyHelper" not in stale_managed_config.read_text()
    assert not stale_settings.exists()
    assert not (state_dir / ".config").exists()
    assert not stale_helper_dir.exists()
    assert not stale_backup_dir.exists()
    assert (state_dir / ".credentials.json").is_symlink()
    assert (state_dir / ".credentials.json").resolve() == native_auth
    assert (state_dir / "sessions").is_symlink()
    assert (state_dir / "sessions").resolve() == native_sessions
    assert not (state_dir / "settings.json").exists()
    assert not (state_dir / "cache").exists()


def test_claude_wrapper_preserves_existing_custom_headers_and_adds_run_marker(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_cli(fake_bin / "claude")
    capture_path = tmp_path / "capture.json"
    state_dir = tmp_path / "claude-state"

    env = _base_env(fake_bin, capture_path)
    env["CLAUDE_LITELLM_STATE_DIR"] = str(state_dir)
    env["ANTHROPIC_CUSTOM_HEADERS"] = "x-existing: value"
    env["LITELLM_PROXY_RUN_MARKER"] = "CLAUDE-RUN-1"
    env["CLAUDE_LITELLM_CLIENT"] = "claude-smoke"
    env["CLAUDE_LITELLM_PROJECT"] = "project\none"
    env["CLAUDE_LITELLM_COMPRESSION_MODE"] = "FALSE"

    subprocess.run(
        [str(REPO_ROOT / "bin/claude-litellm"), "--print", "health marker"],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )

    capture = json.loads(capture_path.read_text())
    assert capture["anthropic_custom_headers"] == "\n".join(
        [
            "x-existing: value",
            "X-LLM-Proxy-Client: claude-smoke",
            "X-LLM-Proxy-Project: project one",
            "X-LLM-Proxy-Run: CLAUDE-RUN-1",
            "X-LLM-Proxy-Compression: off",
        ]
    )
    assert capture["claude_litellm_compression_mode"] == "off"


def test_claude_wrapper_can_disable_analytics_mcp(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_cli(fake_bin / "claude")
    capture_path = tmp_path / "capture.json"
    state_dir = tmp_path / "claude-state"

    env = _base_env(fake_bin, capture_path)
    env["CLAUDE_LITELLM_STATE_DIR"] = str(state_dir)
    env["CLAUDE_LITELLM_DISABLE_ANALYTICS_MCP"] = "true"
    env["CLAUDE_LITELLM_ANALYTICS_URL"] = "http://user:secret@127.0.0.1:8110"

    subprocess.run(
        [str(REPO_ROOT / "bin/claude-litellm"), "--print", "health marker"],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )

    capture = json.loads(capture_path.read_text())
    assert "--mcp-config" not in capture["args"]
    assert "--strict-mcp-config" not in capture["args"]
    assert "--allowedTools" not in capture["args"]
    assert not (state_dir / "mcp.json").exists()


def test_claude_wrapper_defaults_to_managed_home(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_cli(fake_bin / "claude")
    capture_path = tmp_path / "capture.json"
    managed_home = tmp_path / ".claude-headroom"

    env = _base_env(fake_bin, capture_path)
    env["HOME"] = str(tmp_path)
    env.pop("CLAUDE_LITELLM_HOME", None)
    env.pop("CLAUDE_LITELLM_STATE_DIR", None)

    subprocess.run(
        [str(REPO_ROOT / "bin/claude-litellm"), "--print", "health marker"],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )

    capture = json.loads(capture_path.read_text())
    assert capture["args"][2:4] == ["--mcp-config", str(managed_home / "mcp.json")]
    assert (managed_home / "mcp.json").is_file()
    assert not (tmp_path / ".claude").exists()


def test_claude_wrapper_rejects_litellm_base_url_with_credentials(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_cli(fake_bin / "claude")
    capture_path = tmp_path / "capture.json"
    managed_home = tmp_path / ".claude-headroom"

    env = _base_env(fake_bin, capture_path)
    env["HOME"] = str(tmp_path)
    env["CLAUDE_LITELLM_BASE_URL"] = "http://user:secret@10.20.30.1:24040"

    result = subprocess.run(
        [str(REPO_ROOT / "bin/claude-litellm"), "--print", "health marker"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "CLAUDE_LITELLM_BASE_URL" in result.stderr
    assert not capture_path.exists()
    assert not (managed_home / "mcp.json").exists()


def test_opencode_wrapper_generates_managed_config_and_env(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_cli(fake_bin / "opencode")
    capture_path = tmp_path / "capture.json"
    managed_home = tmp_path / "opencode-home"

    env = _base_env(fake_bin, capture_path)
    env["HOME"] = str(tmp_path)
    env["OPENCODE_LITELLM_HOME"] = str(managed_home)
    env["OPENCODE_LITELLM_MODEL"] = "gpt-5.5"
    env["OPENCODE_LITELLM_SMALL_MODEL"] = "gpt-5.4-mini"
    env["OPENCODE_LITELLM_COMPRESSION_MODE"] = "0"

    subprocess.run(
        [str(REPO_ROOT / "bin/opencode-litellm"), "run", "health marker"],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )

    capture = json.loads(capture_path.read_text())
    config_path = managed_home / "opencode.json"
    assert capture["args"] == [
        "--model",
        "litellm/gpt-5.5",
        "run",
        "--variant",
        "xhigh",
        "health marker",
    ]
    assert capture["opencode_config"] == str(config_path)
    assert capture["opencode_config_dir"] == str(managed_home / "config-dir")
    assert capture["xdg_config_home"] == str(managed_home / "xdg-config")
    assert capture["xdg_data_home"] == str(managed_home / "xdg-data")
    assert capture["xdg_cache_home"] == str(managed_home / "xdg-cache")
    assert capture["opencode_litellm_client"] == "opencode"
    assert capture["opencode_litellm_project"] == "litellm-proxy-headroom"
    assert capture["opencode_litellm_compression_mode"] == "off"
    assert not (tmp_path / ".config" / "opencode").exists()
    assert not (tmp_path / ".local" / "share" / "opencode").exists()

    config = json.loads(config_path.read_text())
    provider = config["provider"]["litellm"]
    assert config["enabled_providers"] == ["litellm"]
    assert config["model"] == "litellm/gpt-5.5"
    assert config["small_model"] == "litellm/gpt-5.4-mini"
    assert provider["npm"] == "@ai-sdk/openai-compatible"
    assert provider["options"]["baseURL"] == "http://10.20.30.1:24040/v1"
    assert provider["options"]["apiKey"] == "{env:LITELLM_MASTER_KEY}"
    assert provider["options"]["headers"] == {
        "X-LLM-Proxy-Client": "{env:OPENCODE_LITELLM_CLIENT}",
        "X-LLM-Proxy-Project": "{env:OPENCODE_LITELLM_PROJECT}",
        "X-LLM-Proxy-Run": "{env:LITELLM_PROXY_RUN_MARKER}",
        "X-LLM-Proxy-Compression": "{env:OPENCODE_LITELLM_COMPRESSION_MODE}",
    }
    assert config["mcp"]["analytics"] == {
        "type": "remote",
        "url": "http://10.20.30.1:28010/mcp/",
        "enabled": True,
        "oauth": False,
    }
    assert "sk-test-wrapper-key" not in config_path.read_text()


def test_opencode_wrapper_persists_managed_model_preferences(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_cli(fake_bin / "opencode")
    capture_path = tmp_path / "capture.json"
    managed_home = tmp_path / "opencode-home"

    env = _base_env(fake_bin, capture_path)
    env["OPENCODE_LITELLM_HOME"] = str(managed_home)

    subprocess.run(
        [
            str(REPO_ROOT / "bin/opencode-litellm"),
            "--model",
            "litellm/gpt-5.4-mini",
            "run",
            "first",
        ],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )

    subprocess.run(
        [str(REPO_ROOT / "bin/opencode-litellm"), "run", "second"],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )

    capture = json.loads(capture_path.read_text())
    config = json.loads((managed_home / "opencode.json").read_text())
    preferences = json.loads((managed_home / "litellm-preferences.json").read_text())

    assert capture["args"] == [
        "--model",
        "litellm/gpt-5.4-mini",
        "run",
        "--variant",
        "xhigh",
        "second",
    ]
    assert config["model"] == "litellm/gpt-5.4-mini"
    assert preferences["model"] == "gpt-5.4-mini"
    assert preferences["variant"] == "xhigh"


def test_opencode_wrapper_respects_existing_model_argument(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_cli(fake_bin / "opencode")
    capture_path = tmp_path / "capture.json"
    managed_home = tmp_path / "opencode-home"

    env = _base_env(fake_bin, capture_path)
    env["OPENCODE_LITELLM_HOME"] = str(managed_home)

    subprocess.run(
        [
            str(REPO_ROOT / "bin/opencode-litellm"),
            "--model",
            "litellm/gpt-5.4-mini",
            "run",
            "health marker",
        ],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )

    capture = json.loads(capture_path.read_text())
    assert capture["args"] == [
        "--model",
        "litellm/gpt-5.4-mini",
        "run",
        "--variant",
        "xhigh",
        "health marker",
    ]

    config = json.loads((managed_home / "opencode.json").read_text())
    assert (
        "X-LLM-Proxy-Compression"
        not in config["provider"]["litellm"]["options"]["headers"]
    )


def test_opencode_wrapper_can_disable_analytics_mcp(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_cli(fake_bin / "opencode")
    capture_path = tmp_path / "capture.json"
    managed_home = tmp_path / "opencode-home"

    env = _base_env(fake_bin, capture_path)
    env["OPENCODE_LITELLM_HOME"] = str(managed_home)
    env["OPENCODE_LITELLM_DISABLE_ANALYTICS_MCP"] = "yes"
    env["OPENCODE_LITELLM_ANALYTICS_URL"] = "http://user:secret@127.0.0.1:8110"

    subprocess.run(
        [str(REPO_ROOT / "bin/opencode-litellm"), "run", "health marker"],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )

    config = json.loads((managed_home / "opencode.json").read_text())

    assert "mcp" not in config


def test_opencode_wrapper_does_not_add_model_to_management_commands(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_cli(fake_bin / "opencode")
    capture_path = tmp_path / "capture.json"
    managed_home = tmp_path / "opencode-home"

    env = _base_env(fake_bin, capture_path)
    env["OPENCODE_LITELLM_HOME"] = str(managed_home)

    subprocess.run(
        [str(REPO_ROOT / "bin/opencode-litellm"), "models", "litellm"],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )

    capture = json.loads(capture_path.read_text())
    assert capture["args"] == ["models", "litellm"]


def test_opencode_wrapper_rejects_litellm_base_url_with_credentials(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_cli(fake_bin / "opencode")
    capture_path = tmp_path / "capture.json"
    managed_home = tmp_path / "opencode-home"

    env = _base_env(fake_bin, capture_path)
    env["OPENCODE_LITELLM_HOME"] = str(managed_home)
    env["OPENCODE_LITELLM_BASE_URL"] = "http://user:secret@10.20.30.1:24040"

    result = subprocess.run(
        [str(REPO_ROOT / "bin/opencode-litellm"), "run", "health marker"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "OPENCODE_LITELLM_BASE_URL" in result.stderr
    assert not capture_path.exists()
    assert not (managed_home / "opencode.json").exists()


def test_pi_wrapper_generates_managed_models_config_and_env(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_cli(fake_bin / "pi")
    capture_path = tmp_path / "capture.json"
    managed_home = tmp_path / "pi-home"

    env = _base_env(fake_bin, capture_path)
    env["HOME"] = str(tmp_path)
    env["PI_LITELLM_HOME"] = str(managed_home)
    env["PI_LITELLM_MODEL"] = "gpt-5.5"
    env["PI_LITELLM_SMALL_MODEL"] = "gpt-5.4-mini"
    env["PI_LITELLM_COMPRESSION_MODE"] = "no"

    subprocess.run(
        [str(REPO_ROOT / "bin/pi-litellm"), "-p", "health marker"],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )

    capture = json.loads(capture_path.read_text())
    models_path = managed_home / "models.json"
    assert capture["args"] == [
        "--provider",
        "litellm",
        "--model",
        "gpt-5.5",
        "--thinking",
        "xhigh",
        "-p",
        "health marker",
    ]
    assert capture["pi_coding_agent_dir"] == str(managed_home)
    assert capture["pi_litellm_client"] == "pi"
    assert capture["pi_litellm_project"] == "litellm-proxy-headroom"
    assert capture["pi_litellm_compression_mode"] == "off"
    assert not (tmp_path / ".pi" / "agent").exists()

    config = json.loads(models_path.read_text())
    provider = config["providers"]["litellm"]
    assert provider["baseUrl"] == "http://10.20.30.1:24040/v1"
    assert provider["api"] == "openai-responses"
    assert provider["apiKey"] == "$LITELLM_MASTER_KEY"
    assert provider["models"] == [
        {
            "id": "gpt-5.5",
            "name": "gpt-5.5",
            "reasoning": True,
            "input": ["text"],
            "contextWindow": 400000,
            "maxTokens": 128000,
            "compat": {"sendSessionIdHeader": False},
        },
        {
            "id": "gpt-5.4-mini",
            "name": "gpt-5.4-mini",
            "reasoning": True,
            "input": ["text"],
            "contextWindow": 400000,
            "maxTokens": 128000,
            "compat": {"sendSessionIdHeader": False},
        },
    ]
    assert provider["headers"] == {
        "X-LLM-Proxy-Client": "$PI_LITELLM_CLIENT",
        "X-LLM-Proxy-Project": "$PI_LITELLM_PROJECT",
        "X-LLM-Proxy-Run": "$LITELLM_PROXY_RUN_MARKER",
        "X-LLM-Proxy-Compression": "$PI_LITELLM_COMPRESSION_MODE",
    }
    assert "sk-test-wrapper-key" not in models_path.read_text()


def test_pi_wrapper_persists_managed_model_preferences(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_cli(fake_bin / "pi")
    capture_path = tmp_path / "capture.json"
    managed_home = tmp_path / "pi-home"

    env = _base_env(fake_bin, capture_path)
    env["PI_LITELLM_HOME"] = str(managed_home)

    subprocess.run(
        [
            str(REPO_ROOT / "bin/pi-litellm"),
            "--model",
            "litellm/gpt-5.4-mini",
            "-p",
            "first",
        ],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )

    subprocess.run(
        [str(REPO_ROOT / "bin/pi-litellm"), "-p", "second"],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )

    capture = json.loads(capture_path.read_text())
    config = json.loads((managed_home / "models.json").read_text())
    preferences = json.loads((managed_home / "litellm-preferences.json").read_text())

    assert capture["args"] == [
        "--provider",
        "litellm",
        "--model",
        "gpt-5.4-mini",
        "--thinking",
        "xhigh",
        "-p",
        "second",
    ]
    assert config["providers"]["litellm"]["models"][0]["id"] == "gpt-5.4-mini"
    assert preferences["model"] == "gpt-5.4-mini"
    assert preferences["thinking"] == "xhigh"


def test_pi_wrapper_respects_existing_model_argument(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_cli(fake_bin / "pi")
    capture_path = tmp_path / "capture.json"
    managed_home = tmp_path / "pi-home"

    env = _base_env(fake_bin, capture_path)
    env["PI_LITELLM_HOME"] = str(managed_home)

    subprocess.run(
        [
            str(REPO_ROOT / "bin/pi-litellm"),
            "--model",
            "litellm/gpt-5.4-mini",
            "-p",
            "health marker",
        ],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )

    capture = json.loads(capture_path.read_text())
    assert capture["args"] == [
        "--thinking",
        "xhigh",
        "--model",
        "litellm/gpt-5.4-mini",
        "-p",
        "health marker",
    ]

    config = json.loads((managed_home / "models.json").read_text())
    assert "X-LLM-Proxy-Compression" not in config["providers"]["litellm"]["headers"]


def test_pi_wrapper_does_not_add_model_to_management_commands(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_cli(fake_bin / "pi")
    capture_path = tmp_path / "capture.json"
    managed_home = tmp_path / "pi-home"

    env = _base_env(fake_bin, capture_path)
    env["PI_LITELLM_HOME"] = str(managed_home)

    subprocess.run(
        [str(REPO_ROOT / "bin/pi-litellm"), "--version"],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )

    capture = json.loads(capture_path.read_text())
    assert capture["args"] == ["--version"]


def test_pi_wrapper_rejects_litellm_base_url_with_credentials(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_cli(fake_bin / "pi")
    capture_path = tmp_path / "capture.json"
    managed_home = tmp_path / "pi-home"

    env = _base_env(fake_bin, capture_path)
    env["PI_LITELLM_HOME"] = str(managed_home)
    env["PI_LITELLM_BASE_URL"] = "http://user:secret@10.20.30.1:24040"

    result = subprocess.run(
        [str(REPO_ROOT / "bin/pi-litellm"), "-p", "health marker"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "PI_LITELLM_BASE_URL" in result.stderr
    assert not capture_path.exists()
    assert not (managed_home / "models.json").exists()


def test_copilot_wrapper_defaults_to_managed_home(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_cli(fake_bin / "copilot")
    capture_path = tmp_path / "capture.json"
    managed_home = tmp_path / ".copilot-headroom"

    env = _base_env(fake_bin, capture_path)
    env["HOME"] = str(tmp_path)
    env.pop("COPILOT_LITELLM_HOME", None)

    subprocess.run(
        [str(REPO_ROOT / "bin/copilot-litellm"), "--version"],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )

    capture = json.loads(capture_path.read_text())
    assert capture["args"] == ["--version"]
    assert capture["copilot_home"] == str(managed_home)
    assert capture["copilot_auto_update"] == "false"
    assert capture["copilot_model"] == "gpt-4.1"
    assert capture["copilot_provider_base_url"] == "http://10.20.30.1:24040/v1"
    assert capture["copilot_provider_type"] == "openai"
    assert capture["copilot_provider_api_key_present"] is False
    assert capture["copilot_provider_bearer_token_present"] is True
    assert capture["copilot_provider_wire_api"] == "responses"
    assert capture["copilot_provider_transport"] == "http"
    assert capture["copilot_provider_model_id"] == "gpt-5.5"
    assert capture["copilot_provider_wire_model"] == "gpt-5.5"
    assert capture["copilot_provider_reasoning_effort"] == "xhigh"
    assert capture["copilot_provider_max_prompt_tokens"] is None
    assert capture["copilot_provider_max_output_tokens"] is None
    assert json.loads((managed_home / "config.json").read_text()) == {
        "model": "gpt-4.1",
        "reasoning_effort": "xhigh",
    }
    assert managed_home.is_dir()
    assert not (tmp_path / ".copilot").exists()


def test_copilot_wrapper_respects_explicit_config_dir(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_cli(fake_bin / "copilot")
    capture_path = tmp_path / "capture.json"
    config_dir = tmp_path / "custom-copilot"

    env = _base_env(fake_bin, capture_path)
    env["HOME"] = str(tmp_path)

    subprocess.run(
        [
            str(REPO_ROOT / "bin/copilot-litellm"),
            "--config-dir",
            str(config_dir),
            "--version",
        ],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )

    capture = json.loads(capture_path.read_text())
    assert capture["args"] == ["--config-dir", str(config_dir), "--version"]
    assert capture["copilot_home"] == str(config_dir)
    assert capture["copilot_provider_base_url"] == "http://10.20.30.1:24040/v1"
    assert config_dir.is_dir()


def test_copilot_wrapper_uses_configured_litellm_provider(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_cli(fake_bin / "copilot")
    capture_path = tmp_path / "capture.json"
    managed_home = tmp_path / ".copilot-headroom"

    env = _base_env(fake_bin, capture_path)
    env["HOME"] = str(tmp_path)
    env["COPILOT_LITELLM_HOME"] = str(managed_home)
    env["COPILOT_LITELLM_BASE_URL"] = "http://127.0.0.1:4100"
    env["COPILOT_LITELLM_CLI_MODEL"] = "gpt-4.1"
    env["COPILOT_LITELLM_MODEL"] = "gpt-5.4-mini"
    env["COPILOT_LITELLM_PROVIDER_MODEL_ID"] = "gpt-5.4"
    env["COPILOT_LITELLM_WIRE_MODEL"] = "gpt-5.4-mini"
    env["COPILOT_LITELLM_WIRE_API"] = "responses"
    env["COPILOT_LITELLM_MAX_PROMPT_TOKENS"] = "180000"
    env["COPILOT_LITELLM_MAX_OUTPUT_TOKENS"] = "64000"
    env["COPILOT_LITELLM_REASONING_EFFORT"] = "high"
    env["COPILOT_LITELLM_CLIENT"] = "copilot-smoke"
    env["COPILOT_LITELLM_PROJECT"] = "project\none"
    env["COPILOT_LITELLM_COMPRESSION_MODE"] = "disabled"
    env["COPILOT_AGENT_REQUEST_HEADERS"] = json.dumps({"x-existing": "value"})
    env["LITELLM_PROXY_RUN_MARKER"] = "COPILOT-RUN-1"

    subprocess.run(
        [str(REPO_ROOT / "bin/copilot-litellm"), "--version"],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )

    capture = json.loads(capture_path.read_text())
    assert capture["copilot_home"] == str(managed_home)
    assert capture["copilot_provider_base_url"] == "http://127.0.0.1:4100/v1"
    assert capture["copilot_model"] == "gpt-4.1"
    assert capture["copilot_provider_model_id"] == "gpt-5.4"
    assert capture["copilot_provider_wire_model"] == "gpt-5.4-mini"
    assert capture["copilot_provider_reasoning_effort"] == "high"
    assert capture["copilot_provider_wire_api"] == "responses"
    assert capture["copilot_provider_max_prompt_tokens"] == "180000"
    assert capture["copilot_provider_max_output_tokens"] == "64000"
    assert capture["copilot_litellm_compression_mode"] == "off"
    assert json.loads(capture["copilot_agent_request_headers"]) == {
        "X-LLM-Proxy-Client": "copilot-smoke",
        "X-LLM-Proxy-Compression": "off",
        "X-LLM-Proxy-Project": "project one",
        "X-LLM-Proxy-Run": "COPILOT-RUN-1",
        "x-existing": "value",
    }


def test_copilot_wrapper_persists_managed_provider_preferences(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_cli(fake_bin / "copilot")
    capture_path = tmp_path / "capture.json"
    managed_home = tmp_path / ".copilot-headroom"

    env = _base_env(fake_bin, capture_path)
    env["HOME"] = str(tmp_path)
    env["COPILOT_LITELLM_HOME"] = str(managed_home)
    env["COPILOT_LITELLM_MODEL"] = "gpt-5.4-mini"
    env["COPILOT_LITELLM_PROVIDER_MODEL_ID"] = "gpt-5.4"
    env["COPILOT_LITELLM_WIRE_MODEL"] = "gpt-5.4-mini"
    env["COPILOT_LITELLM_MAX_PROMPT_TOKENS"] = "180000"
    env["COPILOT_LITELLM_REASONING_EFFORT"] = "high"

    subprocess.run(
        [str(REPO_ROOT / "bin/copilot-litellm"), "-p", "first"],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )

    env.pop("COPILOT_LITELLM_MODEL")
    env.pop("COPILOT_LITELLM_PROVIDER_MODEL_ID")
    env.pop("COPILOT_LITELLM_WIRE_MODEL")
    env.pop("COPILOT_LITELLM_MAX_PROMPT_TOKENS")
    env.pop("COPILOT_LITELLM_REASONING_EFFORT")
    subprocess.run(
        [str(REPO_ROOT / "bin/copilot-litellm"), "-p", "second"],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )

    capture = json.loads(capture_path.read_text())
    preferences = json.loads((managed_home / "litellm-preferences.json").read_text())

    assert capture["args"] == ["--model", "gpt-4.1", "--yolo", "-p", "second"]
    assert capture["copilot_provider_model_id"] == "gpt-5.4"
    assert capture["copilot_provider_wire_model"] == "gpt-5.4-mini"
    assert capture["copilot_provider_max_prompt_tokens"] == "180000"
    assert capture["copilot_provider_reasoning_effort"] == "high"
    assert preferences["wire_model"] == "gpt-5.4-mini"
    assert preferences["provider_model_id"] == "gpt-5.4"
    assert preferences["reasoning_effort"] == "high"


def test_copilot_wrapper_owns_provider_env_over_shell_defaults(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_cli(fake_bin / "copilot")
    capture_path = tmp_path / "capture.json"

    env = _base_env(fake_bin, capture_path)
    env["HOME"] = str(tmp_path)
    env["COPILOT_MODEL"] = "hosted-model"
    env["COPILOT_PROVIDER_TYPE"] = "anthropic"
    env["COPILOT_PROVIDER_BASE_URL"] = "https://example.invalid"
    env["COPILOT_PROVIDER_WIRE_API"] = "completions"
    env["COPILOT_PROVIDER_MODEL_ID"] = "hosted-model"
    env["COPILOT_PROVIDER_WIRE_MODEL"] = "hosted-model"

    subprocess.run(
        [str(REPO_ROOT / "bin/copilot-litellm"), "--version"],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )

    capture = json.loads(capture_path.read_text())
    assert capture["copilot_model"] == "gpt-4.1"
    assert capture["copilot_provider_type"] == "openai"
    assert capture["copilot_provider_base_url"] == "http://10.20.30.1:24040/v1"
    assert capture["copilot_provider_wire_api"] == "responses"
    assert capture["copilot_provider_model_id"] == "gpt-5.5"
    assert capture["copilot_provider_wire_model"] == "gpt-5.5"
    assert capture["copilot_provider_reasoning_effort"] == "xhigh"


def test_copilot_wrapper_adds_cli_model_for_provider_calls(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_cli(fake_bin / "copilot")
    capture_path = tmp_path / "capture.json"

    env = _base_env(fake_bin, capture_path)
    env["HOME"] = str(tmp_path)
    env["COPILOT_LITELLM_MODEL"] = "gpt-5.4-mini"

    subprocess.run(
        [str(REPO_ROOT / "bin/copilot-litellm"), "-p", "health marker"],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )

    capture = json.loads(capture_path.read_text())
    assert capture["args"] == ["--model", "gpt-4.1", "--yolo", "-p", "health marker"]
    assert capture["copilot_model"] == "gpt-4.1"
    assert capture["copilot_provider_model_id"] == "gpt-5.4-mini"
    assert capture["copilot_provider_wire_model"] == "gpt-5.4-mini"


def test_copilot_wrapper_preserves_explicit_cli_model(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_cli(fake_bin / "copilot")
    capture_path = tmp_path / "capture.json"

    env = _base_env(fake_bin, capture_path)
    env["HOME"] = str(tmp_path)

    subprocess.run(
        [
            str(REPO_ROOT / "bin/copilot-litellm"),
            "--model",
            "gpt-5-mini",
            "-p",
            "health marker",
        ],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )

    capture = json.loads(capture_path.read_text())
    assert capture["args"] == ["--yolo", "--model", "gpt-5-mini", "-p", "health marker"]


def test_copilot_wrapper_does_not_duplicate_yolo_arg(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_cli(fake_bin / "copilot")
    capture_path = tmp_path / "capture.json"

    env = _base_env(fake_bin, capture_path)
    env["HOME"] = str(tmp_path)

    subprocess.run(
        [
            str(REPO_ROOT / "bin/copilot-litellm"),
            "--yolo",
            "-p",
            "health marker",
        ],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )

    capture = json.loads(capture_path.read_text())
    assert capture["args"] == ["--model", "gpt-4.1", "--yolo", "-p", "health marker"]


def test_copilot_wrapper_rejects_litellm_base_url_with_credentials(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_cli(fake_bin / "copilot")
    capture_path = tmp_path / "capture.json"

    env = _base_env(fake_bin, capture_path)
    env["HOME"] = str(tmp_path)
    env["COPILOT_LITELLM_BASE_URL"] = "http://user:secret@10.20.30.1:24040"

    result = subprocess.run(
        [str(REPO_ROOT / "bin/copilot-litellm"), "--version"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "COPILOT_LITELLM_BASE_URL" in result.stderr
    assert not capture_path.exists()


def test_copilot_wrapper_rejects_native_config_dir_by_default(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_cli(fake_bin / "copilot")
    capture_path = tmp_path / "capture.json"
    native_home = tmp_path / ".copilot"

    env = _base_env(fake_bin, capture_path)
    env["HOME"] = str(tmp_path)

    result = subprocess.run(
        [
            str(REPO_ROOT / "bin/copilot-litellm"),
            "--config-dir",
            str(native_home),
            "--version",
        ],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "refusing to use native ~/.copilot" in result.stderr
    assert not capture_path.exists()
    assert not native_home.exists()


def test_copilot_wrapper_rejects_invalid_token_limits(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_cli(fake_bin / "copilot")
    capture_path = tmp_path / "capture.json"

    env = _base_env(fake_bin, capture_path)
    env["HOME"] = str(tmp_path)
    env["COPILOT_LITELLM_MAX_PROMPT_TOKENS"] = "0"

    result = subprocess.run(
        [str(REPO_ROOT / "bin/copilot-litellm"), "--version"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "COPILOT_LITELLM_MAX_PROMPT_TOKENS must be a positive integer" in (
        result.stderr
    )
    assert not capture_path.exists()
