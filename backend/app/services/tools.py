"""Tool System - 7 P0 Tools for Agent execution.

Implements T-001~T-007:
- file_read: Read file contents
- file_write: Write/create files
- file_list: List directory contents
- shell_execute: Execute shell commands
- web_search: Search the web
- send_message: Send message to another agent
- ask_human: Ask human for input/confirmation

Also implements:
- T-010: Tool default risk mapping
- T-011: Three-level security (low/medium/high)
- T-012: Tool execution timeout
- T-013: Tool output specification ToolResult(success, data, error, metadata)
"""

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from app.core.database import async_session
from app.models.models import ToolExecution

logger = logging.getLogger(__name__)


# ===== Risk Levels (T-011) =====

class ToolRiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# ===== Default Risk Mapping (T-010) =====

TOOL_RISK_MAP = {
    "file_read": ToolRiskLevel.LOW,
    "file_list": ToolRiskLevel.LOW,
    "web_search": ToolRiskLevel.LOW,
    "send_message": ToolRiskLevel.LOW,
    "ask_human": ToolRiskLevel.LOW,
    "file_write": ToolRiskLevel.MEDIUM,
    "shell_execute": ToolRiskLevel.HIGH,
}


# ===== Tool Result (T-013) =====

@dataclass
class ToolResult:
    """Standardized tool output per T-013."""
    success: bool
    data: Any = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "metadata": self.metadata,
        }


# ===== Security Enforcer =====

class ToolSecurityEnforcer:
    """Runtime security enforcement per SC-003~SC-005, T-011."""

    def __init__(self, security_settings: Optional[dict] = None):
        self._settings = security_settings or {}

    async def load_settings(self):
        """Load security settings from file."""
        try:
            from app.api.security import _load_security_settings
            self._settings = _load_security_settings()
        except Exception:
            self._settings = {}

    def check_path_access(self, path: str, operation: str = "read") -> tuple:
        """Check path access per SC-003 (work dir), SC-004 (path traversal), SC-005 (sensitive files)."""
        import fnmatch
        from pathlib import Path

        protected_paths = self._settings.get("protected_paths", [])
        sensitive_patterns = self._settings.get("sensitive_file_patterns", [])

        # SC-004: Path traversal check
        resolved = Path(path).resolve()
        path_str = str(resolved)

        # Check protected paths
        for pp in protected_paths:
            if path_str.startswith(pp) or path_str.startswith(str(Path(pp).resolve())):
                return False, f"Path is in protected directory: {pp}"

        # SC-005: Sensitive file check
        name = resolved.name
        for pattern in sensitive_patterns:
            if fnmatch.fnmatch(name, pattern):
                if operation in ("write", "delete"):
                    return False, f"Cannot modify sensitive file matching pattern: {pattern}"
                if operation == "read" and self._settings.get("safe_mode", False):
                    return False, f"Cannot read sensitive file in safe mode: {pattern}"

        return True, ""

    def check_command(self, command: str) -> tuple:
        """Check command against blacklist."""
        import re

        blacklist = self._settings.get("command_blacklist", [])
        for pattern in blacklist:
            try:
                if re.search(re.escape(pattern), command, re.IGNORECASE):
                    return False, f"Command matches blacklist pattern: {pattern}"
            except re.error:
                if pattern in command:
                    return False, f"Command contains blacklisted pattern: {pattern}"

        return True, ""

    def get_timeout(self) -> int:
        """Get max command timeout per T-012."""
        return self._settings.get("max_command_timeout", 300)

    def requires_approval(self, tool_name: str, risk_level: ToolRiskLevel) -> bool:
        """Check if tool execution requires human approval per T-011."""
        if self._settings.get("safe_mode", False):
            return True
        if risk_level == ToolRiskLevel.HIGH:
            return self._settings.get("high_risk_requires_approval", True)
        return False


# ===== Global Enforcer =====

_security_enforcer: Optional[ToolSecurityEnforcer] = None


