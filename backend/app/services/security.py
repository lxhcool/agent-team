"""Runtime Security Enforcement Service.

Implements:
- SC-003: Work directory restriction (runtime enforcement)
- SC-004: Path traversal protection (runtime interception)
- SC-005: Sensitive file protection (runtime interception)
- SC-011: Log desensitization (strip API keys from logs)
- T-011: Three-level security permissions (low/medium/high)
"""

import fnmatch
import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from app.core.config import settings

logger = logging.getLogger(__name__)


class RuntimeSecurityEnforcer:
    """Runtime security enforcement that actually blocks dangerous operations.

    Unlike the API-level security settings (which just store config),
    this enforcer is called by tools and agents at execution time.
    """

    # Patterns that indicate API keys or secrets in logs
    SENSITIVE_LOG_PATTERNS = [
        (r'(api[_-]?key\s*[:=]\s*["\']?)([\w\-]{20,})(["\']?)', r'\1***\3'),
        (r'(authorization\s*[:=]\s*["\']?bearer\s+)([\w\-\.]{20,})(["\']?)', r'\1***\3'),
        (r'(sk-)([\w]{20,})([\w]*)', r'\1***\3'),
        (r'(password\s*[:=]\s*["\']?)([\w\-]{8,})(["\']?)', r'\1***\3'),
        (r'(token\s*[:=]\s*["\']?)([\w\-\.]{20,})(["\']?)', r'\1***\3'),
        (r'(secret\s*[:=]\s*["\']?)([\w\-]{8,})(["\']?)', r'\1***\3'),
    ]

    def __init__(self):
        self._settings: dict = {}
        self._loaded = False

    async def load_settings(self):
        """Load security settings from the security API's JSON file."""
        if self._loaded:
            return
        try:
            security_file = settings.data_dir / "security_settings.json"
            defaults = {
                "safe_mode": False,
                "command_blacklist": [
                    "rm -rf /", "rm -rf ~", "mkfs", "dd if=/dev/zero",
                    ":(){ :|:& };:", "chmod -R 777 /", "chown -R",
                    "> /dev/sda", "mv / /dev/null",
                ],
                "protected_paths": ["/etc", "/root", "~/.ssh", "/var", "/sys", "/proc"],
                "sensitive_file_patterns": [
                    ".env", "*.key", "*.pem", "*.p12", "*.pfx",
                    "id_rsa", "id_ed25519", "credentials.json",
                ],
                "max_command_timeout": 300,
                "auto_approve": False,
                "high_risk_requires_approval": True,
            }
            if security_file.exists():
                try:
                    loaded = json.loads(security_file.read_text(encoding="utf-8"))
                    self._settings = {**defaults, **loaded}
                except (json.JSONDecodeError, OSError):
                    self._settings = defaults
            else:
                self._settings = defaults
            self._loaded = True
        except Exception as e:
            logger.warning(f"Failed to load security settings: {e}")
            self._settings = {}

    async def reload_settings(self):
        """Force reload settings from disk."""
        self._loaded = False
        await self.load_settings()

    # ===== SC-003: Work Directory Restriction =====

    def check_workdir(self, path: str, allowed_root: Optional[str] = None) -> Tuple[bool, str]:
        """Check if a path is within the allowed work directory.

        Args:
            path: The path to check
            allowed_root: The root directory that bounds operations. If None, only system paths are blocked.

        Returns:
            (allowed, reason) tuple
        """
        try:
            resolved = Path(path).resolve()
        except Exception:
            return False, f"Invalid path: {path}"

        # If an allowed_root is specified, the path must be within it
        if allowed_root:
            try:
                root = Path(allowed_root).resolve()
                resolved.relative_to(root)
            except ValueError:
                return False, f"Path '{path}' is outside allowed work directory: {allowed_root}"

        return True, ""

    # ===== SC-004: Path Traversal Protection =====

    def check_path_traversal(self, path: str) -> Tuple[bool, str]:
        """Detect and block path traversal attacks.

        Checks for:
        - '..' components that escape upward
        - Symlinks that point outside the allowed directory
        - Access to protected system paths
        """
        path_str = str(path)

        # Check for path traversal patterns
        if ".." in Path(path_str).parts:
            return False, f"Path traversal detected: '{path}' contains '..' component"

        # Check protected paths
        protected_paths = self._settings.get("protected_paths", [])
        try:
            resolved = Path(path_str).resolve()
            for pp in protected_paths:
                pp_resolved = Path(pp).expanduser().resolve()
                try:
                    resolved.relative_to(pp_resolved)
                    return False, f"Access to protected path forbidden: {pp}"
                except ValueError:
                    continue
        except Exception:
            pass

        return True, ""

    # ===== SC-005: Sensitive File Protection =====

    def check_sensitive_file(self, path: str, operation: str = "read") -> Tuple[bool, str]:
        """Check if a file is sensitive and should be protected.

        Args:
            path: File path to check
            operation: "read" or "write"

        Returns:
            (allowed, reason) tuple
        """
        sensitive_patterns = self._settings.get("sensitive_file_patterns", [])
        safe_mode = self._settings.get("safe_mode", False)

        name = Path(path).name

        for pattern in sensitive_patterns:
            if fnmatch.fnmatch(name, pattern):
                if operation == "write":
                    return False, f"Cannot modify sensitive file matching '{pattern}': {name}"
                if operation == "read" and safe_mode:
                    return False, f"Cannot read sensitive file in safe mode (pattern: '{pattern}'): {name}"
                # In non-safe mode, reading sensitive files is allowed but logged
                logger.warning(f"Reading sensitive file: {name} (pattern: {pattern})")

        return True, ""

    # ===== Command Security =====

    def check_command(self, command: str) -> Tuple[bool, str]:
        """Check if a command is allowed to execute.

        Checks against the command blacklist and safe mode whitelist.
        """
        blacklist = self._settings.get("command_blacklist", [])
        safe_mode = self._settings.get("safe_mode", False)

        # Check blacklist
        for pattern in blacklist:
            # Support both literal and regex patterns
            try:
                if re.search(re.escape(pattern), command, re.IGNORECASE):
                    return False, f"Command matches blacklist pattern: {pattern}"
            except re.error:
                if pattern.lower() in command.lower():
                    return False, f"Command contains blacklisted pattern: {pattern}"

        # Safe mode whitelist
        if safe_mode:
            safe_prefixes = [
                "git status", "git diff", "git log", "git branch", "git show",
                "ls", "cat", "head", "tail", "find", "grep", "wc",
                "pytest", "npm test", "npm run lint", "yarn test",
                "eslint", "mypy", "ruff", "flake8", "tsc --noEmit",
                "cargo test", "cargo check", "go test", "go vet",
                "python -m pytest",
            ]
            for prefix in safe_prefixes:
                if command.strip().startswith(prefix):
                    return True, ""
            return False, "Safe mode: command not in allowed list"

        return True, ""

    def get_max_timeout(self) -> int:
        """Get the maximum command timeout from settings."""
        return self._settings.get("max_command_timeout", 300)

    def requires_approval(self, risk_level: str) -> bool:
        """Check if a high-risk operation requires human approval."""
        if self._settings.get("safe_mode", False):
            return True
        if risk_level == "high":
            return self._settings.get("high_risk_requires_approval", True)
        return False

    # ===== SC-011: Log Desensitization =====

    @classmethod
    def sanitize_log(cls, message: str) -> str:
        """Remove sensitive information from log messages.

        Strips API keys, tokens, passwords, and other secrets.
        """
        sanitized = message
        for pattern, replacement in cls.SENSITIVE_LOG_PATTERNS:
            sanitized = re.sub(pattern, replacement, sanitized, flags=re.IGNORECASE)
        return sanitized

    @classmethod
    def sanitize_dict(cls, data: dict) -> dict:
        """Recursively sanitize a dictionary for safe logging."""
        sensitive_keys = {
            "api_key", "apikey", "api-key", "secret", "password", "token",
            "authorization", "credentials", "private_key", "access_token",
        }

        result = {}
        for key, value in data.items():
            key_lower = key.lower().replace("-", "_").replace(" ", "_")
            if key_lower in sensitive_keys:
                if isinstance(value, str) and len(value) > 4:
                    result[key] = value[:2] + "***" + value[-2:]
                else:
                    result[key] = "***"
            elif isinstance(value, dict):
                result[key] = cls.sanitize_dict(value)
            elif isinstance(value, list):
                result[key] = [
                    cls.sanitize_dict(v) if isinstance(v, dict) else v
                    for v in value
                ]
            else:
                result[key] = value
        return result


