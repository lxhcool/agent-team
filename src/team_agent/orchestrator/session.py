"""Session — 会话管理"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from team_agent.agents.base import BaseAgent
from team_agent.config import ProjectConfig
from team_agent.memory.manager import MemoryManager
from team_agent.messaging.bus import MessageBus
from team_agent.orchestrator.monitor import Monitor
from team_agent.orchestrator.planner import Plan, Planner
from team_agent.orchestrator.router import Router
from team_agent.skills.skill_manager import SkillManager
from team_agent.tools.builtin import register_builtin_tools
from team_agent.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class SessionState(str, Enum):
    CREATED = "created"
    PLANNING = "planning"
    EXECUTING = "executing"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Session:
    """会话 — 用户一次任务的完整生命周期"""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    state: SessionState = SessionState.CREATED
    user_id: str = ""
    task: str = ""
    plan: Plan | None = None
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)


class SessionManager:
    """会话管理器 — 管理会话的创建、执行、销毁"""

    def __init__(self, config: ProjectConfig):
        self.config = config
        self.sessions: dict[str, Session] = {}

        # 共享组件
        self.message_bus = MessageBus()
        self.memory = MemoryManager()
        self.skill_manager = SkillManager()
        self.tool_registry = ToolRegistry()
        self.monitor = Monitor()
        self.planner = Planner()
        self.router = Router()

        # Agent 实例
        self._agents: dict[str, BaseAgent] = {}

        # 初始化
        self._init_components()

    def _init_components(self) -> None:
        """初始化组件"""
        # 注册内置工具
        register_builtin_tools()

        # 加载 Skills
        self.skill_manager.load_all()

        # 创建配置中的 Agent
        for agent_config in self.config.agents:
            from team_agent.llm.factory import create_llm
            llm = create_llm(agent_config.model)
            from team_agent.agents.base import BaseAgent

            # 根据 Agent 名称选择类型
            agent_cls = self._get_agent_class(agent_config.name)
            agent = agent_cls(
                config=agent_config,
                llm=llm,
                message_bus=self.message_bus,
                memory=self.memory,
                skill_manager=self.skill_manager,
                tool_registry=self.tool_registry,
            )
            self._agents[agent_config.name] = agent
            self.router.register_agent(agent)
            self.monitor.register_agent(agent.name)

        # 设置 Leader
        if "coordinator" in self._agents:
            self.message_bus.set_leader("coordinator")

    def _get_agent_class(self, name: str) -> type[BaseAgent]:
        """根据名称获取 Agent 类"""
        from team_agent.agents.researcher import ResearcherAgent
        from team_agent.agents.coder import CoderAgent
        from team_agent.agents.reviewer import ReviewerAgent
        from team_agent.agents.coordinator import CoordinatorAgent
        from team_agent.agents.base import BaseAgent

        agent_map = {
            "researcher": ResearcherAgent,
            "coder": CoderAgent,
            "reviewer": ReviewerAgent,
            "coordinator": CoordinatorAgent,
        }
        return agent_map.get(name, CoordinatorAgent)  # 默认用 Coordinator

    async def create_session(self, user_id: str, task: str) -> Session:
        """创建新会话"""
        session = Session(user_id=user_id, task=task)
        self.sessions[session.id] = session

        # 启动所有 Agent
        for agent in self._agents.values():
            await agent.start(session.id)

        # 启动监控
        await self.monitor.start_monitoring()

        logger.info(f"Session created: {session.id} for user {user_id}")
        return session

    async def execute_session(self, session_id: str, auto_approve: bool = False) -> str:
        """执行会话"""
        session = self.sessions.get(session_id)
        if not session:
            return "Session not found"

        try:
            # 1. 规划
            session.state = SessionState.PLANNING
            agent_roles = {name: agent.get_role_description() for name, agent in self._agents.items()}
            session.plan = await self.planner.plan(session.task, agent_roles)

            # 2. 等待审批
            if not auto_approve:
                session.state = SessionState.WAITING_APPROVAL
                return self._format_plan(session.plan)

            # 3. 执行
            return await self._execute_plan(session)

        except Exception as e:
            session.state = SessionState.FAILED
            logger.error(f"Session {session_id} failed: {e}")
            return f"Session failed: {e}"

    async def approve_and_execute(self, session_id: str) -> str:
        """审批通过并执行"""
        session = self.sessions.get(session_id)
        if not session or session.state != SessionState.WAITING_APPROVAL:
            return "Session not found or not waiting for approval"

        return await self._execute_plan(session)

    async def _execute_plan(self, session: Session) -> str:
        """执行计划"""
        session.state = SessionState.EXECUTING

        if not session.plan:
            return "No plan to execute"

        # 按依赖顺序执行步骤
        results = []
        for step in session.plan.steps:
            if step.dependencies:
                # 等待依赖完成（简化实现：顺序执行）
                pass

            success = await self.router.assign_task(step, self.message_bus)
            if not success:
                step.status = "failed"
                results.append(f"Step {step.id} failed: could not assign to {step.agent}")
                continue

            # 等待完成（简化实现，实际应该用事件）
            import asyncio
            await asyncio.sleep(1)  # 给 Agent 时间处理

        session.state = SessionState.COMPLETED
        return self._format_results(session)

    async def destroy_session(self, session_id: str) -> None:
        """销毁会话"""
        session = self.sessions.pop(session_id, None)
        if session:
            # 停止所有 Agent
            for agent in self._agents.values():
                await agent.stop()

            # 停止监控
            await self.monitor.stop_monitoring()

            # 归档记忆
            if session.plan:
                await self.memory.save_task_record(
                    session_id, "session", "coordinator",
                    "session_complete", session.task, "completed",
                )

            logger.info(f"Session destroyed: {session_id}")

    def _format_plan(self, plan: Plan) -> str:
        """格式化计划输出"""
        lines = [f"## 需求理解\n{plan.understanding}\n"]
        lines.append("## 执行计划")
        for step in plan.steps:
            lines.append(f"- **{step.id}** ({step.agent}): {step.description}")
        if plan.risks:
            lines.append("\n## 风险点")
            for risk in plan.risks:
                lines.append(f"- {risk}")
        lines.append("\n---\n输入 `approve` 确认执行，或提出修改意见。")
        return "\n".join(lines)

    def _format_results(self, session: Session) -> str:
        """格式化结果输出"""
        lines = [f"## 任务完成\n"]
        if session.plan:
            for step in session.plan.steps:
                icon = "✅" if step.status == "completed" else "❌" if step.status == "failed" else "⏳"
                lines.append(f"{icon} **{step.id}** ({step.agent}): {step.status}")
        return "\n".join(lines)

    def get_session_status(self, session_id: str) -> dict[str, Any] | None:
        """获取会话状态"""
        session = self.sessions.get(session_id)
        if not session:
            return None
        return {
            "id": session.id,
            "state": session.state.value,
            "task": session.task,
            "created_at": session.created_at.isoformat(),
            "monitor": self.monitor.get_status(),
        }

    async def chat_with_agent(self, session_id: str, agent_name: str, message: str) -> str:
        """直接与指定 Agent 对话（单聊模式）"""
        agent = self._agents.get(agent_name)
        if not agent:
            return f"Agent not found: {agent_name}"

        if agent._session_id != session_id:
            await agent.start(session_id)

        return await agent.chat(message)

    async def roundtable(
        self,
        session_id: str,
        agent_names: list[str],
        message: str,
        max_rounds: int = 5,
        consensus_check: bool = False,
    ) -> dict[str, list[str]]:
        """圆桌讨论模式 — 支持收敛策略

        Args:
            session_id: 会话 ID
            agent_names: 参与讨论的 Agent 列表
            message: 讨论主题
            max_rounds: 最大讨论轮数
            consensus_check: 是否自动检测共识
        """
        all_results: dict[str, list[str]] = {name: [] for name in agent_names}
        current_topic = message

        for round_num in range(1, max_rounds + 1):
            round_results = {}
            for name in agent_names:
                agent = self._agents.get(name)
                if agent:
                    result = await self.chat_with_agent(session_id, name, current_topic)
                    round_results[name] = result
                    all_results[name].append(result)

            # 共识检测（可选）
            if consensus_check and round_num < max_rounds:
                consensus_prompt = f"讨论主题: {message}\n\n各方观点:\n"
                for name, result in round_results.items():
                    consensus_prompt += f"\n{name}: {result[:200]}\n"
                consensus_prompt += "\n各方观点是否已经收敛？是否还有关键分歧？只回答 YES 或 NO。"

                consensus = await self.chat_with_agent(session_id, "coordinator", consensus_prompt)
                if "YES" in consensus.upper():
                    break

            # 为下一轮构造上下文
            if round_num < max_rounds:
                next_topic_parts = [f"第 {round_num + 1} 轮讨论，主题: {message}\n\n上一轮观点:"]
                for name, result in round_results.items():
                    next_topic_parts.append(f"{name}: {result[:300]}")
                next_topic_parts.append("\n请基于以上观点继续讨论，提出新见解或补充。")
                current_topic = "\n".join(next_topic_parts)

        return all_results