def get_security_enforcer() -> ToolSecurityEnforcer:
    global _security_enforcer
    if _security_enforcer is None:
        _security_enforcer = ToolSecurityEnforcer()
    return _security_enforcer


# ===== Base Tool =====

class BaseTool:
    """Base class for all tools.

    Provides:
    - Cached security enforcer (avoids repeated load_settings calls)
    - Standard path/workspace/security checks
    - Execution logging
    - Consistent error handling
    """

    name: str = ""
    description: str = ""
    risk_level: ToolRiskLevel = ToolRiskLevel.LOW
    parameters_schema: dict = {}

    def __init__(self):
        self.risk_level = TOOL_RISK_MAP.get(self.name, ToolRiskLevel.MEDIUM)
        self._enforcer: Optional[ToolSecurityEnforcer] = None

    async def _get_enforcer(self) -> ToolSecurityEnforcer:
        """Get or create a cached security enforcer."""
        if self._enforcer is None:
            self._enforcer = get_security_enforcer()
            await self._enforcer.load_settings()
        return self._enforcer

    async def _check_path(self, path: str, operation: str = "read",
                          workspace_root: Optional[str] = None) -> Optional[str]:
        """Unified path security check. Returns error message or None if OK."""
        enforcer = await self._get_enforcer()

        # P1-8: LocalWorkspace check
        if workspace_root:
            from app.services.security import LocalWorkspace
            ws = LocalWorkspace(workspace_root)
            allowed, reason = ws.enforce_path(path)
            if not allowed:
                return reason

        # Security check
        allowed, reason = enforcer.check_path_access(path, operation)
        if not allowed:
            return reason
        return None

    async def _check_command(self, command: str) -> Optional[str]:
        """Unified command security check. Returns error message or None if OK."""
        enforcer = await self._get_enforcer()
        allowed, reason = enforcer.check_command(command)
        if not allowed:
            return reason
        return None

    async def _check_workspace_path(self, path: str, workspace_root: Optional[str] = None) -> Optional[str]:
        """Check only workspace boundary (for listing operations)."""
        if workspace_root:
            from app.services.security import LocalWorkspace
            ws = LocalWorkspace(workspace_root)
            allowed, reason = ws.enforce_path(path)
            if not allowed:
                return reason
        return None

    async def execute(self, **kwargs) -> ToolResult:
        raise NotImplementedError

    async def _log_execution(
        self,
        session_id: str,
        session_type: str,
        agent_name: Optional[str] = None,
        task_id: Optional[str] = None,
        input_data: Optional[dict] = None,
        output_data: Optional[dict] = None,
        status: str = "completed",
        duration_ms: int = 0,
    ):
        """Log tool execution to DB."""
        try:
            async with async_session() as db:
                execution = ToolExecution(
                    session_type=session_type,
                    session_id=session_id,
                    task_id=task_id,
                    agent_name=agent_name,
                    tool_name=self.name,
                    status=status,
                    duration_ms=duration_ms,
                    input_json=json.dumps(input_data, ensure_ascii=False) if input_data else None,
                    output_json=json.dumps(output_data, ensure_ascii=False) if output_data else None,
                )
                db.add(execution)
                await db.commit()
        except Exception as e:
            logger.warning(f"Failed to log tool execution: {e}")


# ===== T-001: file_read =====

