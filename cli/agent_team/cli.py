"""Team Agent CLI - Execute plans locally.

Implements:
- X-007: Long output folding with Rich
- CLI-004: Interactive initialization wizard
- CLI-006~008: Debug commands (prompt, messages, replay)
- LLM-powered task execution: each task is executed by an AI agent
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union

import click
import httpx

# X-007: Rich rendering for structured CLI output
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich.syntax import Syntax
    from rich.markdown import Markdown
    from rich.tree import Tree
    from rich import box

    console = Console()
    HAS_RICH = True
except ImportError:
    HAS_RICH = False
    console = None


# ---------------------------------------------------------------------------
# X-007: Rich Rendering Helpers - Long output folding
# ---------------------------------------------------------------------------

def rich_print(text: str, style: Optional[str] = None):
    """Print with Rich if available, fallback to click."""
    if HAS_RICH and console:
        console.print(text, style=style)
    else:
        click.echo(text)


def rich_print_panel(content: str, title: str = "", style: str = "blue"):
    """Print a Rich panel with optional folding for long content."""
    if HAS_RICH and console:
        console.print(Panel(content, title=title, border_style=style))
    else:
        click.echo(f"\n{'='*60}")
        if title:
            click.echo(f"  {title}")
            click.echo(f"{'='*60}")
        click.echo(content)


def rich_print_folded(content: str, title: str = "", max_lines: int = 20, lang: str = ""):
    """Print content with folding support (X-007).

    For long content, shows first max_lines lines and a hint to expand.
    Rich rendering provides syntax highlighting for code blocks.
    """
    lines = content.strip().split("\n")

    if HAS_RICH and console:
        if len(lines) <= max_lines:
            if lang and len(lines) > 3:
                try:
                    syntax = Syntax(content.strip(), lang, theme="monokai", line_numbers=True)
                    console.print(Panel(syntax, title=title, border_style="blue"))
                    return
                except Exception:
                    pass
            console.print(Panel(content.strip(), title=title, border_style="blue"))
        else:
            # Show first max_lines with fold hint
            visible = "\n".join(lines[:max_lines])
            fold_hint = f"\n\n... ({len(lines) - max_lines} more lines, use --verbose to see all)"
            if lang:
                try:
                    syntax = Syntax(visible, lang, theme="monokai", line_numbers=True)
                    console.print(Panel(syntax, title=title, border_style="blue", subtitle=f"[dim]{len(lines)} lines total[/dim]"))
                    console.print(f"[dim]  {len(lines) - max_lines} more lines hidden. Use --verbose to expand.[/dim]")
                    return
                except Exception:
                    pass
            console.print(Panel(visible + fold_hint, title=title, border_style="blue"))
    else:
        # Fallback: simple text with truncation
        click.echo(f"\n{'='*60}")
        if title:
            click.echo(f"  {title}")
            click.echo(f"{'='*60}")
        if len(lines) <= max_lines:
            click.echo(content.strip())
        else:
            for line in lines[:max_lines]:
                click.echo(line)
            click.echo(f"\n  ... ({len(lines) - max_lines} more lines, use --verbose to see all)")


def rich_print_task_table(tasks: list):
    """Print a Rich table of tasks (X-007)."""
    if HAS_RICH and console:
        table = Table(title="Tasks", box=box.ROUNDED)
        table.add_column("#", style="dim", width=4)
        table.add_column("Owner", style="cyan", width=12)
        table.add_column("Title", style="white")
        table.add_column("Risk", width=8)
        table.add_column("Deps", width=8)

        for i, task in enumerate(tasks):
            owner = task.get("owner_role", task.get("assigned_agent", "?"))
            title = task.get("title", f"Task {i+1}")
            risk = task.get("risk_level", "medium")
            deps = task.get("dependencies", [])
            dep_str = ",".join(str(d) for d in deps) if deps else "-"

            risk_style = {"low": "green", "medium": "yellow", "high": "red"}.get(risk, "white")
            table.add_row(str(i+1), owner, title, f"[{risk_style}]{risk}[/{risk_style}]", dep_str)

        console.print(table)
    else:
        # Fallback
        for i, task in enumerate(tasks):
            title = task.get("title", f"Task {i+1}")
            owner = task.get("owner_role", task.get("assigned_agent", "?"))
            risk = task.get("risk_level", "medium")
            click.echo(f"  {i+1}. [{owner}] {title} (risk: {risk})")


def rich_print_result_summary(result: dict, verbose: bool = False):
    """Print execution result summary with Rich (X-007)."""
    if HAS_RICH and console:
        status = result.get("status", "unknown")
        status_style = {
            "completed": "bold green",
            "partial": "bold yellow",
            "failed": "bold red",
        }.get(status, "white")

        # Header panel
        header = Text()
        header.append(f"Execution: {result.get('execution_id', 'unknown')}\n")
        header.append(f"Plan: {result.get('plan_id', 'unknown')}\n")
        header.append(f"Status: ")
        header.append(status.upper(), style=status_style)
        header.append(f"\nProject: {result.get('project_path', 'unknown')}")

        console.print(Panel(header, title="Execution Summary", border_style=status_style))

        # Task results table
        tasks = result.get("tasks", [])
        if tasks:
            table = Table(title="Task Results", box=box.ROUNDED)
            table.add_column("Task", style="white")
            table.add_column("Status", width=12)
            table.add_column("Summary", style="dim")

            for t in tasks:
                t_status = t.get("status", "unknown")
                t_style = {"completed": "green", "failed": "red", "skipped": "dim"}.get(t_status, "white")
                summary = t.get("result_summary", "")
                if not verbose and len(summary) > 80:
                    summary = summary[:80] + "..."
                table.add_row(
                    t.get("title", "?"),
                    f"[{t_style}]{t_status}[/{t_style}]",
                    summary,
                )
            console.print(table)

        # Validation results
        val_results = result.get("validation_results", [])
        if val_results:
            table = Table(title="Validation Results", box=box.ROUNDED)
            table.add_column("Command", style="cyan")
            table.add_column("Status", width=10)
            if verbose:
                table.add_column("Output", style="dim")

            for vr in val_results:
                v_status = vr.get("status", "unknown")
                v_style = {"passed": "green", "failed": "red", "error": "red"}.get(v_status, "white")
                row = [vr.get("command", "?"), f"[{v_style}]{v_status}[/{v_style}]"]
                if verbose:
                    output = vr.get("output", "")
                    row.append(output[:200] if output else "-")
                table.add_row(*row)
            console.print(table)

        if result.get("error_summary"):
            console.print(Panel(result["error_summary"], title="Errors", border_style="red"))
    else:
        # Fallback: use existing show_result logic
        click.echo(f"Execution ID: {result.get('execution_id', 'unknown')}")
        click.echo(f"Plan ID: {result.get('plan_id', 'unknown')}")
        click.echo(f"Status: {result.get('status', 'unknown')}")


# ---------------------------------------------------------------------------
# LLMClient - 调用 LLM 生成代码
# ---------------------------------------------------------------------------

class LLMClient:
    """Simple LLM client for code generation via OpenAI-compatible API."""

    # Agent role -> system prompt mapping
    _OUTPUT_FORMAT_RULES = """

【严格输出规则 - 必须遵守】
1. 你的唯一输出目的是：生成可执行的代码文件。禁止输出纯分析报告、审查报告、规划文档。
2. 你可以先用 1-3 句中文简要说明思路，但随后必须输出完整的代码文件。
3. 每个文件必须使用以下标记包裹（这是自动解析的唯一格式）：
---FILE: 相对路径---
文件完整内容
---END FILE---
4. 代码必须是完整的、可直接运行的，不能省略、不能只有片段、不能用 "// ..." 占位。
5. 如果任务涉及多个文件，每个文件都要单独用 ---FILE ---END FILE 包裹。
6. 禁止只输出 Markdown 说明而没有代码文件。如果你发现自己没有输出任何 ---FILE 标记，请重新生成。"""

    AGENT_PROMPTS = {
        "architect": """你是一个资深软件架构师。你的职责是：
1. 设计项目架构和技术选型
2. 定义数据模型和接口
3. 配置项目基础设施
4. 生成可直接使用的代码文件

请用中文简要说明你的设计决策，然后输出完整的代码。""" + _OUTPUT_FORMAT_RULES,

        "developer": """你是一个高级全栈开发工程师。你的职责是：
1. 实现功能代码
2. 编写组件和逻辑
3. 集成第三方库
4. 确保代码质量和可维护性

请用中文简要说明实现思路，然后输出完整的代码。""" + _OUTPUT_FORMAT_RULES,

        "tester": """你是一个测试工程师。你的职责是：
1. 编写单元测试和集成测试
2. 实现兼容性检测
3. 配置测试环境
4. 确保测试覆盖关键路径

请用中文说明测试策略，然后输出完整的代码。""" + _OUTPUT_FORMAT_RULES,
    }

    DEFAULT_PROMPT = """你是一个全栈开发工程师。请根据任务描述生成代码。
