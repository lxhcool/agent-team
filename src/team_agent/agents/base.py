"""Agent 基类 — 定义 Agent 生命周期与能力接口"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any

from team_agent.config import AgentConfig
from team_agent.llm.base import BaseLLM, LLMMessage, LLMResponse, Role
from team_agent.llm.factory import create_llm
from team_agent.memory.manager import MemoryManager
from team_agent.messaging.bus import MessageBus
from team_agent.messaging.protocol import Message, MessageType
from team_agent.skills.skill_manager import SkillManager
from team_agent.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class AgentState(str, Enum):
    """Agent 状态"""

    IDLE = "idle"
    WORKING = "working"
    WAITING = "waiting"
    ERROR = "error"
    STOPPED = "stopped"


class BaseAgent(ABC):
    """Agent 基类 — 所有 Agent 继承此类"""

    def __init__(
        self,
        config: AgentConfig,
        llm: BaseLLM | None = None,
        message_bus: MessageBus | None = None,
        memory: MemoryManager | None = None,
        skill_manager: SkillManager | None = None,
        tool_registry: ToolRegistry | None = None,
    ):
        self.id = str(uuid.uuid4())
        self.config = config
        self.name = config.name
        self.state = AgentState.IDLE
        self.llm = llm or create_llm(config.model)
        self.message_bus = message_bus or MessageBus()
        self.memory = memory or MemoryManager()
        self.skill_manager = skill_manager or SkillManager()
        self.tool_registry = tool_registry or ToolRegistry()

        # 对话历史
        self._messages: list[LLMMessage] = []
        # 当前会话
        self._session_id: str | None = None
        # 最大迭代次数
        self._max_iterations = config.max_iterations
        # 当前迭代
        self._iteration = 0
        # 执行轨迹
        self._trace: list[dict[str, Any]] = []

    def _build_system_prompt(self) -> str:
        """构建 System Prompt：基础 + Skill + 记忆"""
        parts = []

        # 1. 基础提示词
        if self.config.system_prompt:
            parts.append(self.config.system_prompt)

        # 2. Skill 提示词
        if self.config.skills:
            skill_names = [s.name for s in self.config.skills if s.enabled]
            skills_prompt = self.skill_manager.build_skills_prompt(skill_names)
            if skills_prompt:
                parts.append(skills_prompt)

        # 3. 记忆上下文
        memory_ctx = self.memory.build_context(self.name)
        if memory_ctx:
            parts.append(memory_ctx)

        return "\n\n".join(parts)

    def _get_tools_schema(self) -> list[dict[str, Any]] | None:
        """获取当前 Agent 可用的工具 schema"""
        if not self.config.skills:
            return None

        tool_names: list[str] = []
        for skill_cfg in self.config.skills:
            if not skill_cfg.enabled:
                continue
            skill = self.skill_manager.get(skill_cfg.name)
            if skill:
                tool_names.extend(skill.tools)

        # 去重
        tool_names = list(dict.fromkeys(tool_names))
        if not tool_names:
            return None

        return self.tool_registry.get_schemas(tool_names)

    async def start(self, session_id: str) -> None:
        """启动 Agent"""
        self._session_id = session_id
        self.state = AgentState.IDLE
        self._messages = []
        self._iteration = 0
        self._trace = []

        # 加载 System Prompt
        system_prompt = self._build_system_prompt()
        self._messages.append(LLMMessage(role=Role.SYSTEM, content=system_prompt))

        # 注册消息处理器
        self.message_bus.register(self.name, self._handle_message)

        logger.info(f"Agent {self.name} started for session {session_id}")

    async def stop(self) -> None:
        """停止 Agent"""
        self.state = AgentState.STOPPED
        logger.info(f"Agent {self.name} stopped")

    async def chat(self, user_message: str) -> str:
        """用户对话入口"""
        self.state = AgentState.WORKING
        self._messages.append(LLMMessage(role=Role.USER, content=user_message))

        # 保存对话到记忆
        if self._session_id:
            await self.memory.save_conversation(self._session_id, self.name, "user", user_message)

        try:
            response = await self._run_loop()
            self.state = AgentState.IDLE
            return response
        except Exception as e:
            self.state = AgentState.ERROR
            logger.error(f"Agent {self.name} error: {e}")
            return f"[Error] {e}"

    async def _run_loop(self) -> str:
        """Agent 执行循环 — 支持工具调用"""
        self._iteration = 0

        while self._iteration < self._max_iterations:
            self._iteration += 1

            # 调用 LLM
            start = time.time()
            response = await self.llm.chat_with_auto_continue(
                messages=self._messages,
                tools=self._get_tools_schema(),
            )
            elapsed = (time.time() - start) * 1000

            # 记录轨迹
            self._trace.append({
                "iteration": self._iteration,
                "model": response.model,
                "tokens": response.usage.total_tokens,
                "latency_ms": elapsed,
                "finish_reason": response.finish_reason,
            })

            # 添加助手消息
            self._messages.append(LLMMessage(
                role=Role.ASSISTANT,
                content=response.content,
                tool_calls=response.tool_calls,
            ))

            # 保存对话
            if self._session_id:
                await self.memory.save_conversation(
                    self._session_id, self.name, "assistant", response.content,
                    model=response.model, tokens=response.usage.total_tokens,
                )

            # 如果没有工具调用，直接返回
            if not response.tool_calls:
                return response.content

            # 执行工具调用
            for tool_call in response.tool_calls:
                result = await self._execute_tool(tool_call)
                self._messages.append(LLMMessage(
                    role=Role.TOOL,
                    content=result,
                    tool_call_id=tool_call.get("id", ""),
                    name=tool_call.get("function", {}).get("name", ""),
                ))

        return self._messages[-1].content if self._messages else "Max iterations reached"

    async def _execute_tool(self, tool_call: dict[str, Any]) -> str:
        """执行工具调用"""
        func_info = tool_call.get("function", {})
        tool_name = func_info.get("name", "")
        try:
            arguments = json.loads(func_info.get("arguments", "{}"))
        except json.JSONDecodeError:
            arguments = {}

        tool_def = self.tool_registry.get(tool_name)
        if not tool_def:
            return f"Error: Tool '{tool_name}' not found"

        try:
            if asyncio.iscoroutinefunction(tool_def.func):
                result = await tool_def.func(**arguments)
            else:
                result = tool_def.func(**arguments)
            return str(result)
        except Exception as e:
            return f"Error executing {tool_name}: {e}"

    async def _handle_message(self, message: Message) -> None:
        """处理来自消息总线的消息"""
        if message.receiver != self.name and not message.is_broadcast():
            return

        if message.type == MessageType.SHUTDOWN:
            await self.stop()
        elif message.type == MessageType.INTERRUPT:
            self.state = AgentState.IDLE
            self._messages = self._messages[:1]  # 只保留 system prompt
        elif message.type == MessageType.TASK_ASSIGN:
            result = await self.chat(message.content)
            await self.message_bus.send(Message(
                type=MessageType.TASK_COMPLETE,
                sender=self.name,
                receiver=message.sender,
                content=result,
                reply_to=message.id,
                session_id=self._session_id,
            ))
        elif message.type == MessageType.COLLAB_REQUEST:
            result = await self.chat(message.content)
            await self.message_bus.send(Message(
                type=MessageType.COLLAB_RESPONSE,
                sender=self.name,
                receiver=message.sender,
                content=result,
                reply_to=message.id,
                session_id=self._session_id,
            ))
        elif message.type == MessageType.QUESTION:
            result = await self.chat(message.content)
            await self.message_bus.send(Message(
                type=MessageType.ANSWER,
                sender=self.name,
                receiver=message.sender,
                content=result,
                reply_to=message.id,
                session_id=self._session_id,
            ))

    @abstractmethod
    def get_role_description(self) -> str:
        """返回 Agent 的角色描述"""
        ...

    def get_trace(self) -> list[dict[str, Any]]:
        """获取执行轨迹"""
        return list(self._trace)

    def get_status(self) -> dict[str, Any]:
        """获取 Agent 状态"""
        return {
            "name": self.name,
            "state": self.state.value,
            "iteration": self._iteration,
            "max_iterations": self._max_iterations,
            "model": self.llm.model_name,
            "trace_count": len(self._trace),
        }