class FileReadTool(BaseTool):
    name = "file_read"
    description = "Read the contents of a file. The file must be within the project workspace."
    risk_level = ToolRiskLevel.LOW
    parameters_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the file to read"},
            "encoding": {"type": "string", "default": "utf-8", "description": "File encoding"},
            "start_line": {"type": "integer", "description": "Start line number (1-based, optional)"},
            "end_line": {"type": "integer", "description": "End line number (1-based, optional)"},
        },
        "required": ["path"],
    }

    async def execute(self, path: str, encoding: str = "utf-8",
                      start_line: Optional[int] = None, end_line: Optional[int] = None,
                      workspace_root: Optional[str] = None,
                      **context) -> ToolResult:
        # Unified security check
        err = await self._check_path(path, "read", workspace_root)
        if err:
            return ToolResult(success=False, error=err)

        try:
            from pathlib import Path
            file_path = Path(path)
            if not file_path.exists():
                return ToolResult(success=False, error=f"File not found: {path}")
            if not file_path.is_file():
                return ToolResult(success=False, error=f"Path is not a file: {path}")

            content = file_path.read_text(encoding=encoding)

            # Line range support
            if start_line is not None or end_line is not None:
                lines = content.splitlines(keepends=True)
                start = (start_line or 1) - 1
                end = end_line or len(lines)
                content = "".join(lines[start:end])
                total_lines = len(lines)
            else:
                total_lines = content.count("\n") + 1

            return ToolResult(
                success=True,
                data={"content": content, "path": str(file_path), "lines": total_lines},
                metadata={"tool": "file_read", "path": path},
            )
        except UnicodeDecodeError:
            return ToolResult(success=False, error=f"Cannot decode file with encoding {encoding}")
        except Exception as e:
            return ToolResult(success=False, error=str(e))


# ===== T-002: file_write =====

class FileWriteTool(BaseTool):
    name = "file_write"
    description = "Write content to a file. Creates parent directories if needed. Medium risk."
    risk_level = ToolRiskLevel.MEDIUM
    parameters_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the file to write"},
            "content": {"type": "string", "description": "Content to write"},
            "encoding": {"type": "string", "default": "utf-8"},
            "append": {"type": "boolean", "default": False, "description": "Append to file instead of overwriting"},
        },
        "required": ["path", "content"],
    }

    async def execute(self, path: str, content: str, encoding: str = "utf-8",
                      append: bool = False, workspace_root: Optional[str] = None,
                      **context) -> ToolResult:
        # Unified security check
        err = await self._check_path(path, "write", workspace_root)
        if err:
            return ToolResult(success=False, error=err)

        try:
            from pathlib import Path
            file_path = Path(path)
            file_path.parent.mkdir(parents=True, exist_ok=True)

            if append:
                existing = ""
                if file_path.exists():
                    existing = file_path.read_text(encoding=encoding)
                content = existing + content

            file_path.write_text(content, encoding=encoding)

            return ToolResult(
                success=True,
                data={"path": str(file_path), "bytes_written": len(content.encode(encoding))},
                metadata={"tool": "file_write", "path": path, "append": append},
            )
        except Exception as e:
            return ToolResult(success=False, error=str(e))


# ===== T-003: file_list =====

class FileListTool(BaseTool):
    name = "file_list"
    description = "List files and directories in a given path."
    risk_level = ToolRiskLevel.LOW
    parameters_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Directory path to list"},
            "pattern": {"type": "string", "description": "Glob pattern to filter (optional)"},
            "recursive": {"type": "boolean", "default": False},
        },
        "required": ["path"],
    }

    async def execute(self, path: str, pattern: Optional[str] = None,
                      recursive: bool = False, workspace_root: Optional[str] = None,
                      **context) -> ToolResult:
        # Unified security check
        err = await self._check_path(path, "read", workspace_root)
        if err:
            return ToolResult(success=False, error=err)

        try:
            from pathlib import Path
            dir_path = Path(path)
            if not dir_path.exists():
                return ToolResult(success=False, error=f"Directory not found: {path}")
            if not dir_path.is_dir():
                return ToolResult(success=False, error=f"Path is not a directory: {path}")

            if pattern:
                if recursive:
                    items = list(dir_path.rglob(pattern))
                else:
                    items = list(dir_path.glob(pattern))
            else:
                items = list(dir_path.iterdir())

            entries = []
            for item in sorted(items):
                # Skip hidden files
                if any(part.startswith(".") for part in item.relative_to(dir_path).parts):
                    continue
                entries.append({
                    "name": item.name,
                    "path": str(item),
                    "type": "directory" if item.is_dir() else "file",
                    "size": item.stat().st_size if item.is_file() else None,
                })

            return ToolResult(
                success=True,
                data={"entries": entries, "count": len(entries), "path": str(dir_path)},
                metadata={"tool": "file_list", "path": path},
            )
        except Exception as e:
            return ToolResult(success=False, error=str(e))


