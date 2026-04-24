"""Security Settings API - manage safe mode, command blacklist, protected paths."""

import json
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.core.config import settings

router = APIRouter()

# Default security settings
_DEFAULT_SECURITY = {
    "safe_mode": False,
    "command_blacklist": [
        "rm -rf /",
        "rm -rf ~",
        "mkfs",
        "dd if=/dev/zero",
        ":(){ :|:& };:",
        "chmod -R 777 /",
        "chown -R",
        "> /dev/sda",
        "mv / /dev/null",
    ],
    "protected_paths": ["/etc", "/root", "~/.ssh", "/var", "/sys", "/proc"],
    "sensitive_file_patterns": [".env", "*.key", "*.pem", "*.p12", "*.pfx", "id_rsa", "id_ed25519", "credentials.json", "service-account*.json"],
    "max_command_timeout": 300,
}


def _get_security_file_path() -> Path:
    return settings.data_dir / "security_settings.json"


def _load_security_settings() -> dict:
    path = _get_security_file_path()
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return dict(_DEFAULT_SECURITY)


def _save_security_settings(data: dict):
    path = _get_security_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ===== Schemas =====

class SecuritySettingsRequest(BaseModel):
    safe_mode: Optional[bool] = None
    command_blacklist: Optional[List[str]] = None
    protected_paths: Optional[List[str]] = None
    sensitive_file_patterns: Optional[List[str]] = None
    max_command_timeout: Optional[int] = Field(default=None, ge=1, le=3600)


class SecuritySettingsResponse(BaseModel):
    safe_mode: bool
    command_blacklist: List[str]
    protected_paths: List[str]
    sensitive_file_patterns: List[str]
    max_command_timeout: int


# ===== Endpoints =====

@router.get("/security", response_model=SecuritySettingsResponse)
async def get_security_settings():
    """Get current security settings."""
    data = _load_security_settings()
    return SecuritySettingsResponse(
        safe_mode=data.get("safe_mode", _DEFAULT_SECURITY["safe_mode"]),
        command_blacklist=data.get("command_blacklist", _DEFAULT_SECURITY["command_blacklist"]),
        protected_paths=data.get("protected_paths", _DEFAULT_SECURITY["protected_paths"]),
        sensitive_file_patterns=data.get("sensitive_file_patterns", _DEFAULT_SECURITY["sensitive_file_patterns"]),
        max_command_timeout=data.get("max_command_timeout", _DEFAULT_SECURITY["max_command_timeout"]),
    )


@router.put("/security", response_model=SecuritySettingsResponse)
async def update_security_settings(req: SecuritySettingsRequest):
    """Update security settings. Only provided fields are updated."""
    data = _load_security_settings()

    if req.safe_mode is not None:
        data["safe_mode"] = req.safe_mode
    if req.command_blacklist is not None:
        data["command_blacklist"] = req.command_blacklist
    if req.protected_paths is not None:
        data["protected_paths"] = req.protected_paths
    if req.sensitive_file_patterns is not None:
        data["sensitive_file_patterns"] = req.sensitive_file_patterns
    if req.max_command_timeout is not None:
        data["max_command_timeout"] = req.max_command_timeout

    _save_security_settings(data)

    return SecuritySettingsResponse(
        safe_mode=data["safe_mode"],
        command_blacklist=data["command_blacklist"],
        protected_paths=data["protected_paths"],
        sensitive_file_patterns=data["sensitive_file_patterns"],
        max_command_timeout=data["max_command_timeout"],
    )
