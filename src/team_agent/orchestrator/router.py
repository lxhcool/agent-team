"""Router — 任务路由与分配"""

from __future__ import annotations

import logging
from typing import Any

from team_agent.agents.base import BaseAgent
from team_agent.messaging.protocol import Message, MessageType
from team_agent.orchestrator.planner import Plan, PlanStep

logger = logging.getLogger(__name__)


class Router:
    """任务路由器 — 将计划步骤分配给对应 Agent"""

    def __init__(self):
        self._agents: dict[str, BaseAgent] = {}
        self._task_queue: list[PlanStep] = []
        self._running_tasks: dict[str, PlanStep] = {}  # agent_name -> step

    def register_agent(self, agent: BaseAgent) -> None:
        """注册 Agent"""
        self._agents[agent.name] = agent
        logger.info(f"Router registered agent: {agent.name}")

    def unregister_agent(self, agent_name: str) -> None:
        """取消注册 Agent"""
        self._agents.pop(agent_name, None)

    def get_available_agents(self) -> list[str]:
        """获取当前空闲的 Agent"""
        return [name for name, agent in self._agents.items() if agent.state.value == "idle"]

    def assign(self, step: PlanStep, message_bus: Any) -> bool:
        """将步骤分配给对应 Agent"""
        agent_name = step.agent
        if agent_name not in self._agents:
            logger.error(f"Agent not found: {agent_name}")
            return False

        agent = self._agents[agent_name]
        if agent.state.value != "idle":
            logger.warning(f"Agent {agent_name} is not idle, state: {agent.state.value}")
            return False

        step.status = "running"
        self._running_tasks[agent_name] = step

        # 通过消息总线发送任务
        message_bus.send_sync = lambda msg: asyncio_get_event_loop().create_task(message_bus.send(msg)) if hasattr(message_bus, 'send') else None
        logger.info(f"Assigned step {step.id} to agent {agent_name}")
        return True

    async def assign_task(self, step: PlanStep, message_bus: Any) -> bool:
        """异步分配任务"""
        agent_name = step.agent
        if agent_name not in self._agents:
            logger.error(f"Agent not found: {agent_name}")
            return False

        agent = self._agents[agent_name]
        step.status = "running"
        self._running_tasks[agent_name] = step

        await message_bus.send(Message(
            type=MessageType.TASK_ASSIGN,
            sender="coordinator",
            receiver=agent_name,
            content=step.description,
            data={
                "step_id": step.id,
                "expected_output": step.expected_output,
            },
        ))
        logger.info(f"Assigned step {step.id} to agent {agent_name}")
        return True

    def complete_task(self, agent_name: str) -> PlanStep | None:
        """标记任务完成"""
        step = self._running_tasks.pop(agent_name, None)
        if step:
            step.status = "completed"
            logger.info(f"Agent {agent_name} completed step {step.id}")
        return step

    def fail_task(self, agent_name: str) -> PlanStep | None:
        """标记任务失败"""
        step = self._running_tasks.pop(agent_name, None)
        if step:
            step.status = "failed"
            logger.error(f"Agent {agent_name} failed step {step.id}")
        return step

    def find_agent_for_role(self, role: str) -> BaseAgent | None:
        """根据角色名查找 Agent"""
        return self._agents.get(role)

    def get_status(self) -> dict[str, Any]:
        """获取路由器状态"""
        return {
            "registered_agents": list(self._agents.keys()),
            "running_tasks": {name: step.id for name, step in self._running_tasks.items()},
            "available_agents": self.get_available_agents(),
        }