# ===== T-004: shell_execute =====

class ShellExecuteTool(BaseTool):
    name = "shell_execute"
    description = "Execute a shell command. High risk - requires approval in safe mode."
    risk_level = ToolRiskLevel.HIGH
    parameters_schema = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command to execute"},
            "cwd": {"type": "string", "description": "Working directory (optional)"},
            "timeout": {"type": "integer", "description": "Timeout in seconds (optional)"},
        },
        "required": ["command"],
    }

    async def execute(self, command: str, cwd: Optional[str] = None,
                      timeout: Optional[int] = None, **context) -> ToolResult:
        # Unified command security check
        err = await self._check_command(command)
        if err:
            return ToolResult(success=False, error=err)

        # Timeout from settings (T-012)
        enforcer = await self._get_enforcer()
        max_timeout = enforcer.get_timeout()
        effective_timeout = min(timeout or max_timeout, max_timeout)

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=effective_timeout
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                return ToolResult(
                    success=False,
                    error=f"Command timed out after {effective_timeout}s",
                    metadata={"tool": "shell_execute", "command": command, "timeout": True},
                )

            stdout_str = stdout.decode("utf-8", errors="replace")
            stderr_str = stderr.decode("utf-8", errors="replace")

            return ToolResult(
                success=proc.returncode == 0,
                data={
                    "exit_code": proc.returncode,
                    "stdout": stdout_str[:10000],  # Truncate large outputs
                    "stderr": stderr_str[:5000],
                },
                error=stderr_str[:500] if proc.returncode != 0 else None,
                metadata={"tool": "shell_execute", "command": command, "exit_code": proc.returncode},
            )
        except Exception as e:
            return ToolResult(success=False, error=str(e))


# ===== T-005: web_search =====

class WebSearchTool(BaseTool):
    name = "web_search"
    description = "Search the web for information. Returns search results."
    risk_level = ToolRiskLevel.LOW
    parameters_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "max_results": {"type": "integer", "default": 5, "description": "Maximum number of results"},
        },
        "required": ["query"],
    }

    async def execute(self, query: str, max_results: int = 5, **context) -> ToolResult:
        try:
            import httpx

            results = []

            # Source 1: DuckDuckGo Instant Answer API
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    "https://api.duckduckgo.com/",
                    params={"q": query, "format": "json", "no_html": 1},
                )
                if resp.status_code == 200:
                    data = resp.json()

                    # Abstract (main answer)
                    if data.get("Abstract"):
                        results.append({
                            "title": data.get("Heading", ""),
                            "url": data.get("AbstractURL", ""),
                            "snippet": data.get("Abstract", ""),
                            "source": "duckduckgo_instant",
                        })

                    # Infobox
                    infobox = data.get("Infobox")
                    if infobox and isinstance(infobox, dict) and infobox.get("content"):
                        for item in infobox["content"][:2]:
                            if item.get("value"):
                                results.append({
                                    "title": item.get("label", ""),
                                    "url": "",
                                    "snippet": str(item["value"]),
                                    "source": "duckduckgo_infobox",
                                })

                    # Related topics
                    for topic in data.get("RelatedTopics", [])[:max_results]:
                        if isinstance(topic, dict) and topic.get("Text"):
                            results.append({
                                "title": topic.get("Text", "")[:100],
                                "url": topic.get("FirstURL", ""),
                                "snippet": topic.get("Text", ""),
                                "source": "duckduckgo_related",
                            })
                        elif isinstance(topic, dict) and topic.get("Topics"):
                            for sub in topic["Topics"][:2]:
                                if sub.get("Text"):
                                    results.append({
                                        "title": sub.get("Text", "")[:100],
                                        "url": sub.get("FirstURL", ""),
                                        "snippet": sub.get("Text", ""),
                                        "source": "duckduckgo_related",
                                    })

            if results:
                return ToolResult(
                    success=True,
                    data={"results": results[:max_results], "query": query, "count": len(results[:max_results])},
                    metadata={"tool": "web_search", "query": query},
                )

            # Fallback: return helpful note
            return ToolResult(
                success=True,
                data={
                    "results": [],
                    "query": query,
                    "note": "No instant answers found. The LLM should use its own knowledge to answer.",
                    "suggestion": "Try rephrasing the query or breaking it into simpler terms.",
                },
                metadata={"tool": "web_search", "query": query},
            )
        except httpx.TimeoutException:
            return ToolResult(
                success=True,
                data={"results": [], "query": query, "note": "Search timed out. Using LLM knowledge instead."},
            )
        except Exception as e:
            return ToolResult(success=False, error=f"Web search failed: {str(e)}")


