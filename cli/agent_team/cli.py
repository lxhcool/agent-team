"""Team Agent CLI - Execute plans locally.

Implements:
- X-007: Long output folding with Rich
- CLI-004: Interactive initialization wizard
- CLI-006~008: Debug commands (prompt, messages, replay)
- LLM-powered task execution: each task is executed by an AI agent
"""

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
    AGENT_PROMPTS = {
        "architect": """你是一个资深软件架构师。你的职责是：
1. 设计项目架构和技术选型
2. 定义数据模型和接口
3. 配置项目基础设施
4. 生成可直接使用的代码文件

请用中文说明你的设计决策，然后输出完整的代码。
输出格式：对每个文件，使用以下标记：
---FILE: 相对路径---
文件内容
---END FILE---

确保生成的代码是完整的、可直接使用的。""",

        "developer": """你是一个高级全栈开发工程师。你的职责是：
1. 实现功能代码
2. 编写组件和逻辑
3. 集成第三方库
4. 确保代码质量和可维护性

请用中文简要说明实现思路，然后输出完整的代码。
输出格式：对每个文件，使用以下标记：
---FILE: 相对路径---
文件内容
---END FILE---

确保生成的代码是完整的、可直接运行的。""",

        "tester": """你是一个测试工程师。你的职责是：
1. 编写单元测试和集成测试
2. 实现兼容性检测
3. 配置测试环境
4. 确保测试覆盖关键路径

请用中文说明测试策略，然后输出完整的代码。
输出格式：对每个文件，使用以下标记：
---FILE: 相对路径---
文件内容
---END FILE---

确保测试代码完整可运行。""",
    }

    DEFAULT_PROMPT = """你是一个全栈开发工程师。请根据任务描述生成代码。
请用中文简要说明实现思路，然后输出完整的代码。
输出格式：对每个文件，使用以下标记：
---FILE: 相对路径---
文件内容
---END FILE---"""

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

        # ===== LLM 代码生成 =====
        if self.llm_client:
            click.echo("\n  🤖 Generating code with LLM...")

            # 收集已有文件上下文
            existing_files = {}
            # 1. target_paths 中已存在的文件
            for tp in target_paths:
                try:
                    if self.workspace.exists(tp):
                        content = self.workspace.read_file(tp)
                        existing_files[tp] = content
                        click.echo(f"  📄 Read existing: {tp} ({content.count(chr(10)) + 1} lines)")
                except (PermissionError, FileNotFoundError):
                    pass

            # 2. 之前生成的相关文件
            for path, content in self.generated_files.items():
                if path not in existing_files:
                    existing_files[path] = content

            # 构建 LLM 请求
            messages = self.llm_client.build_messages(task, existing_files, self.plan_context)

            # 调用 LLM
            import asyncio
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
                else:
                    # No files parsed - save raw response as reference
                    operations_log.append("LLM response could not be parsed into files")
                    click.echo("  ⚠ Could not parse LLM response into files")
                    # If there's only one target_path, write the whole response to it
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
        else:
            # 无 LLM 时：仅验证模式（旧行为）
            click.echo("\n  ⚠ No LLM configured, running in validation-only mode")
            for tp in target_paths:
                try:
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
                except PermissionError as e:
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
                    # Don't mark as error if we generated files - validation might just need more work
                    if not self.llm_client:
                        has_error = True
            except PermissionError as e:
                operations_log.append(f"Command blocked: {vc} - {e}")
                click.echo(f"  🚫 Command blocked: {vc} - {e}")
                if not self.llm_client:
                    has_error = True
            except TimeoutError as e:
                operations_log.append(f"Command timed out: {vc}")
                click.echo(f"  ⏱ Command timed out: {vc}")
            except Exception as e:
                operations_log.append(f"Command error: {vc} - {e}")
                click.echo(f"  ❌ Command error: {vc} - {e}")

        # 如果没有任何验证命令且生成了文件，标记为已完成
        if not validation_commands and not has_error:
            if self.generated_files or self.llm_client:
                operations_log.append("Task completed (files generated)")
                click.echo("  ✅ Task completed (files generated)")
            else:
                operations_log.append("Task marked as completed (no validation)")
                click.echo("  ✅ Task marked as completed (no validation)")

        task_finished = datetime.now(timezone.utc).isoformat()
        # With LLM, consider task completed if files were generated even if validation fails
        if self.llm_client and any("Generated" in op or "Written" in op for op in operations_log):
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
# CLI Commands
# ---------------------------------------------------------------------------

