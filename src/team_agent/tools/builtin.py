"""内置工具 — 文件操作、代码执行等"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from team_agent.tools.registry import tool, get_global_registry


@tool(name="file_read", description="读取文件内容")
async def file_read(path: str) -> str:
    """读取指定路径的文件内容"""
    return Path(path).read_text(encoding="utf-8", errors="replace")


@tool(name="file_write", description="写入文件内容")
async def file_write(path: str, content: str) -> str:
    """将内容写入指定路径的文件"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"Written {len(content)} chars to {path}"


@tool(name="file_list", description="列出目录中的文件")
async def file_list(path: str, pattern: str = "**/*") -> str:
    """列出指定目录下的文件"""
    p = Path(path)
    if not p.exists():
        return f"Path not found: {path}"
    files = [str(f) for f in p.glob(pattern) if f.is_file()]
    return "\n".join(files[:100])  # 限制返回数量


@tool(name="execute", description="执行终端命令")
async def execute(command: str, cwd: str | None = None, timeout: int = 30) -> str:
    """执行终端命令并返回输出"""
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        output = stdout.decode("utf-8", errors="replace")
        if proc.returncode != 0:
            error = stderr.decode("utf-8", errors="replace")
            output += f"\n[EXIT CODE {proc.returncode}]\n{error}"
        return output
    except asyncio.TimeoutError:
        return f"Command timed out after {timeout}s"


@tool(name="web_search", description="搜索网络信息（需要联网）")
async def web_search(query: str) -> str:
    """搜索网络信息"""
    # 占位实现，实际可接入搜索 API
    return f"Search result for: {query} (not implemented yet)"


@tool(name="memory_search", description="搜索历史经验和知识（语义检索）")
async def memory_search(query: str, top_k: int = 3) -> str:
    """从记忆库中搜索与 query 相关的历史经验

    Agent 主动调用此工具检索历史经验，不会自动触发。
    只有在遇到不确定的问题时才需要翻"笔记"。
    """
    # 占位实现，实际接入向量检索
    return f"Memory search results for: {query} (top_k={top_k}, vector search not yet implemented)"


# 注册所有内置工具
def register_builtin_tools() -> None:
    """确保内置工具已注册（导入即注册）"""
    pass