# ===== T-006: send_message =====

class SendMessageTool(BaseTool):
    name = "send_message"
    description = "Send a message to another agent in the session."
    risk_level = ToolRiskLevel.LOW
    parameters_schema = {
        "type": "object",
        "properties": {
            "receiver": {"type": "string", "description": "Name of the target agent"},
            "content": {"type": "string", "description": "Message content"},
            "message_type": {"type": "string", "default": "chat", "description": "Message type"},
        },
        "required": ["receiver", "content"],
    }

    async def execute(self, receiver: str, content: str, message_type: str = "chat",
                      session_id: Optional[str] = None, agent_name: Optional[str] = None,
                      **context) -> ToolResult:
        if not session_id:
            return ToolResult(success=False, error="session_id is required")

        try:
            from app.models.models import Message, MessageType
            from app.services.event_bus import event_bus, Event

            msg_type = MessageType.CHAT if message_type == "chat" else MessageType.SYSTEM

            async with async_session() as db:
                from sqlalchemy import select, func
                result = await db.execute(
                    select(func.max(Message.seq)).where(Message.session_id == session_id)
                )
                max_seq = result.scalar() or 0

                msg = Message(
                    session_type="planning",
                    session_id=session_id,
                    seq=max_seq + 1,
                    sender=agent_name or "tool",
                    receiver=receiver,
                    message_type=msg_type,
                    category="agent_collaboration",
                    content=content,
                )
                db.add(msg)
                await db.commit()
                await db.refresh(msg)

                # Emit SSE event
                event_bus.publish(session_id, Event(
                    event="message",
                    data={
                        "id": msg.id,
                        "seq": msg.seq,
                        "sender": msg.sender,
                        "receiver": msg.receiver,
                        "message_type": msg.message_type.value,
                        "category": msg.category,
                        "content": msg.content,
                        "created_at": msg.created_at.isoformat() if msg.created_at else None,
                    },
                ))

            return ToolResult(
                success=True,
                data={"message_id": msg.id, "seq": msg.seq},
                metadata={"tool": "send_message", "receiver": receiver},
            )
        except Exception as e:
            return ToolResult(success=False, error=str(e))


# ===== T-007: ask_human =====

