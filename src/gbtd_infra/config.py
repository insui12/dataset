from __future__ import annotations

from pathlib import Path
from typing import Optional

import tomllib
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class RateLimitConfig(BaseModel):
    host_concurrency: int = 4
    per_host_rps: float = 1.0
    burst: int = 6
    backoff_base_seconds: float = 10.0
    backoff_max_seconds: float = 180.0
    retry_jitter: float = 0.35


class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="gbtd_")

    database_url: str = "postgresql+psycopg://gbtd:gbtd@127.0.0.1:5432/gbtd"
    runner_id: str = "worker-1"
    worker_concurrency: int = 4
    lease_seconds: int = 900
    visibility_timeout_seconds: int = 1200
    manifest_path: str = "manifests/sample.manifest.yaml"

    rate_limits: RateLimitConfig = RateLimitConfig()

    github_token: Optional[str] = None
    gitlab_token: Optional[str] = None
    jira_token: Optional[str] = None
    launchpad_token: Optional[str] = None
    redmine_token: Optional[str] = None
    youtrack_token: Optional[str] = None
    google_token: Optional[str] = None
    debian_token: Optional[str] = None

    timeout_seconds: float = 20.0
    user_agent: str = "GBTD-Collector/0.1 (+research)"

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

