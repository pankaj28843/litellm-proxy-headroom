from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LITELLM_HEADROOM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = Field(default="127.0.0.1", min_length=1)
    port: int = Field(default=4000, ge=1, le=65535)
    config_path: Path = Field(
        default=Path("config/litellm.yaml"),
        validation_alias=AliasChoices(
            "LITELLM_HEADROOM_CONFIG",
            "LITELLM_HEADROOM_CONFIG_PATH",
        ),
    )