class AskHumanTool(BaseTool):
    name = "ask_human"
    description = "Ask the human user a question and wait for their response."
    risk_level = ToolRiskLevel.LOW
    parameters_schema = {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "Question to ask the human"},
            "options": {"type": "array", "items": {"type": "string"}, "description": "Optional list of options"},
        },
        "required": ["question"],
    }

    async def execute(self, question: str, options: Optional[List[str]] = None,
                      session_id: Optional[str] = None, agent_name: Optional[str] = None,
                      **context) -> ToolResult:
        if not session_id:
            return ToolResult(success=False, error="session_id is required")

        try:
            from app.services.event_bus import event_bus, Event

            # Post the question as an SSE event
            event_bus.publish(session_id, Event(
                event="ask_human",
                data={
                    "agent": agent_name or "tool",
                    "question": question,
                    "options": options or [],
                    "timestamp": time.time(),
                },
            ))

            # For MVP, we don't implement a full wait-for-response mechanism.
            # Instead, we save the question and return immediately.
            # The human can respond via the normal chat interface.
            from app.models.models import Message, MessageType
            async with async_session() as db:
                from sqlalchemy import select, func
                result = await db.execute(
                    select(func.max(Message.seq)).where(Message.session_id == session_id)
                )
                max_seq = result.scalar() or 0

                content = question
                if options:
                    content += "\n\n选项：\n" + "\n".join(f"{i+1}. {opt}" for i, opt in enumerate(options))

                msg = Message(
                    session_type="planning",
                    session_id=session_id,
                    seq=max_seq + 1,
                    sender=agent_name or "tool",
                    message_type=MessageType.CHAT,
                    category="ask_human",
                    content=content,
                )
                db.add(msg)
                await db.commit()

            return ToolResult(
                success=True,
                data={"question_asked": True, "question": question},
                metadata={"tool": "ask_human", "note": "Question posted. Human can respond via chat."},
            )
        except Exception as e:
            return ToolResult(success=False, error=str(e))


# ===== T-008: file_delete (P1, also needed for P2 Git integration) =====

class FileDeleteTool(BaseTool):
    name = "file_delete"
    description = "Delete a file. Requires confirmation - high risk operation."
    risk_level = ToolRiskLevel.HIGH
    parameters_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the file to delete"},
            "confirm": {"type": "boolean", "description": "Must be true to confirm deletion"},
        },
        "required": ["path", "confirm"],
    }

    async def execute(self, path: str, confirm: bool = False,
                      workspace_root: Optional[str] = None, **context) -> ToolResult:
        if not confirm:
            return ToolResult(success=False, error="Deletion not confirmed. Set confirm=true to proceed.")

        # Unified security check
        err = await self._check_path(path, "write", workspace_root)
        if err:
            return ToolResult(success=False, error=err)

        try:
            file_path = Path(path)
            if not file_path.exists():
                return ToolResult(success=False, error=f"File not found: {path}")
            file_path.unlink()
            return ToolResult(
                success=True,
                data={"deleted": path},
                metadata={"tool": "file_delete", "path": path},
            )
        except Exception as e:
            return ToolResult(success=False, error=str(e))


# ===== T-017: git_command (P2) =====

# Protected branches that cannot be directly pushed to
PROTECTED_BRANCHES = {"main", "master"}
# Git commands that are allowed
ALLOWED_GIT_COMMANDS = {
    "status", "diff", "log", "branch", "show", "remote",
    "add", "stash", "fetch", "pull", "merge",
    "checkout", "switch", "tag",
}
# Git commands that require extra validation
RESTRICTED_GIT_COMMANDS = {
    "commit", "push", "reset", "rebase", "cherry-pick", "revert",
    "clean", "rm", "mv",
}


