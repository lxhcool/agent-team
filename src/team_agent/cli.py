"""CLI 交互 — Click + Rich 终端界面"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from team_agent.config import ProjectConfig

console = Console()


def load_config(config_path: str | None = None) -> ProjectConfig:
    """加载项目配置"""
    if config_path and Path(config_path).exists():
        return ProjectConfig.from_yaml(Path(config_path))

    # 查找默认配置
    for name in ["agent_config.yaml", "agent_config.yml", "config.yaml"]:
        path = Path(name)
        if path.exists():
            return ProjectConfig.from_yaml(path)

    # 使用默认配置
    return _default_config()


def _default_config() -> ProjectConfig:
    """默认配置"""
    from team_agent.config import AgentConfig, ModelConfig, SkillConfig

    return ProjectConfig(
        project_name="team-agent",
        agents=[
            AgentConfig(
                name="coordinator",
                model=ModelConfig.from_string("anthropic/claude-sonnet-4-20250514"),
                system_prompt="你是一个团队协调者，负责统筹 Agent 团队的协作。",
                skills=[SkillConfig(name="planning")],
                max_iterations=20,
            ),
            AgentConfig(
                name="researcher",
                model=ModelConfig.from_string("openai/gpt-4o"),
                system_prompt="你是一个研究专家，擅长信息搜索和知识整理。",
                skills=[SkillConfig(name="web_search")],
            ),
            AgentConfig(
                name="coder",
                model=ModelConfig.from_string("openai/gpt-4o"),
                system_prompt="你是一个高级程序员，擅长代码编写和功能实现。",
                skills=[SkillConfig(name="code_execute"), SkillConfig(name="file_read"), SkillConfig(name="file_write")],
            ),
            AgentConfig(
                name="reviewer",
                model=ModelConfig.from_string("openai/gpt-4o"),
                system_prompt="你是一个代码审查专家，擅长代码审查和质量保证。",
                skills=[SkillConfig(name="code_review"), SkillConfig(name="file_read")],
            ),
        ],
    )


async def run_interactive(config: ProjectConfig, workspace: Path) -> None:
    """交互式 CLI"""
    from team_agent.orchestrator.session import SessionManager

    console.print(Panel.fit(
        "[bold blue]Team Agent[/] — 多 Agent 团队协作框架\n"
        f"工作空间: {workspace}",
        title="Welcome",
    ))

    session_manager = SessionManager(config)

    while True:
        try:
            user_input = Prompt.ask("\n[bold green]You[/]")

            if user_input.lower() in ("exit", "quit", "q"):
                console.print("[dim]Goodbye![/]")
                break

            if user_input.lower() == "status":
                _show_status(session_manager)
                continue

            if user_input.lower() == "agents":
                _show_agents(session_manager)
                continue

            if user_input.lower().startswith("chat "):
                # 单聊模式: chat researcher 你好
                parts = user_input.split(" ", 2)
                if len(parts) >= 3:
                    agent_name = parts[1]
                    message = parts[2]
                    session = await session_manager.create_session("cli_user", message)
                    result = await session_manager.chat_with_agent(session.id, agent_name, message)
                    console.print(Panel(Markdown(result), title=f"[bold]{agent_name}[/]"))
                else:
                    console.print("[red]Usage: chat <agent_name> <message>[/]")
                continue

            if user_input.lower().startswith("roundtable "):
                # 圆桌模式: roundtable researcher,coder 讨论方案
                parts = user_input.split(" ", 2)
                if len(parts) >= 3:
                    agent_names = parts[1].split(",")
                    message = parts[2]
                    session = await session_manager.create_session("cli_user", message)
                    results = await session_manager.roundtable(session.id, agent_names, message)
                    for name, result in results.items():
                        console.print(Panel(Markdown(result), title=f"[bold]{name}[/]"))
                else:
                    console.print("[red]Usage: roundtable <agent1,agent2> <message>[/]")
                continue

            # 默认：任务模式
            session = await session_manager.create_session("cli_user", user_input)
            result = await session_manager.execute_session(session.id, auto_approve=False)

            console.print(Panel(Markdown(result), title="[bold]Plan[/]"))

            # 等待确认
            approval = Prompt.ask("[bold yellow]Approve?[/]", choices=["y", "n", "modify"], default="y")
            if approval == "y":
                result = await session_manager.approve_and_execute(session.id)
                console.print(Panel(Markdown(result), title="[bold]Result[/]"))
            elif approval == "modify":
                modification = Prompt.ask("[bold]Your feedback[/]")
                # 重新规划
                console.print("[dim]Replanning...[/]")

        except KeyboardInterrupt:
            console.print("\n[dim]Interrupted[/]")
            break
        except Exception as e:
            console.print(f"[red]Error: {e}[/]")


def _show_status(session_manager) -> None:
    """显示状态"""
    table = Table(title="System Status")
    table.add_column("Component", style="cyan")
    table.add_column("Status", style="green")

    monitor_status = session_manager.monitor.get_status()
    table.add_row("Agents", str(len(monitor_status.get("agents", {}))))
    table.add_row("Total Traces", str(monitor_status.get("total_traces", 0)))
    table.add_row("Total Tokens", str(monitor_status.get("total_tokens", 0)))

    console.print(table)


def _show_agents(session_manager) -> None:
    """显示 Agent 列表"""
    table = Table(title="Agents")
    table.add_column("Name", style="cyan")
    table.add_column("State", style="green")
    table.add_column("Task", style="yellow")

    monitor_status = session_manager.monitor.get_status()
    for name, info in monitor_status.get("agents", {}).items():
        table.add_row(name, info.get("state", "unknown"), info.get("current_task", "-"))

    console.print(table)


@click.group()
def main():
    """Team Agent — 多 Agent 团队协作框架"""
    pass


@main.command()
@click.option("--workspace", "-w", default=".", help="工作空间路径")
@click.option("--config", "-c", default=None, help="配置文件路径")
def run(workspace: str, config: str | None):
    """启动交互式 Team Agent"""
    ws = Path(workspace).resolve()
    project_config = load_config(config)
    asyncio.run(run_interactive(project_config, ws))


@main.command()
@click.argument("task")
@click.option("--workspace", "-w", default=".", help="工作空间路径")
@click.option("--config", "-c", default=None, help="配置文件路径")
@click.option("--auto-approve", is_flag=True, help="自动审批")
def exec(task: str, workspace: str, config: str | None, auto_approve: bool):
    """执行单个任务"""
    from team_agent.orchestrator.session import SessionManager

    ws = Path(workspace).resolve()
    project_config = load_config(config)
    session_manager = SessionManager(project_config)

    async def _run():
        session = await session_manager.create_session("cli_user", task)
        result = await session_manager.execute_session(session.id, auto_approve=auto_approve)
        console.print(Panel(Markdown(result), title="[bold]Result[/]"))

    asyncio.run(_run())


@main.command()
def init():
    """初始化项目配置"""
    config = _default_config()
    config_path = Path("agent_config.yaml")
    config.to_yaml(config_path)
    console.print(f"[green]Created config file: {config_path}[/]")

    # 创建目录
    for d in ["memory", "skills", "data"]:
        Path(d).mkdir(exist_ok=True)
        console.print(f"[green]Created directory: {d}/[/]")

    console.print("[bold green]Project initialized![/]")


if __name__ == "__main__":
    main()
