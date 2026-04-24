"""Application configuration using pydantic-settings."""

import secrets
from pathlib import Path
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve .env path: check backend/ first, then project root
_project_root = Path(__file__).resolve().parent.parent.parent.parent
_backend_dir = Path(__file__).resolve().parent.parent.parent
_env_file = _backend_dir / ".env" if (_backend_dir / ".env").exists() else _project_root / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_env_file) if _env_file.exists() else ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = True

    # Database
    database_url: str = "sqlite+aiosqlite:///./data/team_agent.db"

    # Encryption
    encryption_key: str = secrets.token_urlsafe(32)

    # LLM Defaults
    default_llm_provider: str = "openai"
    default_llm_model: str = "gpt-4o-mini"

    # CORS
    cors_origins: List[str] = [
        "http://localhost:3000", "http://127.0.0.1:3000",
        "http://localhost:3001", "http://127.0.0.1:3001",
        "http://localhost:3200", "http://127.0.0.1:3200",
    ]

    # Paths
    data_dir: Path = Path("./data")
    artifacts_dir: Path = Path("./data/artifacts")

    # Budget
    default_session_budget_usd: float = 10.0


settings = Settings()