请用中文简要说明实现思路，然后输出完整的代码。""" + _OUTPUT_FORMAT_RULES

    def __init__(self, base_url: str, api_key: str, model: str = "gpt-4o-mini"):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    async def generate(self, messages: list[dict]) -> str:
        """Call the LLM and return the full response text."""
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": messages,
                    "max_tokens": 8192,
                    "temperature": 0.5,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]

    def parse_files(self, text: str) -> list[tuple[str, str]]:
        """Parse generated files from LLM response text.
        
        Returns list of (relative_path, content) tuples.
        """
        files = []
        pattern = r"---FILE:\s*(.+?)---\s*\n(.*?)---END FILE---"
        matches = re.findall(pattern, text, re.DOTALL)
        for path, content in matches:
            files.append((path.strip(), content.strip()))
        
        # Fallback: try ```path format
        if not files:
            pattern2 = r"```(\S+\.\w+)\n(.*?)```"
            matches2 = re.findall(pattern2, text, re.DOTALL)
            for path, content in matches2:
                if "/" in path or path.endswith((".ts", ".tsx", ".js", ".jsx", ".json", ".html", ".css", ".py")):
                    files.append((path.strip(), content.strip()))
        
        return files

    def build_messages(
        self,
        task: dict,
        existing_files: dict[str, str],
        plan_context: str = "",
    ) -> list[dict]:
        """Build LLM messages for a task."""
        agent_role = task.get("assigned_agent", task.get("owner_role", "developer"))
        system_prompt = self.AGENT_PROMPTS.get(agent_role, self.DEFAULT_PROMPT)

        parts = [f"## 任务\n标题：{task.get('title', 'Untitled')}\n"]
        if task.get("description"):
            parts.append(f"描述：{task['description']}")
        if task.get("target_paths"):
            parts.append(f"目标文件：{', '.join(task['target_paths'])}")
        if task.get("validation_commands"):
            parts.append(f"验证命令：{', '.join(task['validation_commands'])}")
        if task.get("steps"):
            parts.append(f"执行步骤：{' → '.join(task['steps'])}")

        # Add existing file context
        if existing_files:
            parts.append("\n## 项目中已有的相关文件\n")
            for path, content in existing_files.items():
                # Truncate very long files
                if len(content) > 2000:
                    content = content[:2000] + "\n... (truncated)"
                parts.append(f"### {path}\n```\n{content}\n```\n")

        if plan_context:
            parts.append(f"\n## 整体方案上下文\n{plan_context}")

        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "\n".join(parts)},
        ]


# ---------------------------------------------------------------------------
# WorkspacePolicy - 安全策略
# ---------------------------------------------------------------------------

class WorkspacePolicy:
    """Security policy for local workspace operations."""

    # 命令黑名单模式
    COMMAND_BLACKLIST = [
        r"rm\s+-rf\s+/",
        r"mkfs",
        r"dd\s+if=",
        r":\(\)\{:\|:&\};:",
        r"chmod\s+777\s+/",
        r"sudo\s+rm",
        r">\s*/dev/sd",
        r"wget\s+.*\|\s*sh",
        r"curl\s+.*\|\s*sh",
        r"shutdown",
        r"reboot",
        r"init\s+[06]",
        r"systemctl\s+(stop|disable|mask)",
        r"pip\s+install\s+--user",
    ]

    # 禁止访问的系统路径
    FORBIDDEN_PATH_PREFIXES = [
        "/etc",
        "/root",
        "/var/log",
        "/boot",
        "/proc",
        "/sys",
        "/dev",
    ]

    # 敏感文件模式
    SENSITIVE_FILE_PATTERNS = [
        ".env",
        "*.key",
        "*.pem",
        "id_rsa*",
        "id_ed25519*",
        "id_ecdsa*",
        ".gitconfig",
        ".npmrc",
        ".pypirc",
        ".netrc",
        "*.p12",
        "*.pfx",
        ".aws/credentials",
        ".ssh/config",
        ".ssh/known_hosts",
    ]

    # safe_mode 下允许的命令前缀
    SAFE_COMMAND_PREFIXES = [
        "git status",
        "git diff",
        "git log",
        "git branch",
        "git show",
        "ls",
        "cat",
        "head",
        "tail",
        "find",
        "grep",
        "wc",
        "python -m pytest",
        "pytest",
        "npm test",
        "npm run lint",
        "npm run typecheck",
        "yarn test",
        "yarn lint",
        "eslint",
        "mypy",
        "ruff",
        "flake8",
        "pylint",
        "black --check",
        "isort --check",
        "tsc --noEmit",
        "cargo test",
        "cargo check",
        "go test",
        "go vet",
    ]

    def __init__(self, project_path: Path, safe_mode: bool = False):
        self.project_path = project_path.resolve()
        self.safe_mode = safe_mode

    def check_path(self, path: Path) -> tuple:
        """检查路径是否允许访问。返回 (allowed, reason)。"""
        resolved = path.resolve()

        # 检查系统禁止路径
        for prefix in self.FORBIDDEN_PATH_PREFIXES:
            if str(resolved).startswith(prefix):
                return False, f"Access to system path forbidden: {prefix}"

        # 检查 home 目录下的敏感路径
        home = Path.home()
        home_sensitive = [home / ".ssh", home / ".aws", home / ".gnupg"]
        for sensitive_path in home_sensitive:
            try:
                resolved.relative_to(sensitive_path)
                return False, f"Access to sensitive directory forbidden: {sensitive_path}"
            except ValueError:
                pass

        # 检查是否在项目目录内
        try:
            resolved.relative_to(self.project_path)
        except ValueError:
            return False, f"Path is outside project directory: {self.project_path}"

        return True, ""

    def is_sensitive_file(self, path: Path) -> bool:
        """检查文件是否为敏感文件。"""
        name = path.name
        # 转换为相对路径字符串用于匹配
        try:
            rel_str = str(path.resolve().relative_to(self.project_path))
        except ValueError:
            rel_str = name

        for pattern in self.SENSITIVE_FILE_PATTERNS:
            if fnmatch.fnmatch(name, pattern):
                return True
            if fnmatch.fnmatch(rel_str, pattern):
                return True
        return False

    def check_command(self, command: str) -> tuple:
        """检查命令是否允许执行。返回 (allowed, reason)。"""
        stripped = command.strip()

        # 检查黑名单
        for pattern in self.COMMAND_BLACKLIST:
            if re.search(pattern, stripped, re.IGNORECASE):
                return False, f"Command matches blocked pattern: {pattern}"

        # safe_mode 下只允许白名单命令
        if self.safe_mode:
            for prefix in self.SAFE_COMMAND_PREFIXES:
                if stripped.startswith(prefix):
                    return True, ""
            return False, f"Safe mode: command not in allowed list. Allowed prefixes: {self.SAFE_COMMAND_PREFIXES[:5]}..."

        return True, ""


# ---------------------------------------------------------------------------
# LocalWorkspace - 文件读写和命令执行的抽象层
# ---------------------------------------------------------------------------

class LocalWorkspace:
    """Local filesystem workspace with security boundaries."""

    def __init__(self, project_path: Path, safe_mode: bool = False):
        self.project_path = project_path.resolve()
        self.policy = WorkspacePolicy(self.project_path, safe_mode=safe_mode)

    def _resolve_and_check(self, path: Union[str, Path]) -> Path:
        """解析路径并检查安全性。"""
        p = Path(path)
        if not p.is_absolute():
            p = self.project_path / p
        resolved = p.resolve()

        allowed, reason = self.policy.check_path(resolved)
        if not allowed:
            raise PermissionError(reason)

        return resolved

    def read_file(self, path: Union[str, Path]) -> str:
        """读取文件内容。"""
        resolved = self._resolve_and_check(path)

        if self.policy.is_sensitive_file(resolved):
            raise PermissionError(f"Reading sensitive file is forbidden: {resolved.name}")

        if not resolved.exists():
            raise FileNotFoundError(f"File not found: {resolved}")

        if not resolved.is_file():
            raise IsADirectoryError(f"Path is a directory: {resolved}")

        return resolved.read_text(encoding="utf-8")

    def write_file(self, path: Union[str, Path], content: str) -> None:
        """写入文件内容。"""
        resolved = self._resolve_and_check(path)

        if self.policy.is_sensitive_file(resolved):
            raise PermissionError(f"Writing sensitive file is forbidden: {resolved.name}")

        # 确保父目录存在
        resolved.parent.mkdir(parents=True, exist_ok=True)

        resolved.write_text(content, encoding="utf-8")

    def list_dir(self, path: Union[str, Path] = "") -> list:
        """列出目录内容。返回文件/目录信息列表。"""
        if path:
            resolved = self._resolve_and_check(path)
        else:
            resolved = self.project_path

        if not resolved.exists():
            raise FileNotFoundError(f"Directory not found: {resolved}")

        if not resolved.is_dir():
            raise NotADirectoryError(f"Path is not a directory: {resolved}")

        items = []
        for entry in sorted(resolved.iterdir()):
            # 跳过隐藏文件
            if entry.name.startswith("."):
                continue
            items.append({
                "name": entry.name,
                "type": "directory" if entry.is_dir() else "file",
                "size": entry.stat().st_size if entry.is_file() else None,
            })
        return items

    def exists(self, path: Union[str, Path]) -> bool:
        """检查路径是否存在。"""
        try:
            resolved = self._resolve_and_check(path)
        except PermissionError:
            return False
        return resolved.exists()

    def run_command(
        self, cmd: str, cwd: Optional[str] = None, timeout: int = 300
    ) -> tuple:
        """执行命令。返回 (exit_code, stdout, stderr)。"""
        allowed, reason = self.policy.check_command(cmd)
        if not allowed:
            raise PermissionError(reason)

        # 确定工作目录
        if cwd:
            work_dir = self._resolve_and_check(cwd)
        else:
            work_dir = self.project_path

        if not work_dir.exists():
            raise FileNotFoundError(f"Working directory not found: {work_dir}")

        try:
            result = subprocess.run(
                cmd,
                shell=True,
                cwd=str(work_dir),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            raise TimeoutError(f"Command timed out after {timeout}s: {cmd}")
        except Exception as e:
            raise RuntimeError(f"Command execution failed: {e}")


# ---------------------------------------------------------------------------
# ExecutionRunner - 逐 task 执行引擎
# ---------------------------------------------------------------------------

class ExecutionRunner:
    """Execute tasks from an execution plan sequentially with LLM-powered code generation."""

    def __init__(
        self,
        plan: dict,
        workspace: LocalWorkspace,
        step_by_step: bool = False,
        safe_mode: bool = False,
        llm_client: Optional[LLMClient] = None,
        plan_context: str = "",
    ):
        self.plan = plan
        self.workspace = workspace
        self.step_by_step = step_by_step
        self.safe_mode = safe_mode
        self.llm_client = llm_client
        self.plan_context = plan_context
        self.execution_id = f"exec_{uuid.uuid4().hex[:12]}"
        self.plan_id = plan.get("plan_id", "unknown")
        self.source_session_id = plan.get("source_session_id", "")
        self.project_path = str(workspace.project_path)
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.task_results: list[dict] = []
        self.completed_task_ids: set[str] = set()
        self.generated_files: dict[str, str] = {}  # Track all generated files

    def _check_dependencies(self, task: dict) -> tuple:
        """检查任务的前置依赖是否已完成。"""
        deps = task.get("dependencies", [])
        if not deps:
            return True, ""

        # 兼容两种格式：list of strings 或 list of dicts
        dep_ids = []
        for dep in deps:
            if isinstance(dep, str):
                dep_ids.append(dep)
            elif isinstance(dep, dict):
                dep_ids.append(dep.get("task_id", ""))

        for dep_id in dep_ids:
            if dep_id not in self.completed_task_ids:
                return False, f"Dependency not completed: {dep_id}"

        return True, ""

    def _execute_single_task(self, task: dict) -> dict:
        """执行单个任务：用 LLM 生成代码 → 写入文件 → 运行验证命令。"""
        task_id = task.get("task_id", f"task_{len(self.task_results)}")
        title = task.get("title", "Untitled Task")
        description = task.get("description", "")
        validation_commands = task.get("validation_commands", [])
        target_paths = task.get("target_paths", [])
        steps = task.get("steps", [])

        task_started = datetime.now(timezone.utc).isoformat()

        click.echo(f"\n{'='*60}")
        click.echo(f"  Task: {title}")
        click.echo(f"  ID: {task_id}")
        click.echo(f"  Owner: {task.get('assigned_agent', task.get('owner_role', 'unknown'))}")
        click.echo(f"  Risk: {task.get('risk_level', 'medium')}")
        if description:
            click.echo(f"  Description: {description}")
        if target_paths:
            click.echo(f"  Target paths: {', '.join(target_paths)}")
        if steps:
            click.echo(f"  Steps: {' → '.join(steps)}")
        click.echo(f"{'='*60}")

        # 依赖检查
        deps_ok, deps_reason = self._check_dependencies(task)
        if not deps_ok:
            click.echo(f"  ⚠ Skipping: {deps_reason}")
            task_finished = datetime.now(timezone.utc).isoformat()
            return {
                "task_id": task_id,
                "title": title,
                "status": "skipped",
                "result_summary": f"Skipped: {deps_reason}",
                "started_at": task_started,
                "finished_at": task_finished,
            }

        # step_by_step 模式：询问确认
        if self.step_by_step:
            choice = click.prompt(
                "\n  Execute this task? (yes/no/skip)",
                type=str,
                default="yes",
            ).strip().lower()

            if choice in ("n", "no"):
                task_finished = datetime.now(timezone.utc).isoformat()
                click.echo("  ✗ Task rejected by user.")
                return {
                    "task_id": task_id,
                    "title": title,
                    "status": "skipped",
                    "result_summary": "Rejected by user",
                    "started_at": task_started,
                    "finished_at": task_finished,
                }
            elif choice in ("s", "skip"):
                task_finished = datetime.now(timezone.utc).isoformat()
                click.echo("  ⊘ Task skipped by user.")
                return {
                    "task_id": task_id,
                    "title": title,
                    "status": "skipped",
                    "result_summary": "Skipped by user",
                    "started_at": task_started,
                    "finished_at": task_finished,
                }

        # 执行任务操作
        operations_log = []
        has_error = False
        validation_failed = False

        # ===== LLM 代码生成 =====
        if self.llm_client:
            click.echo("\n  🤖 Generating code with LLM...")

            # 收集已有文件上下文
            existing_files = {}
            # 1. target_paths 中已存在的文件
            for tp in target_paths:
                try:
                    resolved = self.workspace._resolve_and_check(tp)
                    if resolved.is_dir():
                        click.echo(f"  📁 Target is directory, skipping read: {tp}")
                        continue
                    if self.workspace.exists(tp):
                        content = self.workspace.read_file(tp)
                        existing_files[tp] = content
                        click.echo(f"  📄 Read existing: {tp} ({content.count(chr(10)) + 1} lines)")
                except (PermissionError, FileNotFoundError, IsADirectoryError):
                    pass

            # 2. 之前生成的相关文件
            for path, content in self.generated_files.items():
                if path not in existing_files:
                    existing_files[path] = content

            # 构建 LLM 请求
            messages = self.llm_client.build_messages(task, existing_files, self.plan_context)

            # 调用 LLM（带自动重试：如果首次输出解析不到代码文件，则带修正提示重试一次）
            import asyncio
            MAX_RETRIES = 2  # 最多尝试 2 次
            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    response_text = asyncio.run(self.llm_client.generate(messages))
                    click.echo(f"  ✅ LLM response received ({len(response_text)} chars)")

                    # 解析生成的文件
                    generated = self.llm_client.parse_files(response_text)
                    if generated:
                        for rel_path, content in generated:
                            try:
                                self.workspace.write_file(rel_path, content)
                                self.generated_files[rel_path] = content
                                operations_log.append(f"Generated {rel_path} ({content.count(chr(10)) + 1} lines)")
                                click.echo(f"  📝 Written: {rel_path} ({content.count(chr(10)) + 1} lines)")
                            except PermissionError as e:
                                operations_log.append(f"Write blocked: {rel_path} - {e}")
                                click.echo(f"  🚫 Write blocked: {rel_path} - {e}")
                                has_error = True
                        break  # 成功解析，退出重试循环

                    # 首次解析失败，尝试重试
                    if attempt < MAX_RETRIES:
                        click.echo(f"  ⚠ Attempt {attempt}: Could not parse LLM response into files, retrying with correction hint...")
                        retry_hint = (
                            "你上次的输出中没有包含任何 ---FILE 标记，无法解析为代码文件。"
                            "请检查你的输出——你必须输出完整的代码文件，每个文件用 ---FILE: 相对路径--- 和 ---END FILE--- 包裹。"
                            "不要只输出分析报告或说明文档，必须输出可执行的代码。"
                            "现在请重新生成，确保输出中包含 ---FILE 标记。"
                        )
                        messages.append({"role": "assistant", "content": response_text})
                        messages.append({"role": "user", "content": retry_hint})
                    else:
                        # 最终仍解析失败，fallback 写入原文
                        operations_log.append("LLM response could not be parsed into files after retry")
                        click.echo("  ⚠ Could not parse LLM response into files after retry")
                        if len(target_paths) == 1:
                            try:
                                # Strip markdown code fences if present
                                cleaned = re.sub(r'^```\w*\n?', '', response_text)
                                cleaned = re.sub(r'\n?```$', '', cleaned)
                                self.workspace.write_file(target_paths[0], cleaned)
                                self.generated_files[target_paths[0]] = cleaned
                                operations_log.append(f"Written raw response to {target_paths[0]}")
                                click.echo(f"  📝 Written raw response to: {target_paths[0]}")
                            except PermissionError as e:
                                operations_log.append(f"Write blocked: {target_paths[0]} - {e}")
                                has_error = True
                except Exception as e:
                    operations_log.append(f"LLM call failed: {e}")
                    click.echo(f"  ❌ LLM call failed: {e}")
                    has_error = True
                    break
        else:
            # 无 LLM 时：仅验证模式（旧行为）
            click.echo("\n  ⚠ No LLM configured, running in validation-only mode")
            for tp in target_paths:
                try:
                    resolved = self.workspace._resolve_and_check(tp)
                    if resolved.is_dir():
                        operations_log.append(f"Target is directory: {tp}")
                        click.echo(f"  📁 Target is directory: {tp}")
                        continue
                    if self.workspace.exists(tp):
                        try:
                            content = self.workspace.read_file(tp)
                            lines = content.count("\n") + 1
                            operations_log.append(f"Read {tp} ({lines} lines)")
                            click.echo(f"  📄 Read {tp} ({lines} lines)")
                        except PermissionError as e:
                            operations_log.append(f"Read {tp} blocked: {e}")
                            click.echo(f"  🚫 Read {tp} blocked: {e}")
                    else:
                        operations_log.append(f"Target path not found: {tp}")
                        click.echo(f"  ⚠ Target path not found: {tp}")
                except (PermissionError, IsADirectoryError) as e:
                    operations_log.append(f"Access check for {tp} blocked: {e}")

        # ===== 运行验证命令 =====
        for vc in validation_commands:
            try:
                click.echo(f"  🔧 Running: {vc}")
                exit_code, stdout, stderr = self.workspace.run_command(vc)
                if exit_code == 0:
                    operations_log.append(f"Validation passed: {vc}")
                    click.echo(f"  ✅ Validation passed: {vc}")
                else:
                    operations_log.append(
                        f"Validation failed: {vc} (exit={exit_code})"
                    )
                    click.echo(f"  ❌ Validation failed: {vc} (exit={exit_code})")
                    if stderr.strip():
                        click.echo(f"     stderr: {stderr[:500]}")
                    validation_failed = True
            except PermissionError as e:
                operations_log.append(f"Command blocked: {vc} - {e}")
                click.echo(f"  🚫 Command blocked: {vc} - {e}")
                validation_failed = True
            except TimeoutError as e:
                operations_log.append(f"Command timed out: {vc}")
                click.echo(f"  ⏱ Command timed out: {vc}")
                validation_failed = True
            except Exception as e:
                operations_log.append(f"Command error: {vc} - {e}")
                click.echo(f"  ❌ Command error: {vc} - {e}")
                validation_failed = True

        # 如果没有任何验证命令且生成了文件，标记为已完成
        if not validation_commands and not has_error:
            if self.generated_files or self.llm_client:
                operations_log.append("Task completed (files generated)")
                click.echo("  ✅ Task completed (files generated)")
            else:
                operations_log.append("Task marked as completed (no validation)")
                click.echo("  ✅ Task marked as completed (no validation)")

        task_finished = datetime.now(timezone.utc).isoformat()
        if validation_failed or has_error:
            status = "failed"
        elif self.llm_client and any("Generated" in op or "Written" in op for op in operations_log):
            status = "completed"
        else:
            status = "completed" if not has_error else "failed"
        result_summary = "; ".join(operations_log) if operations_log else "Task executed"

        if status == "completed":
            self.completed_task_ids.add(task_id)
            click.echo(f"\n  ✅ Task completed: {title}")
        else:
            click.echo(f"\n  ❌ Task failed: {title}")

        return {
            "task_id": task_id,
            "title": title,
            "status": status,
            "result_summary": result_summary,
            "started_at": task_started,
            "finished_at": task_finished,
        }

    def run(self) -> dict:
        """执行所有任务，返回 execution_result。"""
        click.echo(f"\n🚀 Starting execution: {self.execution_id}")
        click.echo(f"   Plan: {self.plan.get('title', 'Unknown')}")
        click.echo(f"   Project: {self.project_path}")
        click.echo(f"   Tasks: {len(self.plan.get('tasks', []))}")
        click.echo(f"   Safe mode: {'ON' if self.safe_mode else 'OFF'}")
        click.echo(f"   Step-by-step: {'ON' if self.step_by_step else 'OFF'}")

        tasks = self.plan.get("tasks", [])

        for i, task in enumerate(tasks):
            click.echo(f"\n--- Task {i + 1}/{len(tasks)} ---")
            task_result = self._execute_single_task(task)
            self.task_results.append(task_result)

        # 确定整体状态
        completed_count = sum(1 for t in self.task_results if t["status"] == "completed")
        failed_count = sum(1 for t in self.task_results if t["status"] == "failed")
        skipped_count = sum(1 for t in self.task_results if t["status"] == "skipped")

        if failed_count > 0 and completed_count > 0:
            overall_status = "partial"
        elif failed_count > 0:
            overall_status = "failed"
        elif completed_count == len(tasks):
            overall_status = "completed"
        else:
            overall_status = "partial"

        finished_at = datetime.now(timezone.utc).isoformat()

        # 运行全局验证命令
        validation_results = []
        global_validations = self.plan.get("validation_commands", [])
        for vc in global_validations:
            try:
                exit_code, stdout, stderr = self.workspace.run_command(vc)
                validation_results.append({
                    "command": vc,
                    "status": "passed" if exit_code == 0 else "failed",
                    "exit_code": exit_code,
                    "output": (stdout + stderr)[:1000],
                })
            except Exception as e:
                validation_results.append({
                    "command": vc,
                    "status": "error",
                    "exit_code": -1,
                    "output": str(e),
                })

        error_summary = None
        if failed_count > 0:
            failed_titles = [
                t["title"] for t in self.task_results if t["status"] == "failed"
            ]
            error_summary = f"{failed_count} task(s) failed: {', '.join(failed_titles)}"

        result = {
            "execution_id": self.execution_id,
            "plan_id": self.plan_id,
            "source_session_id": self.source_session_id,
            "status": overall_status,
            "project_path": self.project_path,
            "started_at": self.started_at,
            "finished_at": finished_at,
            "tasks": self.task_results,
            "validation_results": validation_results,
            "error_summary": error_summary,
        }

        # 打印摘要
        click.echo(f"\n{'='*60}")
        click.echo(f"  Execution Summary")
        click.echo(f"{'='*60}")
        click.echo(f"  Status: {overall_status}")
        click.echo(f"  Completed: {completed_count}/{len(tasks)}")
        click.echo(f"  Failed: {failed_count}/{len(tasks)}")
        click.echo(f"  Skipped: {skipped_count}/{len(tasks)}")
        if validation_results:
            click.echo(f"  Global validations: {len(validation_results)}")
        if error_summary:
            click.echo(f"  Errors: {error_summary}")
        click.echo(f"{'='*60}")

        return result


# ---------------------------------------------------------------------------
# Helper: resolve LLM config from server or env
# ---------------------------------------------------------------------------

def _resolve_llm_config(server: str, llm_api_key: Optional[str], llm_base_url: str, llm_model: str, token: str = "") -> tuple:
    """Resolve LLM configuration. Returns (api_key, base_url, model, source_description).

    Resolution order:
    1. CLI args / env vars (explicit --llm-api-key)
    2. Local config file (.agent-team.json)
    3. Server /api/settings/llm-config (auto-fetch with auth token)
    """
    if llm_api_key:
        return llm_api_key, llm_base_url, llm_model, f"env: {llm_model} @ {llm_base_url}"
    # Try local config file
    for config_path in [Path.cwd() / ".agent-team.json", Path.home() / ".agent-team.json"]:
        if config_path.exists():
            try:
                cfg = json.loads(config_path.read_text(encoding="utf-8"))
                if cfg.get("llm_api_key"):
                    return (cfg["llm_api_key"], cfg.get("llm_base_url", llm_base_url),
                            cfg.get("llm_model", llm_model), f"config ({config_path.name})")
            except Exception:
                pass
    # Try server llm-config endpoint (returns decrypted api_key with auth)
    if token:
        try:
            import urllib.request
            url = f"{server}/api/settings/llm-config"
            headers = {"Authorization": f"Bearer {token}"}
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("configured") and data.get("api_key"):
                    return (data["api_key"], data.get("base_url", llm_base_url),
                            data.get("model", llm_model),
                            f"server: {data.get('provider_name', '?')} / {data.get('model', llm_model)}")
        except Exception:
            pass
    return None, llm_base_url, llm_model, ""


def _interactive_llm_setup(base_url: str, model: str) -> tuple:
    """Interactive prompt to collect LLM API key. Returns (api_key, base_url, model)."""
    rich_print_panel(
        "执行计划需要 LLM 来生成代码，请配置：\n\n"
        "常见配置：\n"
        "  • OpenAI:     base_url=https://api.openai.com/v1  model=gpt-4o-mini\n"
        "  • DeepSeek:   base_url=https://api.deepseek.com   model=deepseek-chat\n"
        "  • 硅基流动:   base_url=https://api.siliconflow.cn  model=Qwen/Qwen2.5-7B-Instruct\n"
        "  • Moonshot:   base_url=https://api.moonshot.cn    model=moonshot-v1-8k\n"
        "  • 本地 Ollama: base_url=http://localhost:11434    model=llama3\n",
        title="LLM 配置",
    )
    api_key = click.prompt("  API Key", default="", show_default=False)
    if not api_key:
        api_key = os.environ.get("LLM_API_KEY", "")
    new_base_url = click.prompt("  Base URL", default=base_url)
    new_model = click.prompt("  Model", default=model)
    save = click.confirm("  保存配置到当前目录 (.agent-team.json)？", default=True)
    if save and api_key:
        config_path = Path.cwd() / ".agent-team.json"
        cfg = {}
        if config_path.exists():
            try: cfg = json.loads(config_path.read_text(encoding="utf-8"))
            except Exception: pass
        cfg.update({"llm_api_key": api_key, "llm_base_url": new_base_url, "llm_model": new_model,
                     "updated_at": datetime.now(timezone.utc).isoformat()})
        config_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        click.echo(f"  ✅ 配置已保存到 {config_path}")
    return api_key, new_base_url, new_model


def _resolve_token(token: Optional[str] = None) -> str:
    """Resolve auth token from arg, env, or config file. Returns empty string if not found."""
    if token:
        return token
    env_token = os.environ.get("AGENT_TEAM_TOKEN", "")
    if env_token:
        return env_token
    for config_path in [Path.cwd() / ".agent-team.json", Path.home() / ".agent-team.json"]:
        if config_path.exists():
            try:
                cfg = json.loads(config_path.read_text(encoding="utf-8"))
                if cfg.get("token"):
                    return cfg["token"]
            except Exception:
                pass
    return ""


def _make_request(url: str, token: str = "", method: str = "GET", data: Optional[bytes] = None, timeout: int = 30):
    """Make an HTTP request with optional auth token. Returns (status_code, body_bytes) or raises."""
    import urllib.request
    import urllib.error
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read()


def _fetch_plan_from_server(plan_id: str, server: str, token: str = "") -> Optional[dict]:
    """Fetch execution plan from server. Returns plan dict or None."""
    import urllib.request
    import urllib.error
    session_id = plan_id[5:] if plan_id.startswith("plan_") else plan_id
    url = f"{server}/api/planning-sessions/{session_id}/execution-plan"
    try:
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 401:
            click.echo(f"❌ 认证失败：请提供有效的 Token。", err=True)
            click.echo(f"   在 Web 端「设置」页面获取 Token，或使用 --token 参数。", err=True)
        elif e.code == 400:
            click.echo(f"❌ 执行计划尚未就绪（会话可能未审批）。", err=True)
            click.echo(f"   请先在 Web 端审批方案，等待执行计划生成完成。", err=True)
        elif e.code == 404:
            click.echo(f"❌ 找不到会话或执行计划 (ID: {session_id})", err=True)
        else:
            click.echo(f"❌ 服务器返回错误 {e.code}: {e.reason}", err=True)
        return None
    except urllib.error.URLError as e:
        click.echo(f"❌ 无法连接到服务器 {server}: {e.reason}", err=True)
        click.echo(f"   请确保后端服务已启动。", err=True)
        return None
    except Exception as e:
        click.echo(f"❌ 获取计划失败: {e}", err=True)
        return None


# ---------------------------------------------------------------------------
# CLI Commands
# ---------------------------------------------------------------------------

@click.group()
@click.version_option(version="0.1.0")
@click.option("--token", envvar="AGENT_TEAM_TOKEN", help="认证 Token（或在 Web 端「设置」页面获取）")
@click.pass_context
def cli(ctx, token):
    """Team Agent CLI - 在本地执行 AI 生成的执行计划。"""
    ctx.ensure_object(dict)
    ctx.obj["token"] = _resolve_token(token)


@cli.command()
@click.option("--plan-id", required=True, help="执行计划 ID（从 Web 端获取）")
@click.option("--server", default="http://localhost:8200", help="服务器地址")
@click.option("--project", "-p", type=click.Path(), help="项目目录（默认当前目录）")
@click.option("--step-by-step", is_flag=True, help="逐步执行（每个任务前需确认）")
@click.option("--safe-mode", is_flag=True, help="安全模式（仅执行只读操作）")
@click.option("--output", "-o", type=click.Path(), help="输出结果文件路径")
@click.option("--verbose", "-v", is_flag=True, help="显示完整输出")
@click.option("--llm-api-key", envvar="LLM_API_KEY", help="LLM API Key（或设置 LLM_API_KEY 环境变量）")
@click.option("--llm-base-url", envvar="LLM_BASE_URL", default="https://api.openai.com/v1", help="LLM Base URL")
@click.option("--llm-model", envvar="LLM_MODEL", default="gpt-4o-mini", help="LLM 模型名称")
@click.pass_context
def execute(ctx, plan_id, server, project, step_by_step, safe_mode, output, verbose, llm_api_key, llm_base_url, llm_model):
    """一键拉取并执行计划（推荐使用此命令）。

    \b
    流程: 从服务器拉取计划 → 预览任务 → 确认 → LLM 生成代码 → 验证 → 输出结果

    \b
    快速开始:
      1. 在 Web 端创建规划会话 → 审批方案 → 等待执行计划生成
      2. 复制 plan_id，然后运行:
         agent-team execute --plan-id plan_xxxxx --server http://localhost:8200

    \b
    LLM 配置（三选一）:
      1. 环境变量:  export LLM_API_KEY=sk-xxx LLM_BASE_URL=... LLM_MODEL=...
      2. 命令行:    --llm-api-key sk-xxx --llm-base-url https://api.deepseek.com
      3. 交互输入:  不传参数，命令会引导你输入
    """
    # Banner
    token = ctx.obj.get("token", "")
    if HAS_RICH and console:
        console.print(Panel(
            f"[bold]Plan ID:[/bold] {plan_id}\n[bold]Server:[/bold]  {server}",
            title="🚀 Team Agent 执行引擎", border_style="blue",
        ))
    else:
        click.echo(f"\n🚀 Team Agent 执行引擎 | Plan: {plan_id} | Server: {server}")

    # Fetch plan
    click.echo(f"\n📡 正在从服务器拉取执行计划...")
    plan = _fetch_plan_from_server(plan_id, server, token=token)
    if not plan:
        sys.exit(1)

    tasks = plan.get("tasks", [])
    if not tasks:
        click.echo("❌ 执行计划中没有任务。")
        sys.exit(1)

    # Preview
    click.echo(f"\n📋 执行计划: {plan.get('title', 'Unknown')}")
    click.echo(f"   计划 ID: {plan.get('plan_id', 'unknown')} | 任务数: {len(tasks)} | 类型: {plan.get('project_type', 'unknown')}")
    rich_print_task_table(tasks)

    # Project path
    project_path = Path(project) if project else Path.cwd()
    if not project_path.exists():
        project_path.mkdir(parents=True, exist_ok=True)
        click.echo(f"📁 已创建项目目录: {project_path}")

    # Resolve LLM
    click.echo(f"\n🤖 检查 LLM 配置...")
    api_key, base_url, model, source = _resolve_llm_config(server, llm_api_key, llm_base_url, llm_model, token=token)
    if api_key:
        click.echo(f"   ✅ LLM 已配置: {model} @ {base_url}")
        llm_client = LLMClient(base_url=base_url, api_key=api_key, model=model)
    else:
        click.echo(f"   ⚠️  未找到 LLM API Key")
        if source:
            click.echo(f"   检测到: {source}")
        do_setup = click.confirm("   是否现在配置 LLM？", default=True)
        if do_setup:
            api_key, base_url, model = _interactive_llm_setup(base_url, model)
            if api_key:
                llm_client = LLMClient(base_url=base_url, api_key=api_key, model=model)
                click.echo(f"   ✅ LLM 已配置: {model} @ {base_url}")
            else:
                click.echo(f"   ⚠️  将运行在仅验证模式（不会生成代码）")
                llm_client = None
        else:
            llm_client = None

    # Confirm
    click.echo(f"\n{'─'*50}")
    click.echo(f"  项目目录: {project_path} | 任务: {len(tasks)} | LLM: {'已启用' if llm_client else '未启用'}")
    click.echo(f"{'─'*50}")
    if not click.confirm("  确认开始执行？", default=True):
        click.echo("已取消。")
        return

    # Execute
    plan_context = plan.get("proposal", plan.get("description", ""))
    workspace = LocalWorkspace(project_path, safe_mode=safe_mode)
    runner = ExecutionRunner(plan=plan, workspace=workspace, step_by_step=step_by_step,
                             safe_mode=safe_mode, llm_client=llm_client, plan_context=plan_context)
    result = runner.run()

    # Save result
    output_path = Path(output) if output else project_path / f"execution_result_{result['execution_id']}.json"
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    rich_print_result_summary(result, verbose=verbose)
    click.echo(f"\n📄 结果已保存到: {output_path}")
    click.echo(f"\n💡 后续: agent-team push-result --result-file {output_path} --server {server}")


@cli.command()
@click.argument("plan_file", type=click.Path(exists=True))
@click.option("--project", "-p", type=click.Path(), help="Local project path")
@click.option("--step-by-step", is_flag=True, help="Execute one step at a time with confirmation")
@click.option("--output", "-o", type=click.Path(), help="Output result file path")
@click.option("--safe-mode", is_flag=True, help="Enable safe mode (no destructive operations)")
@click.option("--verbose", "-v", is_flag=True, help="Show full output without folding (X-007)")
@click.option("--llm-api-key", envvar="LLM_API_KEY", help="LLM API key (or set LLM_API_KEY env var)")
@click.option("--llm-base-url", envvar="LLM_BASE_URL", default="https://api.openai.com/v1", help="LLM API base URL")
@click.option("--llm-model", envvar="LLM_MODEL", default="gpt-4o-mini", help="LLM model name")
@click.option("--server", default="http://localhost:8200", help="Server URL to fetch LLM config")
def apply(plan_file, project, step_by_step, output, safe_mode, verbose, llm_api_key, llm_base_url, llm_model, server):
    """从本地 JSON 文件执行计划（推荐使用 execute 命令一键拉取并执行）。"""
    click.echo(f"📂 加载计划文件: {plan_file}")

    # Load and validate the plan
    try:
        with open(plan_file, "r", encoding="utf-8") as f:
            plan = json.load(f)
    except json.JSONDecodeError as e:
        click.echo(f"Error: Invalid JSON in plan file: {e}", err=True)
        sys.exit(1)

    # Validate required fields
    for field in ["plan_id", "title", "tasks"]:
        if field not in plan:
            click.echo(f"❌ 缺少必要字段 '{field}'", err=True)
            sys.exit(1)

    click.echo(f"📋 计划: {plan['title']} | 任务: {len(plan.get('tasks', []))}")

    if not plan.get("tasks"):
        click.echo("❌ 计划中没有任务。")
        return

    project_path = Path(project) if project else Path.cwd()
    if not project_path.exists():
        project_path.mkdir(parents=True, exist_ok=True)
        click.echo(f"📁 已创建项目目录: {project_path}")

    rich_print_task_table(plan["tasks"])

    # Resolve LLM
    api_key, base_url, model, _ = _resolve_llm_config(server, llm_api_key, llm_base_url, llm_model, token="")
    if api_key:
        llm_client = LLMClient(base_url=base_url, api_key=api_key, model=model)
        click.echo(f"🤖 LLM 已启用: {model} @ {base_url}")
    else:
        click.echo("⚠️  未配置 LLM API Key，运行在仅验证模式。")
        click.echo("   使用 --llm-api-key 或设置 LLM_API_KEY 环境变量来启用代码生成。")
        llm_client = None

    # Build plan context from proposal
    plan_context = plan.get("proposal", plan.get("description", ""))

    # Create workspace and runner
    workspace = LocalWorkspace(project_path, safe_mode=safe_mode)
    runner = ExecutionRunner(
        plan=plan,
        workspace=workspace,
        step_by_step=step_by_step,
        safe_mode=safe_mode,
        llm_client=llm_client,
        plan_context=plan_context,
    )

    # Execute
    result = runner.run()

    # Save execution result
    if output:
        output_path = Path(output)
    else:
        # Default output path: same directory as plan file
        plan_dir = Path(plan_file).parent
        output_path = plan_dir / f"execution_result_{result['execution_id']}.json"

    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # X-007: Rich result summary
    rich_print_result_summary(result, verbose=verbose)
    click.echo(f"\nResult saved to: {output_path}")


@cli.command("pull-plan")
@click.option("--plan-id", required=True, help="Plan ID to pull")
@click.option("--server", default="http://localhost:8200", help="服务器地址")
@click.option("--output", "-o", type=click.Path(), default="execution_plan.json", help="输出文件路径")
@click.pass_context
def pull_plan(ctx, plan_id, server, output):
    """从服务器拉取执行计划到本地。"""
    token = ctx.obj.get("token", "")
    click.echo(f"📡 正在拉取执行计划 (ID: {plan_id})...")

    plan = _fetch_plan_from_server(plan_id, server, token=token)
    if not plan:
        sys.exit(1)

    output_path = Path(output)
    output_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

    tasks = plan.get("tasks", [])
    click.echo(f"\n✅ 执行计划已保存到: {output_path}")
    click.echo(f"   标题: {plan.get('title', 'unknown')} | 任务: {len(tasks)} 个")

    if tasks:
        rich_print_task_table(tasks)

    click.echo(f"\n💡 执行方式:")
    click.echo(f"   agent-team apply {output_path}")
    click.echo(f"   # 或一键执行:")
    click.echo(f"   agent-team execute --plan-id {plan_id} --server {server}")


@cli.command("debug")
@click.argument("subcommand", type=click.Choice(["prompt", "messages", "replay", "timeline"]))
@click.option("--session-id", help="Session ID")
@click.option("--plan-file", type=click.Path(exists=True), help="Plan file for replay")
@click.option("--limit", default=10, help="Number of messages to show")
def debug(subcommand, session_id, plan_file, limit):
    """Debug tools: prompt, messages, replay, timeline."""
    if subcommand == "prompt":
        _debug_prompt(session_id)
    elif subcommand == "messages":
        _debug_messages(session_id, limit)
    elif subcommand == "replay":
        _debug_replay(plan_file)
    elif subcommand == "timeline":
        _debug_timeline(session_id)


def _debug_prompt(session_id):
    """Show the final prompt that would be sent to the LLM."""
    if not session_id:
        click.echo("Error: --session-id is required", err=True)
        return

    import urllib.request
    import urllib.error

    try:
        url = f"http://localhost:8000/api/planning-sessions/{session_id}/messages?limit=200"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            messages = json.loads(resp.read().decode("utf-8"))

        click.echo(f"=== Prompt for session {session_id} ===\n")
        for msg in messages:
            role = msg.get("sender", "unknown")
            content = msg.get("content", "")
            msg_type = msg.get("message_type", "chat")
            click.echo(f"[{role}] ({msg_type}):")
            click.echo(f"  {content[:500]}")
            click.echo()

        click.echo(f"Total messages: {len(messages)}")
    except Exception as e:
        click.echo(f"Error: {e}", err=True)


def _debug_messages(session_id, limit):
    """Show recent messages for a session."""
    if not session_id:
        click.echo("Error: --session-id is required", err=True)
        return

    import urllib.request
    import urllib.error

    try:
        url = f"http://localhost:8000/api/planning-sessions/{session_id}/messages?limit={limit}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            messages = json.loads(resp.read().decode("utf-8"))

        click.echo(f"=== Last {limit} messages for session {session_id} ===\n")
        for msg in messages[-limit:]:
            role = msg.get("sender", "unknown")
            content = msg.get("content", "")
            click.echo(f"[{msg.get('seq', '?')}] {role}: {content[:200]}")
            click.echo()
    except Exception as e:
        click.echo(f"Error: {e}", err=True)


def _debug_replay(plan_file):
    """Replay an execution plan step by step (dry run)."""
    if not plan_file:
        click.echo("Error: --plan-file is required", err=True)
        return

    try:
        with open(plan_file, "r", encoding="utf-8") as f:
            plan = json.load(f)
    except json.JSONDecodeError as e:
        click.echo(f"Error: Invalid JSON: {e}", err=True)
        return

    click.echo(f"=== Replay: {plan.get('title', 'Unknown Plan')} ===\n")
    tasks = plan.get("tasks", [])

    for i, task in enumerate(tasks):
        title = task.get("title", f"Task {i + 1}")
        owner = task.get("owner_role", "unknown")
        deps = task.get("dependencies", [])
        target_paths = task.get("target_paths", [])
        validation_commands = task.get("validation_commands", [])

        click.echo(f"--- Task {i + 1}/{len(tasks)} ---")
        click.echo(f"  Title: {title}")
        click.echo(f"  Owner: {owner}")
        click.echo(f"  Dependencies: {deps}")
        click.echo(f"  Target paths: {target_paths}")
        click.echo(f"  Validation commands: {validation_commands}")
        click.echo()

        if i < len(tasks) - 1:
            choice = click.prompt("  Continue? [y]es / [s]kip / [q]uit", type=str, default="y").strip().lower()
            if choice in ("q", "quit"):
                click.echo("Replay stopped.")
                return
            elif choice in ("s", "skip"):
                click.echo("  (skipped)")
                continue

    click.echo("\nReplay complete.")


def _debug_timeline(session_id):
    """P2-CLI-011: Generate a Mermaid sequence diagram from session messages."""
    if not session_id:
        click.echo("Error: --session-id is required", err=True)
        return

    import urllib.request
    import urllib.error

    try:
        url = f"http://localhost:8000/api/planning-sessions/{session_id}/messages?limit=200"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            messages = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        click.echo(f"Error fetching messages: {e}", err=True)
        return

    if not messages:
        click.echo("No messages found for this session.")
        return

    # Generate Mermaid sequence diagram
    participants = set()
    for msg in messages:
        sender = msg.get("sender", "unknown")
        if sender != "system":
            participants.add(sender)

    lines = ["sequenceDiagram"]
    for p in sorted(participants):
        lines.append(f"    participant {p}")

    for msg in messages:
        sender = msg.get("sender", "unknown")
        msg_type = msg.get("message_type", "chat")
        content = msg.get("content", "")[:80].replace('"', "'").replace("\n", " ")

        if sender == "system":
            # System messages as notes
            lines.append(f'    Note over all: {content}')
        elif msg_type == "command":
            # Commands as solid arrows
            lines.append(f'    {sender}->>User: {content}')
        else:
            # Regular messages
            lines.append(f'    {sender}->>all: {content}')

    mermaid = "\n".join(lines)
    click.echo("=== Mermaid Sequence Diagram ===\n")
    click.echo(mermaid)
    click.echo("\n\nView at: https://mermaid.live")
    click.echo("Copy the above diagram and paste it into the Mermaid Live Editor.")


@cli.command("run-validation")
@click.option("--project", "-p", type=click.Path(), required=True, help="Project path")
@click.option("--command", "-c", help="Validation command to run")
def run_validation(project, command):
    """Run validation commands."""
    if not command:
        click.echo("Error: --command is required", err=True)
        sys.exit(1)

    click.echo(f"Running validation in: {project}")
    click.echo(f"Command: {command}")

    workspace = LocalWorkspace(Path(project))

    try:
        exit_code, stdout, stderr = workspace.run_command(command, cwd=".")
        click.echo(f"\nExit code: {exit_code}")
        if stdout:
            click.echo(f"Output:\n{stdout[:2000]}")
        if stderr:
            click.echo(f"Errors:\n{stderr[:2000]}")

        if exit_code == 0:
            click.echo("Validation passed!")
        else:
            click.echo("Validation failed.")
    except PermissionError as e:
        click.echo(f"Error: Command blocked by security policy: {e}", err=True)
    except TimeoutError as e:
        click.echo(f"Error: {e}", err=True)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)


@cli.command("show-result")
@click.argument("result_file", type=click.Path(exists=True))
@click.option("--verbose", "-v", is_flag=True, help="Show full output without folding (X-007)")
def show_result(result_file, verbose):
    """Display an execution result file."""
    try:
        with open(result_file, "r", encoding="utf-8") as f:
            result = json.load(f)
    except json.JSONDecodeError as e:
        click.echo(f"Error: Invalid JSON: {e}", err=True)
        sys.exit(1)

    # X-007: Use Rich rendering
    rich_print_result_summary(result, verbose=verbose)

    if result.get("started_at"):
        rich_print(f"Started: {result['started_at']}", style="dim")
    if result.get("finished_at"):
        rich_print(f"Finished: {result['finished_at']}", style="dim")


@cli.command("push-result")
@click.option("--result-file", required=True, type=click.Path(exists=True), help="执行结果文件路径")
@click.option("--server", default="http://localhost:8200", help="服务器地址")
@click.pass_context
def push_result(ctx, result_file, server):
    """推送执行结果到服务器。"""
    import urllib.request
    import urllib.error

    token = ctx.obj.get("token", "")

    # Load the result file
    try:
        with open(result_file, "r", encoding="utf-8") as f:
            result_data = json.load(f)
    except json.JSONDecodeError as e:
        click.echo(f"Error: Invalid JSON in result file: {e}", err=True)
        sys.exit(1)

    url = f"{server}/api/execution-results"
    click.echo(f"Pushing result to: {url}")
    click.echo(f"Execution ID: {result_data.get('execution_id', 'unknown')}")
    click.echo(f"Plan ID: {result_data.get('plan_id', 'unknown')}")
    click.echo(f"Status: {result_data.get('status', 'unknown')}")

    # POST to server
    payload = json.dumps(result_data, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        url,
        data=payload,
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp_data = json.loads(resp.read().decode("utf-8"))
            click.echo(f"\n✅ Result pushed successfully!")
            if resp_data.get("id"):
                click.echo(f"   Server ID: {resp_data['id']}")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        if e.code == 401:
            click.echo(f"❌ 认证失败：请提供有效的 Token。", err=True)
            click.echo(f"   在 Web 端「设置」页面获取 Token，或使用 --token 参数。", err=True)
        else:
            click.echo(f"Error: Server returned {e.code}: {e.reason}", err=True)
            if body:
                click.echo(f"   Response: {body[:500]}", err=True)
        sys.exit(1)
    except urllib.error.URLError as e:
        click.echo(f"Error: Cannot connect to server: {e.reason}", err=True)
        sys.exit(1)


@cli.command("chat")
@click.option("--session-id", help="接入已有会话 ID")
@click.option("--topic", help="新会话主题")
@click.option("--server", default="http://localhost:8200", help="服务器地址")
@click.option("--project", "-p", type=click.Path(), help="项目目录")
@click.pass_context
def chat_cmd(ctx, session_id, topic, server, project):
    """进入交互对话模式 — 直接跟 Agent 团队聊天。

    \b
    最简用法:
      agent-team chat              # 自动创建/恢复会话，直接聊

    \b
    进入后直接打字就行，跟聊天软件一样。
    输入 /help 看更多命令。
    """
    import urllib.request
    import urllib.error
    import threading

    token = ctx.obj.get("token", "")
    project_path = Path(project) if project else Path.cwd()
    config_path = project_path / ".agent-team.json"

    # --- Helper: HTTP request ---
    def _api(method, path, data=None):
        url = f"{server}/api{path}"
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        body = json.dumps(data, ensure_ascii=False).encode("utf-8") if data else None
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body_text = e.read().decode("utf-8", errors="replace")
            if e.code == 401:
                click.echo("❌ 认证失败：请提供有效的 Token。使用 --token 参数。")
                sys.exit(1)
            return e.code, body_text
        except Exception as e:
            return None, str(e)

    # --- Auto resolve session ---
    # Priority: --session-id > saved config > auto create
    if session_id:
        if session_id.startswith("plan_"):
            session_id = session_id[5:]
    else:
        # Check saved session in config
        if config_path.exists():
            try:
                cfg = json.loads(config_path.read_text(encoding="utf-8"))
                saved_id = cfg.get("chat_session_id", "")
                if saved_id:
                    # Verify session still exists
                    status, _ = _api("GET", f"/planning-sessions/{saved_id}/messages?limit=1")
                    if status == 200:
                        session_id = saved_id
            except Exception:
                pass

        if not session_id:
            # Auto create a new session
            session_topic = topic or f"Chat - {project_path.name}"
            status, data = _api("POST", "/planning-sessions", {"title": session_topic})
            if status not in (200, 201):
                click.echo(f"❌ 无法连接服务器: {data}")
                sys.exit(1)
            session_id = data.get("id", data.get("session_id", ""))

            # Save session id for next time
            try:
                cfg = {}
                if config_path.exists():
                    cfg = json.loads(config_path.read_text(encoding="utf-8"))
                cfg["chat_session_id"] = session_id
                config_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass

    # --- Load existing messages ---
    _, existing = _api("GET", f"/planning-sessions/{session_id}/messages?limit=50")
    if isinstance(existing, list) and existing:
        click.echo(f"  📜 历史消息 ({len(existing)} 条)：")
        for m in existing[-10:]:  # Show last 10
            display = m.get("sender_display", m.get("sender", "?"))
            content = m.get("content", "")
            msg_type = m.get("message_type", "chat")
            if msg_type == "system":
                continue
            preview = content[:120] + ("..." if len(content) > 120 else "")
            click.echo(f"  {display}: {preview}")

    # --- SSE listener thread ---
    last_seq = max((m.get("seq", 0) for m in existing), default=0) if isinstance(existing, list) else 0
    stop_event = threading.Event()
    printing_lock = threading.Lock()

    def _print_agent_msg(sender_display, content, msg_type):
        with printing_lock:
            if msg_type == "system":
                click.echo(f"\n  ⚙ {content[:300]}")
            else:
                lines = content.split("\n")
                click.echo(f"\n  {sender_display}:")
                for line in lines[:30]:
                    click.echo(f"  │ {line}")
                if len(lines) > 30:
                    click.echo(f"  │ ... ({len(lines) - 30} more lines)")

    def _poll_listener():
        """Polling listener — reliable and simple."""
        nonlocal last_seq
        while not stop_event.is_set():
            stop_event.wait(2.0)
            if stop_event.is_set():
                break
            try:
                _, msgs = _api("GET", f"/planning-sessions/{session_id}/messages?limit=20&after_seq={last_seq}")
                if isinstance(msgs, list):
                    for m in msgs:
                        msg_seq = m.get("seq", 0)
                        if msg_seq <= last_seq:
                            continue
                        last_seq = msg_seq
                        display = m.get("sender_display", m.get("sender", "?"))
                        content = m.get("content", "")
                        msg_type = m.get("message_type", "chat")
                        if m.get("sender") != "user":
                            _print_agent_msg(display, content, msg_type)
            except Exception:
                pass

    poll_thread = threading.Thread(target=_poll_listener, daemon=True)
    poll_thread.start()

    # --- Interactive loop ---
    if HAS_RICH and console:
        console.print(Panel(
            f"[bold]会话:[/bold] {session_id}\n[bold]项目:[/bold] {project_path}",
            title="💬 Team Agent Chat", border_style="blue",
        ))
    else:
        click.echo(f"\n{'─'*40}")
        click.echo(f"  💬 Team Agent Chat")
        click.echo(f"  会话: {session_id}")
        click.echo(f"  项目: {project_path}")
        click.echo(f"  打字即聊天，/help 看命令")
        click.echo(f"{'─'*40}\n")

    workspace = LocalWorkspace(project_path, safe_mode=False)

    while True:
        try:
            user_input = input("你> ").strip()
        except (EOFError, KeyboardInterrupt):
            click.echo("\n  👋 再见！")
            break

        if not user_input:
            continue

        # Built-in commands
        if user_input in ("/quit", "/exit", "/q"):
            click.echo("  👋 再见！")
            break
        elif user_input == "/help":
            click.echo("\n  /help      帮助")
            click.echo("  /quit      退出")
            click.echo("  /files     查看项目文件")
            click.echo("  /read <p>  查看文件内容")
            click.echo("  /tasks     查看任务列表")
            click.echo("  /new       新建会话")
            click.echo("  /clear     清屏\n")
            continue
        elif user_input.startswith("/files"):
            try:
                items = workspace.list_dir()
                for item in items:
                    icon = "📁" if item["type"] == "directory" else "📄"
                    click.echo(f"  {icon} {item['name']}")
            except Exception as e:
                click.echo(f"  ❌ {e}")
            continue
        elif user_input.startswith("/read "):
            path = user_input[6:].strip()
            try:
                content = workspace.read_file(path)
                if len(content) > 2000:
                    click.echo(content[:2000] + f"\n... ({len(content)} chars total)")
                else:
                    click.echo(content)
            except Exception as e:
                click.echo(f"  ❌ {e}")
            continue
        elif user_input == "/tasks":
            _, tasks_data = _api("GET", f"/planning-sessions/{session_id}/tasks")
            if isinstance(tasks_data, list) and tasks_data:
                for t in tasks_data:
                    si = {"completed": "✅", "failed": "❌", "in_progress": "🔄"}.get(t.get("status", ""), "⏳")
                    click.echo(f"  {si} {t.get('title', '?')} [{t.get('assigned_agent', '?')}]")
            else:
                click.echo("  暂无任务")
            continue
        elif user_input == "/new":
            session_topic = topic or f"Chat - {project_path.name}"
            status, data = _api("POST", "/planning-sessions", {"title": session_topic})
            if status in (200, 201):
                session_id = data.get("id", data.get("session_id", ""))
                last_seq = 0
                try:
                    cfg = {}
                    if config_path.exists():
                        cfg = json.loads(config_path.read_text(encoding="utf-8"))
                    cfg["chat_session_id"] = session_id
                    config_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
                except Exception:
                    pass
                click.echo(f"  ✅ 新会话: {session_id}")
            else:
                click.echo(f"  ❌ 创建失败: {data}")
            continue
        elif user_input == "/clear":
            click.clear()
            continue

        # Send as chat message
        status, resp = _api("POST", f"/planning-sessions/{session_id}/messages", {
            "content": user_input,
            "sender": "user",
            "message_type": "chat",
        })
        if status != 200:
            click.echo(f"  ❌ 发送失败: {resp}")

    stop_event.set()
    click.echo(f"\n  💡 下次运行 agent-team chat 自动恢复此会话")


@cli.command("init")
@click.option("--server", default="http://localhost:8200", help="服务器地址")
@click.option("--project", "-p", type=click.Path(), help="项目目录")
@click.pass_context
def init_cmd(ctx, server, project):
    """初始化 CLI 工作区并验证服务器连接。"""
    import urllib.request
    import urllib.error

    token = ctx.obj.get("token", "")

    click.echo("🔧 Team Agent CLI 初始化向导")
    click.echo("=" * 40)

    # Step 1: Verify server connectivity
    click.echo(f"\n1. 检查服务器连接: {server}")
    try:
        req = urllib.request.Request(f"{server}/api/health")
        with urllib.request.urlopen(req, timeout=5) as resp:
            health = json.loads(resp.read().decode("utf-8"))
            if health.get("status") == "ok":
                click.echo("   ✅ 服务器连接成功")
            else:
                click.echo("   ⚠️ 服务器返回异常状态")
    except urllib.error.URLError as e:
        click.echo(f"   ❌ 无法连接到服务器: {e.reason}")
        click.echo("   请确保后端服务已启动: uvicorn app.main:app")
        sys.exit(1)

    # Step 1.5: Configure token if not set
    if not token:
        click.echo("\n1b. 配置认证 Token")
        click.echo("   在 Web 端「设置」页面可以获取 Token，用于 CLI 访问服务器 API。")
        input_token = click.prompt("   Token（留空跳过）", default="", show_default=False)
        if input_token.strip():
            token = input_token.strip()

    # Step 2: Check available models
    click.echo("\n2. 检查模型配置")
    try:
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(f"{server}/api/settings/models", headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:
            model_settings = json.loads(resp.read().decode("utf-8"))
            providers = model_settings.get("providers", [])
            configured = [p for p in providers if p.get("has_api_key")]
            if configured:
                click.echo(f"   ✅ 已配置 {len(configured)} 个 LLM Provider")
                for p in configured:
                    click.echo(f"      - {p['display_name']} ({p.get('default_model', 'N/A')})")
            else:
                click.echo("   ⚠️ 没有配置任何 LLM Provider")
                click.echo("   请在 Web 端设置页面配置 API Key")
    except urllib.error.HTTPError as e:
        if e.code == 401:
            click.echo("   ⚠️ Token 无效，无法获取模型配置")
        else:
            click.echo("   ⚠️ 无法获取模型配置")
    except Exception:
        click.echo("   ⚠️ 无法获取模型配置")

    # Step 3: Bind project directory
    click.echo("\n3. 绑定项目目录")
    if project:
        project_path = Path(project).resolve()
    else:
        project_path = Path.cwd()

    if project_path.exists():
        click.echo(f"   ✅ 项目目录: {project_path}")
    else:
        click.echo(f"   ❌ 目录不存在: {project_path}")
        sys.exit(1)

    # Step 3.5: LLM config
    click.echo("\n3b. 配置 LLM（可选）")
    do_llm = click.confirm("   是否配置 LLM API Key？", default=True)
    llm_api_key = ""
    llm_base_url = "https://api.openai.com/v1"
    llm_model = "gpt-4o-mini"
    if do_llm:
        llm_api_key, llm_base_url, llm_model = _interactive_llm_setup(llm_base_url, llm_model)

    # Step 4: Write local config
    click.echo("\n4. 保存本地配置")
    config_file = project_path / ".agent-team.json"
    config = {
        "server_url": server,
        "project_path": str(project_path),
        "llm_base_url": llm_base_url,
        "llm_model": llm_model,
        "initialized_at": datetime.now(timezone.utc).isoformat(),
        "version": "0.1.0",
    }
    if llm_api_key:
        config["llm_api_key"] = llm_api_key
    if token:
        config["token"] = token
    config_file.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    click.echo(f"   ✅ 配置已保存: {config_file}")

    # Step 5: Summary
    click.echo("\n" + "=" * 40)
    click.echo("🎉 初始化完成！")
    click.echo(f"\n服务器: {server}")
    click.echo(f"项目目录: {project_path}")
    click.echo(f"\n常用命令:")
    click.echo(f"  agent-team execute --plan-id <plan_id>              # 一键拉取并执行")
    click.echo(f"  agent-team pull-plan --plan-id <plan_id>            # 拉取执行计划")
    click.echo(f"  agent-team apply <plan_file>                        # 执行本地计划文件")
    click.echo(f"  agent-team push-result --result-file <file>         # 推送结果到服务器")


if __name__ == "__main__":
    cli()
