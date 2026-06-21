from pathlib import Path

import pytest
from pydantic import ValidationError

from litellm_proxy_headroom.settings import Settings


def test_settings_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.host == "127.0.0.1"
    assert settings.port == 4000
    assert settings.config_path == Path("config/litellm.yaml")


def test_settings_reads_prefixed_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LITELLM_HEADROOM_HOST", "0.0.0.0")
    monkeypatch.setenv("LITELLM_HEADROOM_PORT", "4100")
    monkeypatch.setenv("LITELLM_HEADROOM_CONFIG", "custom/litellm.yaml")

    settings = Settings(_env_file=None)

    assert settings.host == "0.0.0.0"
    assert settings.port == 4100
    assert settings.config_path == Path("custom/litellm.yaml")


def test_settings_rejects_invalid_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LITELLM_HEADROOM_PORT", "70000")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)
