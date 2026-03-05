from __future__ import annotations

from pathlib import Path
from typing import Optional

import tomllib
from pydantic import AliasChoices, BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class RateLimitConfig(BaseModel):
    host_concurrency: int = Field(
        default=4,
        validation_alias=AliasChoices("GBTD_HOST_CONCURRENCY", "RATE_LIMITS_HOST_CONCURRENCY", "HOST_CONCURRENCY"),
    )
    per_host_rps: float = Field(
        default=1.0,
        validation_alias=AliasChoices("GBTD_PER_HOST_RPS", "RATE_LIMITS_PER_HOST_RPS", "PER_HOST_RPS"),
    )
    burst: int = Field(
        default=6,
        validation_alias=AliasChoices("GBTD_RATE_BUCKET_BURST", "RATE_LIMITS_BURST", "BURST"),
    )
    backoff_base_seconds: float = Field(
        default=10.0,
        validation_alias=AliasChoices("GBTD_RETRY_BASE_SECONDS", "RATE_LIMITS_RETRY_BASE_SECONDS", "RETRY_BASE_SECONDS"),
    )
    backoff_max_seconds: float = Field(
        default=180.0,
        validation_alias=AliasChoices("GBTD_RETRY_MAX_SECONDS", "RATE_LIMITS_RETRY_MAX_SECONDS", "RETRY_MAX_SECONDS"),
    )
    retry_jitter: float = Field(
        default=0.35,
        validation_alias=AliasChoices("GBTD_RETRY_JITTER", "RATE_LIMITS_RETRY_JITTER", "RETRY_JITTER"),
    )


class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    database_url: str = Field(
        default="postgresql+psycopg://gbtd:gbtd@127.0.0.1:5432/gbtd",
        validation_alias=AliasChoices("GBTD_DATABASE_URL", "DATABASE_URL"),
    )
    runner_id: str = Field(default="worker-1", validation_alias=AliasChoices("GBTD_RUNNER_ID", "RUNNER_ID"))
    worker_concurrency: int = Field(default=4, validation_alias=AliasChoices("GBTD_CONCURRENCY", "WORKER_CONCURRENCY"))
    lease_seconds: int = Field(default=900, validation_alias=AliasChoices("GBTD_LEASE_SECONDS", "LEASE_SECONDS"))
    visibility_timeout_seconds: int = Field(
        default=1200,
        validation_alias=AliasChoices("GBTD_VISIBILITY_TIMEOUT_SECONDS", "VISIBILITY_TIMEOUT_SECONDS"),
    )
    manifest_path: str = Field(
        default="manifests/sample.manifest.yaml",
        validation_alias=AliasChoices("GBTD_MANIFEST_PATH", "MANIFEST_PATH"),
    )

    rate_limits: RateLimitConfig = RateLimitConfig()

    github_token: Optional[str] = Field(default=None, validation_alias=AliasChoices("GBTD_GITHUB_TOKEN", "GITHUB_TOKEN"))
    gitlab_token: Optional[str] = Field(default=None, validation_alias=AliasChoices("GBTD_GITLAB_TOKEN", "GITLAB_TOKEN"))
    jira_token: Optional[str] = Field(default=None, validation_alias=AliasChoices("GBTD_JIRA_TOKEN", "JIRA_TOKEN"))
    launchpad_token: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("GBTD_LAUNCHPAD_TOKEN", "LAUNCHPAD_TOKEN"),
    )
    redmine_token: Optional[str] = Field(default=None, validation_alias=AliasChoices("GBTD_REDMINE_TOKEN", "REDMINE_TOKEN"))
    youtrack_token: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("GBTD_YOUTRACK_TOKEN", "YOUTRACK_TOKEN"),
    )
    google_token: Optional[str] = Field(default=None, validation_alias=AliasChoices("GBTD_GOOGLE_TOKEN", "GOOGLE_TOKEN"))
    debian_token: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("GBTD_DEBIAN_TOKEN", "DEBIAN_TOKEN", "DEBIAN_API_KEY"),
    )

    timeout_seconds: float = Field(default=20.0, validation_alias=AliasChoices("GBTD_TIMEOUT_SECONDS", "TIMEOUT_SECONDS"))
    user_agent: str = Field(
        default="GBTD-Collector/0.1 (+research)",
        validation_alias=AliasChoices("GBTD_USER_AGENT", "USER_AGENT"),
    )

    @classmethod
    def load(cls, config_path: str | None = None) -> "AppConfig":
        file_cfg: dict = {}
        if config_path:
            path = Path(config_path)
            if not path.exists():
                raise FileNotFoundError(f"config file not found: {config_path}")
            with path.open("rb") as fh:
                file_cfg = tomllib.load(fh)

        return cls(**file_cfg)