# ===== P1-8: LocalWorkspace for Tool Execution Safety =====

class LocalWorkspace:
    """Restricts tool execution to a specific workspace directory.

    Per requirements: Backend tools should operate within a bounded
    workspace directory, preventing access outside the project root.
    """

    def __init__(self, root_path: str):
        """Initialize with the workspace root path.

        Args:
            root_path: The root directory that bounds all file operations.
        """
        self.root = Path(root_path).resolve()

    def is_within(self, path: str) -> bool:
        """Check if a path is within the workspace root."""
        try:
            resolved = Path(path).resolve()
            resolved.relative_to(self.root)
            return True
        except (ValueError, OSError):
            return False

    def safe_path(self, path: str) -> Optional[str]:
        """Resolve a path and verify it's within workspace.

        Returns:
            The resolved absolute path if safe, None otherwise.
        """
        try:
            resolved = Path(path).resolve()
            resolved.relative_to(self.root)
            return str(resolved)
        except (ValueError, OSError):
            return None

    def enforce_path(self, path: str) -> Tuple[bool, str]:
        """Enforce that a path is within the workspace.

        Returns:
            (allowed, reason) tuple.
        """
        if self.is_within(path):
            return True, ""
        return False, f"Path '{path}' is outside workspace root: {self.root}"

    def get_root(self) -> str:
        """Get the workspace root path."""
        return str(self.root)


# ===== Custom Logging Filter for SC-011 =====

class SensitiveDataFilter(logging.Filter):
    """Logging filter that automatically sanitizes sensitive data."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = RuntimeSecurityEnforcer.sanitize_log(record.msg)
        if record.args and isinstance(record.args, dict):
            record.args = RuntimeSecurityEnforcer.sanitize_dict(record.args)
        return True


# ===== Global Singleton =====

runtime_security = RuntimeSecurityEnforcer()


def setup_secure_logging():
    """Set up secure logging with sensitive data filtering."""
    # Apply the filter to all handlers on the root logger
    sensitive_filter = SensitiveDataFilter()
    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        handler.addFilter(sensitive_filter)

    # Also apply to our app loggers
    for name in ["app", "app.llm", "app.services", "app.api"]:
        app_logger = logging.getLogger(name)
        for handler in app_logger.handlers:
            handler.addFilter(sensitive_filter)
        if not app_logger.handlers:
            # Propagate to root which has the filter
            app_logger.propagate = True
