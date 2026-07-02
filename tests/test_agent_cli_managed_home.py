import os
import tomllib
from pathlib import Path

import pytest

from litellm_proxy_headroom.agent_cli.managed_home import (
    compression_mode_header_value,
    find_real_executable,
    normalize_base_url,
    render_toml,
    sync_native_state,
)


def test_normalize_base_url_adds_suffix_and_rejects_secret_url_parts() -> None:
    assert (
        normalize_base_url(
            "http://10.20.30.1:24040",
            env_name="TEST_BASE_URL",
            suffix="/v1",
        )
        == "http://10.20.30.1:24040/v1"
    )
    assert (
        normalize_base_url(
            "http://127.0.0.1:28010",
            env_name="TEST_ANALYTICS_URL",
            suffix="/mcp",
        )
        == "http://127.0.0.1:28010/mcp/"
    )

    with pytest.raises(ValueError, match="TEST_BASE_URL"):
        normalize_base_url(
            "http://user:secret@10.20.30.1:24040",
            env_name="TEST_BASE_URL",
            suffix="/v1",
        )
    with pytest.raises(ValueError, match="TEST_BASE_URL"):
        normalize_base_url(
            "http://10.20.30.1:24040?api_key=secret",
            env_name="TEST_BASE_URL",
            suffix="/v1",
        )


def test_compression_mode_header_value_normalizes_baseline_modes() -> None:
    assert compression_mode_header_value(None, env_name="TEST_MODE") is None
    assert compression_mode_header_value("", env_name="TEST_MODE") is None
    assert compression_mode_header_value("disabled", env_name="TEST_MODE") == "off"
    assert compression_mode_header_value("FALSE", env_name="TEST_MODE") == "off"
    assert compression_mode_header_value("enabled", env_name="TEST_MODE") == "on"
    assert compression_mode_header_value("1", env_name="TEST_MODE") == "on"

    with pytest.raises(ValueError, match="TEST_MODE"):
        compression_mode_header_value("maybe", env_name="TEST_MODE")


def test_render_toml_writes_parseable_config_without_secret_magic() -> None:
    rendered = render_toml(
        {
            "model": "gpt-5.5",
            "model_providers": {
                "litellm": {
                    "base_url": "http://10.20.30.1:24040/v1",
                    "env_key": "OPENAI_API_KEY",
                }
            },
        },
        header="# generated",
    )

    parsed = tomllib.loads(rendered)
    assert parsed["model"] == "gpt-5.5"
    assert parsed["model_providers"]["litellm"] == {
        "base_url": "http://10.20.30.1:24040/v1",
        "env_key": "OPENAI_API_KEY",
    }
    assert "secret" not in rendered


def test_sync_native_state_symlinks_native_files_and_backs_up_local_state(
    tmp_path: Path,
) -> None:
    native_home = tmp_path / ".native"
    managed_home = tmp_path / ".managed"
    native_home.mkdir()
    managed_home.mkdir()
    native_sessions = native_home / "sessions"
    native_sessions.mkdir()
    (native_sessions / "session.jsonl").write_text("{}\n")
    (native_home / "config.toml").write_text("model = 'native'\n")
    local_sessions = managed_home / "sessions"
    local_sessions.mkdir()
    (local_sessions / "old.jsonl").write_text("{}\n")

    sync_native_state(
        native_home=native_home,
        managed_home=managed_home,
        excluded_names={"config.toml"},
        backup_tag="test-backup",
    )

    assert (managed_home / "sessions").is_symlink()
    assert (managed_home / "sessions").resolve(strict=True) == native_sessions
    assert not (managed_home / "config.toml").exists()
    backups = list(managed_home.glob(".sessions.test-backup.*"))
    assert len(backups) == 1
    assert (backups[0] / "old.jsonl").read_text() == "{}\n"


def test_find_real_executable_skips_wrapper_path(tmp_path: Path, monkeypatch) -> None:
    wrapper = tmp_path / "wrapper" / "codex"
    real = tmp_path / "real" / "codex"
    wrapper.parent.mkdir()
    real.parent.mkdir()
    wrapper.write_text("#!/bin/sh\n")
    real.write_text("#!/bin/sh\n")
    wrapper.chmod(0o755)
    real.chmod(0o755)
    monkeypatch.setenv("PATH", os.pathsep.join([str(wrapper.parent), str(real.parent)]))

    assert find_real_executable(binary_name="codex", wrapper_path=wrapper) == str(real)


def test_find_real_executable_skips_legacy_litellm_shims(
    tmp_path: Path, monkeypatch
) -> None:
    wrapper = tmp_path / "wrapper" / "codex-litellm"
    stale = tmp_path / "stale" / "codex"
    real = tmp_path / "real" / "codex"
    wrapper.parent.mkdir()
    stale.parent.mkdir()
    real.parent.mkdir()
    wrapper.write_text("#!/bin/sh\n")
    stale.write_text(
        "#!/usr/bin/env bash\n"
        'ENV_FILE="${LITELLM_PROXY_ENV_FILE:-$HOME/.config/litellm-proxy/env}"\n'
        'export OPENAI_BASE_URL="${OPENAI_BASE_URL:-http://10.20.30.1:11435/v1}"\n'
        'exec npx --yes @openai/codex "$@"\n'
    )
    real.write_text("#!/usr/bin/env node\n")
    wrapper.chmod(0o755)
    stale.chmod(0o755)
    real.chmod(0o755)
    monkeypatch.setenv("PATH", os.pathsep.join([str(stale.parent), str(real.parent)]))

    assert find_real_executable(binary_name="codex", wrapper_path=wrapper) == str(real)