class GitCommandTool(BaseTool):
    name = "git_command"
    description = "Execute Git operations with branch protection (P2-T-017). High risk - protected branches are enforced."
    risk_level = ToolRiskLevel.HIGH
    parameters_schema = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Git subcommand (status, diff, log, add, commit, push, etc.)"},
            "args": {"type": "string", "description": "Additional arguments for the git command"},
            "cwd": {"type": "string", "description": "Working directory (must be within project)"},
        },
        "required": ["command"],
    }

    async def execute(self, command: str, args: str = "", cwd: Optional[str] = None,
                      workspace_root: Optional[str] = None, **context) -> ToolResult:
        # Validate git subcommand
        cmd_parts = command.strip().split()
        subcmd = cmd_parts[0].lower() if cmd_parts else ""

        if subcmd == "push":
            # P2-G-005: Block force push
            if "--force" in args or "-f " in args:
                return ToolResult(success=False, error="Force push is prohibited (P2-G-005)")

            # P2-G-004: Block direct push to protected branches
            for branch in PROTECTED_BRANCHES:
                if branch in args:
                    return ToolResult(
                        success=False,
                        error=f"Direct push to protected branch '{branch}' is prohibited (P2-G-004). "
                              f"Create a feature branch and use pull request instead."
                    )

        if subcmd == "reset" and "--hard" in args:
            return ToolResult(success=False, error="git reset --hard is prohibited for safety")

        if subcmd not in ALLOWED_GIT_COMMANDS and subcmd not in RESTRICTED_GIT_COMMANDS:
            return ToolResult(success=False, error=f"Git subcommand '{subcmd}' is not allowed")

        # P1-8: LocalWorkspace check
        effective_cwd = cwd or "."
        err = await self._check_workspace_path(effective_cwd, workspace_root)
        if err:
            return ToolResult(success=False, error=err)

        # Build the full git command
        full_cmd = f"git {command}"
        if args:
            full_cmd += f" {args}"

        # Unified command security check
        err = await self._check_command(full_cmd)
        if err:
            return ToolResult(success=False, error=err)

        enforcer = await self._get_enforcer()
        max_timeout = enforcer.get_timeout()

        try:
            proc = await asyncio.create_subprocess_shell(
                full_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=effective_cwd,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=max_timeout
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                return ToolResult(success=False, error=f"Git command timed out after {max_timeout}s")

            stdout_str = stdout.decode("utf-8", errors="replace")
            stderr_str = stderr.decode("utf-8", errors="replace")

            # Git often outputs to stderr even on success
            output = stdout_str
            if stderr_str:
                output += ("\n" if output else "") + stderr_str

            return ToolResult(
                success=proc.returncode == 0,
                data={
                    "exit_code": proc.returncode,
                    "output": output[:10000],
                    "command": full_cmd,
                },
                error=stderr_str[:500] if proc.returncode != 0 else None,
                metadata={"tool": "git_command", "command": full_cmd, "exit_code": proc.returncode},
            )
        except Exception as e:
            return ToolResult(success=False, error=str(e))


# ===== Tool Registry =====

class ToolRegistry:
    """Registry for all available tools."""

    def __init__(self):
        self._tools: Dict[str, BaseTool] = {}
        self._register_builtin_tools()

    def _register_builtin_tools(self):
        """Register all built-in tools."""
        builtin_tools = [
            FileReadTool(),
            FileWriteTool(),
            FileListTool(),
            ShellExecuteTool(),
            WebSearchTool(),
            SendMessageTool(),
            AskHumanTool(),
            GitCommandTool(),
            FileDeleteTool(),
        ]
        for tool in builtin_tools:
            self._tools[tool.name] = tool

    def register_tool(self, tool: BaseTool):
        """P2-T-21: Register an external tool dynamically."""
        self._tools[tool.name] = tool

    def unregister_tool(self, name: str):
        """P2-T-21: Unregister a tool by name."""
        self._tools.pop(name, None)

    def get_tool(self, name: str) -> Optional[BaseTool]:
        return self._tools.get(name)

    def list_tools(self) -> List[dict]:
        return [
            {
                "name": t.name,
                "description": t.description,
                "risk_level": t.risk_level.value,
                "parameters": t.parameters_schema,
            }
            for t in self._tools.values()
        ]

    def get_tools_for_agent(self, allowed_tools: Optional[List[str]] = None) -> List[BaseTool]:
        """Get tools available to an agent based on its allowed_tools list."""
        if not allowed_tools:
            # Return only low-risk tools by default
            return [t for t in self._tools.values() if t.risk_level == ToolRiskLevel.LOW]
        return [self._tools[name] for name in allowed_tools if name in self._tools]


# Global singleton
tool_registry = ToolRegistry()
