"""本地工作空间 — Agent 直接操作本地文件系统"""

from __future__ import annotations

import asyncio
import os
import stat
from datetime import datetime
from pathlib import Path
from typing import Any

from team_agent.workspace.base import ExecuteResult, Workspace


class LocalWorkspace(Workspace):
    """本地工作空间 — CLI 模式下 Agent 直接操作本地文件"""

    def __init__(self, root: str | Path, allowed_commands: list[str] | None = None):
        """
        Args:
            root: 工作空间根路径
            allowed_commands: 允许执行的命令前缀白名单，None 表示允许所有
        """
        self.root = Path(root).resolve()
        self.allowed_commands = allowed_commands

        # 确保根目录存在
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve_path(self, path: str) -> Path:
        """解析路径，确保在工作空间内（防止路径穿越）"""
        p = (self.root / path).resolve()
        # 安全检查：路径必须在 root 内
        if not str(p).startswith(str(self.root)):
            raise PermissionError(f"Path escapes workspace: {path}")
        return p

    def get_root(self) -> str:
        return str(self.root)

    async def read_file(self, path: str) -> str:
        p = self._resolve_path(path)
        if not p.exists():
            raise FileNotFoundError(f"File not found: {path}")
        if p.is_dir():
            raise IsADirectoryError(f"Path is a directory: {path}")
        return p.read_text(encoding="utf-8", errors="replace")

    async def write_file(self, path: str, content: str) -> None:
        p = self._resolve_path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    async def append_file(self, path: str, content: str) -> None:
        p = self._resolve_path(path)
        if not p.exists():
            await self.write_file(path, content)
            return
        existing = p.read_text(encoding="utf-8", errors="replace")
        if not existing.endswith("\n"):
            existing += "\n"
        p.write_text(existing + content, encoding="utf-8")

    async def delete_file(self, path: str) -> bool:
        p = self._resolve_path(path)
        if not p.exists():
            return False
        p.unlink()
        return True

    async def list_files(self, pattern: str = "**/*", path: str = ".") -> list[str]:
        p = self._resolve_path(path)
        if not p.exists():
            return []
        files = []
        for f in p.glob(pattern):
            if f.is_file():
                # 返回相对于 root 的路径
                rel = f.relative_to(self.root)
                files.append(str(rel))
        return sorted(files)

    async def file_exists(self, path: str) -> bool:
        p = self._resolve_path(path)
        return p.exists()

    async def execute(self, command: str, cwd: str | None = None, timeout: int = 30, env: dict[str, str] | None = None) -> ExecuteResult:
        # 安全检查：命令白名单
        if self.allowed_commands is not None:
            cmd_base = command.split()[0] if command.split() else ""
            if not any(cmd_base.startswith(allowed) for allowed in self.allowed_commands):
                return ExecuteResult(
                    exit_code=1,
                    stdout="",
                    stderr=f"Command not allowed: {cmd_base}. Allowed: {self.allowed_commands}",
                )

        work_dir = self._resolve_path(cwd) if cwd else self.root

        exec_env = dict(os.environ)
        if env:
            exec_env.update(env)

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(work_dir),
                env=exec_env,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return ExecuteResult(
                exit_code=proc.returncode or 0,
                stdout=stdout.decode("utf-8", errors="replace"),
                stderr=stderr.decode("utf-8", errors="replace"),
            )
        except asyncio.TimeoutError:
            proc.kill()
            return ExecuteResult(
                exit_code=-1,
                stdout="",
                stderr=f"Command timed out after {timeout}s",
                timed_out=True,
            )
        except Exception as e:
            return ExecuteResult(
                exit_code=1,
                stdout="",
                stderr=str(e),
            )

    async def mkdir(self, path: str) -> None:
        p = self._resolve_path(path)
        p.mkdir(parents=True, exist_ok=True)

    async def get_file_info(self, path: str) -> dict[str, Any] | None:
        p = self._resolve_path(path)
        if not p.exists():
            return None

        stat_result = p.stat()
        return {
            "path": str(p.relative_to(self.root)),
            "size": stat_result.st_size,
            "is_dir": p.is_dir(),
            "is_file": p.is_file(),
            "modified": datetime.fromtimestamp(stat_result.st_mtime).isoformat(),
            "permissions": stat.filemode(stat_result.st_mode),
        }