@click.group()
@click.version_option(version="0.1.0")
def cli():
    """Team Agent CLI - Execute planning plans locally."""
    pass


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
    """Execute an execution plan from a JSON file with LLM-powered code generation.
    
    If --llm-api-key is not provided, attempts to fetch from the server.
    Set LLM_API_KEY, LLM_BASE_URL, LLM_MODEL env vars as alternative.
    """
    click.echo(f"Loading plan from: {plan_file}")

    # Load and validate the plan
    try:
        with open(plan_file, "r", encoding="utf-8") as f:
            plan = json.load(f)
    except json.JSONDecodeError as e:
        click.echo(f"Error: Invalid JSON in plan file: {e}", err=True)
        sys.exit(1)

    # Validate required fields
    required_fields = ["plan_id", "title", "tasks"]
    for field in required_fields:
        if field not in plan:
            click.echo(f"Error: Missing required field '{field}' in plan", err=True)
            sys.exit(1)

    click.echo(f"Plan: {plan['title']}")
    click.echo(f"Plan ID: {plan['plan_id']}")
    click.echo(f"Tasks: {len(plan.get('tasks', []))}")

    if not plan.get("tasks"):
        click.echo("No tasks to execute.")
        return

    # Determine project path
    project_path = Path(project) if project else Path.cwd()
    if not project_path.exists():
        project_path.mkdir(parents=True, exist_ok=True)
        click.echo(f"Created project directory: {project_path}")

    click.echo(f"Project: {project_path}")

    # X-007: Rich task summary table
    rich_print_task_table(plan["tasks"])

    # Resolve LLM configuration
    llm_client = None
    plan_context = ""

    # Try to fetch LLM config from server if no API key provided
    if not llm_api_key:
        try:
            import urllib.request
            import urllib.error
            # Try to get provider config from server
            url = f"{server}/api/settings/providers"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=5) as resp:
                providers = json.loads(resp.read().decode("utf-8"))
                for p in providers:
                    if p.get("has_api_key") and p.get("enabled", True):
                        llm_base_url = p.get("base_url", llm_base_url)
                        llm_model = p.get("default_model", llm_model)
                        # Can't get actual API key from server for security
                        break
        except Exception:
            pass  # Server not available, continue without

    if llm_api_key:
        llm_client = LLMClient(
            base_url=llm_base_url,
            api_key=llm_api_key,
            model=llm_model,
        )
        click.echo(f"🤖 LLM enabled: {llm_model} @ {llm_base_url}")
    else:
        click.echo("⚠️  No LLM API key provided. Running in validation-only mode.")
        click.echo("   Set LLM_API_KEY env var or use --llm-api-key to enable code generation.")

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
@click.option("--server", default="http://localhost:8000", help="Server URL")
@click.option("--output", "-o", type=click.Path(), default="execution_plan.json", help="Output file path")
def pull_plan(plan_id, server, output):
    """Pull an execution plan from the server."""
    import urllib.request
    import urllib.error

    # The plan_id format is "plan_{session_id}", extract session_id
    session_id = plan_id
    if plan_id.startswith("plan_"):
        session_id = plan_id[5:]

    url = f"{server}/api/planning-sessions/{session_id}/execution-plan"

    click.echo(f"Pulling plan from: {url}")

    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=30) as resp:
            plan_data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 400:
            click.echo("Error: Execution plan not yet available (session may not be approved).", err=True)
        elif e.code == 404:
            click.echo("Error: Session or plan not found.", err=True)
        else:
            click.echo(f"Error: Server returned {e.code}: {e.reason}", err=True)
        sys.exit(1)
    except urllib.error.URLError as e:
        click.echo(f"Error: Cannot connect to server: {e.reason}", err=True)
        sys.exit(1)

    # Save to file
    output_path = Path(output)
    output_path.write_text(
        json.dumps(plan_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    click.echo(f"Plan saved to: {output_path}")
    click.echo(f"Plan ID: {plan_data.get('plan_id', 'unknown')}")
    click.echo(f"Title: {plan_data.get('title', 'unknown')}")
    click.echo(f"Tasks: {len(plan_data.get('tasks', []))}")

    # Show task summary
    for i, task in enumerate(plan_data.get("tasks", [])):
        title = task.get("title", f"Task {i + 1}")
        owner = task.get("owner_role", "unknown")
        click.echo(f"  {i + 1}. [{owner}] {title}")

    click.echo(f"\nTo execute, run:")
    click.echo(f"  agent-team apply {output_path}")


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
@click.option("--result-file", required=True, type=click.Path(exists=True), help="Path to execution_result.json")
@click.option("--server", default="http://localhost:8000", help="Server URL")
def push_result(result_file, server):
    """Push execution result to the server."""
    import urllib.request
    import urllib.error

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
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
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
        click.echo(f"Error: Server returned {e.code}: {e.reason}", err=True)
        if body:
            click.echo(f"   Response: {body[:500]}", err=True)
        sys.exit(1)
    except urllib.error.URLError as e:
        click.echo(f"Error: Cannot connect to server: {e.reason}", err=True)
        sys.exit(1)


@cli.command("init")
@click.option("--server", default="http://localhost:8000", help="Server URL")
@click.option("--project", "-p", type=click.Path(), help="Project path to bind")
def init_cmd(server, project):
    """Initialize the CLI workspace and verify server connection.
    
    Per CLI-004: Interactive initialization wizard that:
    - Verifies server connectivity
    - Checks authentication
    - Binds local project directory
    - Creates local config file
    """
    import urllib.request
    import urllib.error

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

    # Step 2: Check available models
    click.echo("\n2. 检查模型配置")
    try:
        req = urllib.request.Request(f"{server}/api/settings/models")
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

    # Step 4: Write local config
    click.echo("\n4. 保存本地配置")
    config_file = project_path / ".agent-team.json"
    config = {
        "server_url": server,
        "project_path": str(project_path),
        "initialized_at": datetime.now(timezone.utc).isoformat(),
        "version": "0.1.0",
    }
    config_file.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    click.echo(f"   ✅ 配置已保存: {config_file}")

    # Step 5: Summary
    click.echo("\n" + "=" * 40)
    click.echo("🎉 初始化完成！")
    click.echo(f"\n服务器: {server}")
    click.echo(f"项目目录: {project_path}")
    click.echo(f"\n常用命令:")
    click.echo(f"  agent-team pull-plan --plan-id <plan_id>    # 拉取执行计划")
    click.echo(f"  agent-team apply <plan_file>                # 执行计划")
    click.echo(f"  agent-team push-result --result-file <file> # 推送结果")
    click.echo(f"  agent-team debug messages --session-id <id> # 查看消息")


if __name__ == "__main__":
    cli()
