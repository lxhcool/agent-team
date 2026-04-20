"""Workspace 抽象基类 — Agent 操作文件的统一接口"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class ExecuteResult:
    """命令执行结果"""

    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def success(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def to_str(self) -> str:
        parts = []
        if self.stdout:
            parts.append(self.stdout)
        if self.stderr and self.exit_code != 0:
            parts.append(f"[stderr]\n{self.stderr}")
        if self.timed_out:
            parts.append("[TIMEOUT]")
        return "\n".join(parts)


class Workspace(ABC):
    """工作空间抽象基类 — Agent 通过此接口操作文件和执行命令"""

    @abstractmethod
    async def read_file(self, path: str) -> str:
        """读取文件内容"""
        ...

    @abstractmethod
    async def write_file(self, path: str, content: str) -> None:
        """写入文件内容"""
        ...

    @abstractmethod
    async def append_file(self, path: str, content: str) -> None:
        """追加文件内容"""
        ...

    @abstractmethod
    async def delete_file(self, path: str) -> bool:
        """删除文件，返回是否成功"""
        ...

    @abstractmethod
    async def list_files(self, pattern: str = "**/*", path: str = ".") -> list[str]:
        """列出文件"""
        ...

    @abstractmethod
    async def file_exists(self, path: str) -> bool:
        """检查文件是否存在"""
        ...

    @abstractmethod
    async def execute(self, command: str, cwd: str | None = None, timeout: int = 30, env: dict[str, str] | None = None) -> ExecuteResult:
        """执行命令"""
        ...

    @abstractmethod
    async def mkdir(self, path: str) -> None:
        """创建目录"""
        ...

    @abstractmethod
    def get_root(self) -> str:
        """获取工作空间根路径"""
        ...

    @abstractmethod
    async def get_file_info(self, path: str) -> dict[str, Any] | None:
        """获取文件信息（大小、修改时间等）"""
        ...
