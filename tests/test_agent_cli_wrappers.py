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
    "codex_litellm_client": os.environ.get("CODEX_LITELLM_CLIENT"),
    "codex_litellm_project": os.environ.get("CODEX_LITELLM_PROJECT"),
    "codex_litellm_reasoning_effort": os.environ.get("CODEX_LITELLM_REASONING_EFFORT"),
    "codex_litellm_model_verbosity": os.environ.get("CODEX_LITELLM_MODEL_VERBOSITY"),
    "openai_api_key_present": bool(os.environ.get("OPENAI_API_KEY")),
    "openai_base_url": os.environ.get("OPENAI_BASE_URL"),
    "anthropic_base_url": os.environ.get("ANTHROPIC_BASE_URL"),
    "anthropic_custom_headers": os.environ.get("ANTHROPIC_CUSTOM_HEADERS"),
    "anthropic_auth_token_present": bool(os.environ.get("ANTHROPIC_AUTH_TOKEN")),
    "anthropic_api_key_present": bool(os.environ.get("ANTHROPIC_API_KEY")),
    "opencode_config": os.environ.get("OPENCODE_CONFIG"),
    "opencode_config_dir": os.environ.get("OPENCODE_CONFIG_DIR"),
    "opencode_litellm_client": os.environ.get("OPENCODE_LITELLM_CLIENT"),
    "opencode_litellm_project": os.environ.get("OPENCODE_LITELLM_PROJECT"),
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
    "pi_coding_agent_dir": os.environ.get("PI_CODING_AGENT_DIR"),
    "pi_litellm_client": os.environ.get("PI_LITELLM_CLIENT"),
    "pi_litellm_project": os.environ.get("PI_LITELLM_PROJECT"),
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
    env.update(
        {
            "PATH": f"{fake_bin}{os.pathsep}{env.get('PATH', '')}",
            "FAKE_CLI_CAPTURE": str(capture_path),
            "CODEX_LITELLM_INHERIT_NATIVE_CONFIG": "0",
            "CODEX_LITELLM_LINK_NATIVE_STATE": "0",
            "LITELLM_MASTER_KEY": "sk-test-wrapper-key",
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
    assert capture["openai_api_key_present"] is True
    assert capture["openai_base_url"] == "http://127.0.0.1:4000/v1"

    base_config = tomllib.loads((codex_home / "config.toml").read_text())
    profile_config = tomllib.loads((codex_home / "litellm.config.toml").read_text())

    assert base_config["mcp_servers"]["analytics"]["url"] == (
        "http://127.0.0.1:8010/mcp/"
    )
    assert set(base_config["mcp_servers"]) == {"analytics"}
    assert "headroom" not in base_config["mcp_servers"]
    assert profile_config["model"] == "gpt-5.4"
    assert profile_config["model_reasoning_effort"] == "high"
    assert profile_config["model_verbosity"] == "low"
    assert profile_config["model_provider"] == "litellm"
    assert profile_config["openai_base_url"] == "http://127.0.0.1:4000/v1"
    provider = profile_config["model_providers"]["litellm"]
    assert provider == {
        "name": "Local LiteLLM",
        "base_url": "http://127.0.0.1:4000/v1",
        "env_key": "OPENAI_API_KEY",
        "wire_api": "responses",
        "env_http_headers": {
            "X-LiteLLM-Proxy-Client": "CODEX_LITELLM_CLIENT",
            "X-LiteLLM-Proxy-Project": "CODEX_LITELLM_PROJECT",
            "X-LiteLLM-Proxy-Run": "LITELLM_PROXY_RUN_MARKER",
        },
    }
    assert "supports_websockets" not in provider
    assert "sk-test-wrapper-key" not in (codex_home / "config.toml").read_text()
    assert "sk-test-wrapper-key" not in (codex_home / "litellm.config.toml").read_text()


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
    assert profile_config["openai_base_url"] == "http://127.0.0.1:4000/v1"
    assert profile_config["model_providers"]["litellm"]["base_url"] == (
        "http://127.0.0.1:4000/v1"
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
    assert "supports_websockets = true" not in profile_config_text
    assert "requires_openai_auth = true" not in profile_config_text
    assert 'api_key = "must-not-copy"' not in base_config_text
    assert "must-not-copy" not in profile_config_text


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
    assert capture["anthropic_base_url"] == "http://127.0.0.1:4000"
    assert capture["anthropic_auth_token_present"] is True
    assert capture["anthropic_api_key_present"] is True
    assert capture["anthropic_custom_headers"] == "\n".join(
        [
            "X-LiteLLM-Proxy-Client: claude",
            "X-LiteLLM-Proxy-Project: litellm-proxy-headroom",
        ]
    )
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
        "gpt-5.5",
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
            "X-LiteLLM-Proxy-Client: claude-smoke",
            "X-LiteLLM-Proxy-Project: project one",
            "X-LiteLLM-Proxy-Run: CLAUDE-RUN-1",
        ]
    )


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
    env["CLAUDE_LITELLM_BASE_URL"] = "http://user:secret@127.0.0.1:4000"

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
        "health marker",
    ]
    assert capture["opencode_config"] == str(config_path)
    assert capture["opencode_config_dir"] == str(managed_home / "config-dir")
    assert capture["xdg_config_home"] == str(managed_home / "xdg-config")
    assert capture["xdg_data_home"] == str(managed_home / "xdg-data")
    assert capture["xdg_cache_home"] == str(managed_home / "xdg-cache")
    assert capture["opencode_litellm_client"] == "opencode"
    assert capture["opencode_litellm_project"] == "litellm-proxy-headroom"
    assert not (tmp_path / ".config" / "opencode").exists()
    assert not (tmp_path / ".local" / "share" / "opencode").exists()

    config = json.loads(config_path.read_text())
    provider = config["provider"]["litellm"]
    assert config["enabled_providers"] == ["litellm"]
    assert config["model"] == "litellm/gpt-5.5"
    assert config["small_model"] == "litellm/gpt-5.4-mini"
    assert provider["npm"] == "@ai-sdk/openai-compatible"
    assert provider["options"]["baseURL"] == "http://127.0.0.1:4000/v1"
    assert provider["options"]["apiKey"] == "{env:LITELLM_MASTER_KEY}"
    assert provider["options"]["headers"] == {
        "X-LiteLLM-Proxy-Client": "{env:OPENCODE_LITELLM_CLIENT}",
        "X-LiteLLM-Proxy-Project": "{env:OPENCODE_LITELLM_PROJECT}",
        "X-LiteLLM-Proxy-Run": "{env:LITELLM_PROXY_RUN_MARKER}",
    }
    assert config["mcp"]["analytics"] == {
        "type": "remote",
        "url": "http://127.0.0.1:8010/mcp/",
        "enabled": True,
        "oauth": False,
    }
    assert "sk-test-wrapper-key" not in config_path.read_text()


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
        "health marker",
    ]


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
    env["OPENCODE_LITELLM_BASE_URL"] = "http://user:secret@127.0.0.1:4000"

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
        "-p",
        "health marker",
    ]
    assert capture["pi_coding_agent_dir"] == str(managed_home)
    assert capture["pi_litellm_client"] == "pi"
    assert capture["pi_litellm_project"] == "litellm-proxy-headroom"
    assert not (tmp_path / ".pi" / "agent").exists()

    config = json.loads(models_path.read_text())
    provider = config["providers"]["litellm"]
    assert provider["baseUrl"] == "http://127.0.0.1:4000/v1"
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
        "X-LiteLLM-Proxy-Client": "$PI_LITELLM_CLIENT",
        "X-LiteLLM-Proxy-Project": "$PI_LITELLM_PROJECT",
        "X-LiteLLM-Proxy-Run": "$LITELLM_PROXY_RUN_MARKER",
    }
    assert "sk-test-wrapper-key" not in models_path.read_text()


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
        "--model",
        "litellm/gpt-5.4-mini",
        "-p",
        "health marker",
    ]


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
    env["PI_LITELLM_BASE_URL"] = "http://user:secret@127.0.0.1:4000"

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
    assert capture["copilot_model"] == "gpt-5.5"
    assert capture["copilot_provider_base_url"] == "http://127.0.0.1:4000/v1"
    assert capture["copilot_provider_type"] == "openai"
    assert capture["copilot_provider_api_key_present"] is False
    assert capture["copilot_provider_bearer_token_present"] is True
    assert capture["copilot_provider_wire_api"] == "responses"
    assert capture["copilot_provider_transport"] == "http"
    assert capture["copilot_provider_model_id"] == "gpt-5.5"
    assert capture["copilot_provider_wire_model"] == "gpt-5.5"
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
    assert capture["copilot_provider_base_url"] == "http://127.0.0.1:4000/v1"
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
    env["COPILOT_LITELLM_MODEL"] = "gpt-5.4-mini"
    env["COPILOT_LITELLM_PROVIDER_MODEL_ID"] = "gpt-5.4"
    env["COPILOT_LITELLM_WIRE_MODEL"] = "gpt-5.4-mini"
    env["COPILOT_LITELLM_WIRE_API"] = "responses"

    subprocess.run(
        [str(REPO_ROOT / "bin/copilot-litellm"), "--version"],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )

    capture = json.loads(capture_path.read_text())
    assert capture["copilot_home"] == str(managed_home)
    assert capture["copilot_provider_base_url"] == "http://127.0.0.1:4100/v1"
    assert capture["copilot_model"] == "gpt-5.4-mini"
    assert capture["copilot_provider_model_id"] == "gpt-5.4"
    assert capture["copilot_provider_wire_model"] == "gpt-5.4-mini"
    assert capture["copilot_provider_wire_api"] == "responses"


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
    assert capture["copilot_model"] == "gpt-5.5"
    assert capture["copilot_provider_type"] == "openai"
    assert capture["copilot_provider_base_url"] == "http://127.0.0.1:4000/v1"
    assert capture["copilot_provider_wire_api"] == "responses"
    assert capture["copilot_provider_model_id"] == "gpt-5.5"
    assert capture["copilot_provider_wire_model"] == "gpt-5.5"


def test_copilot_wrapper_rejects_litellm_base_url_with_credentials(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_cli(fake_bin / "copilot")
    capture_path = tmp_path / "capture.json"

    env = _base_env(fake_bin, capture_path)
    env["HOME"] = str(tmp_path)
    env["COPILOT_LITELLM_BASE_URL"] = "http://user:secret@127.0.0.1:4000"

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
