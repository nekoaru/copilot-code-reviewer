from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT_DIR / "backend" / "data"
REPOS_DIR = ROOT_DIR / "backend" / "repos"
JOB_LOGS_DIR = DATA_DIR / "job_logs"
FRONTEND_DIST_DIR = ROOT_DIR / "frontend" / "dist"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = Field(
        default="GitLab Copilot MR Reviewer",
        validation_alias=AliasChoices("APP_NAME"),
    )
    app_host: str = Field(default="0.0.0.0", validation_alias=AliasChoices("APP_HOST"))
    app_port: int = Field(default=8001, validation_alias=AliasChoices("APP_PORT"))
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173"],
        validation_alias=AliasChoices("CORS_ORIGINS"),
    )

    gitlab_base_url: str = Field(default="https://gitlab.com", validation_alias=AliasChoices("GITLAB_BASE_URL"))
    gitlab_token: str = Field(validation_alias=AliasChoices("GITLAB_TOKEN"))
    public_base_url: str = Field(
        default="https://lkjdp2fh-8001.jpe1.devtunnels.ms",
        validation_alias=AliasChoices("PUBLIC_BASE_URL"),
    )
    review_trigger_keyword: str = Field(
        default="/copilot-review",
        validation_alias=AliasChoices("REVIEW_TRIGGER_KEYWORD"),
    )
    default_review_language: str = Field(
        default="Chinese",
        validation_alias=AliasChoices("DEFAULT_REVIEW_LANGUAGE"),
    )
    copilot_model: str = Field(default="claude-sonnet-4.6", validation_alias=AliasChoices("COPILOT_MODEL"))
    copilot_timeout_seconds: int = Field(
        default=3600,
        validation_alias=AliasChoices("COPILOT_TIMEOUT_SECONDS", "COPILOT_TIMEOUT"),
    )
    inline_min_severity: str = Field(
        default="medium",
        validation_alias=AliasChoices("INLINE_MIN_SEVERITY"),
    )
    clone_root: Path = Field(default=REPOS_DIR, validation_alias=AliasChoices("CLONE_ROOT"))
    config_store_path: Path = Field(
        default=DATA_DIR / "project_configs.json",
        validation_alias=AliasChoices("CONFIG_STORE_PATH"),
    )
    job_store_path: Path = Field(
        default=DATA_DIR / "review_jobs.json",
        validation_alias=AliasChoices("JOB_STORE_PATH"),
    )
    job_logs_dir: Path = Field(default=JOB_LOGS_DIR, validation_alias=AliasChoices("JOB_LOGS_DIR"))
    frontend_dist_dir: Path = Field(
        default=FRONTEND_DIST_DIR,
        validation_alias=AliasChoices("FRONTEND_DIST_DIR"),
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: Any) -> list[str]:
        if value is None or value == "":
            return ["http://localhost:5173"]
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return ["http://localhost:5173"]
            if text.startswith("["):
                parsed = json.loads(text)
                if not isinstance(parsed, list):
                    raise ValueError("CORS_ORIGINS JSON must be an array")
                return [str(item).strip() for item in parsed if str(item).strip()]
            return [item.strip() for item in text.split(",") if item.strip()]
        raise ValueError("Invalid CORS_ORIGINS value")

    @field_validator("clone_root", "config_store_path", "job_store_path", "job_logs_dir", "frontend_dist_dir", mode="before")
    @classmethod
    def parse_paths(cls, value: Any) -> Path | Any:
        if isinstance(value, str) and value.strip():
            return Path(value.strip()).expanduser()
        return value


settings = Settings()
DATA_DIR.mkdir(parents=True, exist_ok=True)
REPOS_DIR.mkdir(parents=True, exist_ok=True)
JOB_LOGS_DIR.mkdir(parents=True, exist_ok=True)
settings.clone_root.mkdir(parents=True, exist_ok=True)
settings.job_logs_dir.mkdir(parents=True, exist_ok=True)
settings.config_store_path.parent.mkdir(parents=True, exist_ok=True)
settings.job_store_path.parent.mkdir(parents=True, exist_ok=True)
