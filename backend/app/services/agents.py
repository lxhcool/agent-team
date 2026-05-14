"""Agent base classes and core agent implementations.

Architecture:
- BaseAgent: provides LLM calling, message publishing, state management
- LeaderAgent: orchestrates the planning flow (analyze -> proposal -> plan)
- AgentFactory: creates Agent instances from AgentTemplate

Also implements:
- C-003: Sub-agent collaboration requests
- C-005: Leader intervention (interrupt/command messages)
- F-001: Agent output repair (feed back error and retry)
- F-002: Tool retry
- O-009: Agent exclusive execution
"""

import asyncio
import json
import logging
import os
from typing import Any, Dict, List, Optional

from app.core.database import async_session
from app.llm.router import LLMMessage, LLMRouter, LLMError
from app.models.models import AgentTemplate, Message, MessageType, PlanningSession, PlanningStatus, Task, TaskStatus
from app.services.event_bus import EventBus, Event


logger = logging.getLogger(__name__)


class BaseAgent:
    """Base class for all agents with full template support.

    Implements:
    - C-003: Sub-agent collaboration requests
    - C-005: Leader intervention (interrupt/command messages)
    - A-011: Agent Card capability broadcast on startup
    - O-009: Agent exclusive execution
    """

    def __init__(
        self,
        name: str,
        display_name: str,
        llm_router: LLMRouter,
        event_bus: EventBus,
        role: str = "assistant",
        goal: Optional[str] = None,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        provider: Optional[str] = None,
        capabilities: Optional[List[Dict[str, Any]]] = None,
        constraints: Optional[List[str]] = None,
        participation_modes: Optional[List[str]] = None,
        risk_level: str = "low",
    ):
        self.name = name
        self.display_name = display_name
        self.llm_router = llm_router
        self.event_bus = event_bus
        self.role = role
        self.goal = goal
        self.system_prompt = system_prompt or goal or f"你是一个{display_name}。"
        self.model = model
        self.provider = provider
        self.capabilities = capabilities or []
        self.constraints = constraints or []
        self.participation_modes = participation_modes or ["planning"]
        self.risk_level = risk_level

    # ===== A-011: Agent Card Capability Broadcast =====

    def get_agent_card(self) -> dict:
        """Generate an Agent Card describing this agent's capabilities.

        Per A-011: Agents should broadcast their capabilities on startup
        so the orchestrator and other agents know what they can do.

        Returns:
            Dict with agent card information
        """
        allowed_tools = []
        for cap in self.capabilities:
            if isinstance(cap, dict) and "tools" in cap:
                allowed_tools.extend(cap["tools"])

        return {
            "name": self.name,
            "display_name": self.display_name,
            "role": self.role,
            "goal": self.goal,
            "model": self.model,
            "provider": self.provider,
            "capabilities": [c if isinstance(c, str) else c.get("name", "") for c in self.capabilities],
            "allowed_tools": allowed_tools,
            "constraints": self.constraints,
            "participation_modes": self.participation_modes,
            "risk_level": self.risk_level,
        }

    async def broadcast_capabilities(self, session_id: Optional[str] = None):
        """Broadcast this agent's capabilities via the event bus.

        Per A-011: Called when an agent starts up or joins a session.
        """
        card = self.get_agent_card()
        target = session_id or "__global__"
        self.event_bus.publish(target, Event(
            event="agent_card",
            data=card,
        ))

    def _infer_default_model(self, adapter) -> str:
        """Infer a reasonable default model from provider info.

        Called when neither agent.model nor provider.default_model is set.
        Prefers chat-capable models based on provider base_url.
        """
        base_url = (adapter.base_url or "").lower()
        # Known provider defaults
        if "deepseek" in base_url:
            return "deepseek-chat"
        if "siliconflow" in base_url:
            return "Qwen/Qwen2.5-7B-Instruct"
        if "moonshot" in base_url:
            return "moonshot-v1-8k"
        if "groq" in base_url:
            return "llama-3.1-8b-instant"
        if "openrouter" in base_url:
            return "openai/gpt-4o-mini"
        if "localhost:11434" in base_url:
            return "llama3"
        # Try to find from provider's models_json via database
        try:
            import asyncio
            from app.core.database import async_session
            from sqlalchemy import select
            from app.models.models import ProviderConfig
            import json as _json

            async def _get_first_model():
                async with async_session() as db:
                    result = await db.execute(
                        select(ProviderConfig).where(
                            ProviderConfig.provider_name == adapter.name
                        )
                    )
                    p = result.scalars().first()
                    if p and p.models_json:
                        models = _json.loads(p.models_json)
                        if models:
                            return models[0].get("model_id", models[0].get("id", ""))
                return None

            loop = asyncio.get_event_loop()
            if loop.is_running():
                return "gpt-4o-mini"  # Fallback in async context
            model = loop.run_until_complete(_get_first_model())
            if model:
                return model
        except Exception:
            pass
        return "gpt-4o-mini"

    async def call_llm(
        self,
        messages: List[LLMMessage],
        session_id: str,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> str:
        """Call the LLM and return the content string."""
        # Determine provider and model: agent-specific > provider default
        adapter = None
        if self.provider:
            adapter = self.llm_router._providers.get(self.provider)
        if not adapter:
            for p in self.llm_router._providers.values():
                adapter = p
                break
        if not adapter:
            raise LLMError("No LLM provider configured")

        model = self.model or adapter.default_model or self._infer_default_model(adapter)

        from app.llm.router import ProviderAdapter
        pa = ProviderAdapter(adapter)
        result = await pa.complete(
            messages=messages,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            stream=False,
        )
        return result.content

    async def call_llm_stream(
        self,
        messages: List[LLMMessage],
        session_id: str,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ):
        """Call the LLM in stream mode. Yields str chunks for real-time output.

        Uses true streaming so the user sees output as it arrives.
        Falls back to non-stream complete if streaming fails.
        """
        adapter = None
        if self.provider:
            adapter = self.llm_router._providers.get(self.provider)
        if not adapter:
            for p in self.llm_router._providers.values():
                adapter = p
                break
        if not adapter:
            raise LLMError("No LLM provider configured")

        from app.llm.router import ProviderAdapter
        pa = ProviderAdapter(adapter)
        model = self.model or adapter.default_model or self._infer_default_model(adapter)

        import logging
        logging.getLogger(__name__).info(
            f"LLM stream call: provider={adapter.name}, model={model}, "
            f"base_url={adapter.base_url}, messages={len(messages)}"
        )

        # Use true stream mode for real-time output
        try:
            stream_iter = await pa.complete(
                messages=messages,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                stream=True,
            )
            full_content = ""
            reasoning_chars = 0
            next_reasoning_notice_at = 1
            async for chunk in stream_iter:
                if isinstance(chunk, dict):
                    chunk_type = chunk.get("type")
                    chunk_content = chunk.get("content") or ""
                    if chunk_type == "reasoning":
                        reasoning_chars += len(chunk_content)
                        if reasoning_chars >= next_reasoning_notice_at:
                            if reasoning_chars < 80:
                                detail = "模型正在理解需求..."
                            elif reasoning_chars < 220:
                                detail = "模型正在推理方案结构..."
                            else:
                                detail = "模型正在整理最终输出..."
                            self.emit_status(session_id, "thinking", detail)
                            next_reasoning_notice_at = max(reasoning_chars + 120, next_reasoning_notice_at * 2)
                        continue
                    if chunk_type == "content" and chunk_content:
                        if reasoning_chars:
                            self.emit_status(session_id, "generating", "推理完成，正在输出结果...")
                            reasoning_chars = 0
                        full_content += chunk_content
                        yield chunk_content
                    continue
                if chunk:
                    full_content += chunk
                    yield chunk
        except Exception as e:
            logging.getLogger(__name__).warning(f"Stream mode failed, falling back to complete: {e}")
            # Fallback to non-stream mode
            try:
                result = await pa.complete(
                    messages=messages,
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    stream=False,
                )
                if result.was_truncated:
                    self.emit_status(session_id, "continuing", "输出被截断，正在续写...")
                yield result.content
            except Exception as fallback_err:
                logging.getLogger(__name__).error(f"Both stream and fallback failed: {fallback_err}")
                raise

    async def save_message(
        self,
        session_id: str,
        content: str,
        message_type: MessageType = MessageType.CHAT,
        category: Optional[str] = None,
        receiver: Optional[str] = None,
    ) -> Message:
        """Save a message to the database."""
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
                sender=self.name,
                receiver=receiver,
                message_type=message_type,
                category=category,
                content=content,
            )
            db.add(msg)
            await db.commit()
            await db.refresh(msg)
            return msg

    def emit_message(self, session_id: str, msg: Message):
        self.event_bus.publish(session_id, Event(
            event="message",
            data={
                "id": msg.id,
                "seq": msg.seq,
                "sender": msg.sender,
                "sender_display": self.display_name,
                "receiver": msg.receiver,
                "message_type": msg.message_type.value if msg.message_type else "chat",
                "category": msg.category,
                "content": msg.content,
                "created_at": msg.created_at.isoformat() if msg.created_at else None,
            },
        ))

    def emit_status(self, session_id: str, status: str, detail: Optional[str] = None):
        self.event_bus.publish(session_id, Event(
            event="status",
            data={"status": status, "detail": detail},
        ))

    def emit_typing(self, session_id: str, is_typing: bool = True):
        self.event_bus.publish(session_id, Event(
            event="typing",
            data={"agent": self.name, "display_name": self.display_name, "is_typing": is_typing},
        ))

    def emit_stream_chunk(self, session_id: str, chunk: str, message_id: Optional[str] = None):
        self.event_bus.publish(session_id, Event(
            event="stream",
            data={"agent": self.name, "display_name": self.display_name, "chunk": chunk, "message_id": message_id},
        ))

    def emit_stream_end(self, session_id: str, message_id: Optional[str] = None):
        self.event_bus.publish(session_id, Event(
            event="stream_end",
            data={"agent": self.name, "message_id": message_id},
        ))

    def build_system_message(self, context: Optional[str] = None) -> LLMMessage:
        """Build a system message from the agent's template."""
        parts = [self.system_prompt]
        if self.constraints:
            parts.append("\n\n约束条件：")
            for c in self.constraints:
                parts.append(f"- {c}")
        if context:
            parts.append(f"\n\n{context}")
        return LLMMessage(role="system", content="".join(parts))

    # ===== C-003: Sub-agent Collaboration =====

    async def send_collaboration_request(
        self,
        session_id: str,
        target_agent: str,
        request_content: str,
    ) -> Message:
        """Send a collaboration request to another agent (C-003).

        Sub-agents can request help from other sub-agents, but cannot
        issue commands to them.
        """
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
                sender=self.name,
                receiver=target_agent,
                message_type=MessageType.CHAT,
                category="collaboration_request",
                content=request_content,
            )
            db.add(msg)
            await db.commit()
            await db.refresh(msg)

        self.event_bus.publish(session_id, Event(
            event="message",
            data={
                "id": msg.id,
                "seq": msg.seq,
                "sender": msg.sender,
                "sender_display": self.display_name,
                "receiver": msg.receiver,
                "message_type": "chat",
                "category": "collaboration_request",
                "content": msg.content,
                "created_at": msg.created_at.isoformat() if msg.created_at else None,
            },
        ))
        return msg

    # ===== C-005: Leader Intervention =====

    async def send_interrupt(
        self,
        session_id: str,
        target_agent: str,
        reason: str,
    ) -> Message:
        """Send an interrupt to an agent (C-005: Leader intervention).

        Only Leader should use this. Pauses the target agent's current task.
        """
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
                sender=self.name,
                receiver=target_agent,
                message_type=MessageType.INTERRUPT,
                category="intervention",
                content=reason,
            )
            db.add(msg)
            await db.commit()
            await db.refresh(msg)

        self.event_bus.publish(session_id, Event(
            event="interrupt",
            data={
                "id": msg.id,
                "seq": msg.seq,
                "sender": self.name,
                "sender_display": self.display_name,
                "receiver": target_agent,
                "reason": reason,
            },
        ))
        return msg

    async def send_command(
        self,
        session_id: str,
        target_agent: str,
        command: str,
    ) -> Message:
        """Send a command to an agent (C-005: Leader intervention).

        Only Leader should use this. Directs the target agent to take action.
        """
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
                sender=self.name,
                receiver=target_agent,
                message_type=MessageType.COMMAND,
                category="intervention",
                content=command,
            )
            db.add(msg)
            await db.commit()
            await db.refresh(msg)

        self.event_bus.publish(session_id, Event(
            event="command",
            data={
                "id": msg.id,
                "seq": msg.seq,
                "sender": self.name,
                "sender_display": self.display_name,
                "receiver": target_agent,
                "command": command,
            },
        ))
        return msg

    # ===== Tool Execution Support (T-001~T-007) =====

    async def execute_tool(
        self,
        tool_name: str,
        parameters: dict,
        session_id: str,
        task_id: Optional[str] = None,
        debug_mode: bool = False,
    ) -> dict:
        """Execute a tool and return the result.

        P2-F-005: If debug_mode is True, records tool-level checkpoint.
        """
        from app.services.tools import tool_registry, ToolResult

        tool = tool_registry.get_tool(tool_name)
        if not tool:
            return {"success": False, "error": f"Tool '{tool_name}' not found"}

        # Check if agent has access to this tool
        if self.capabilities:
            allowed_tools = []
            for cap in self.capabilities:
                if isinstance(cap, dict) and "tools" in cap:
                    allowed_tools.extend(cap["tools"])
            if allowed_tools and tool_name not in allowed_tools:
                return {"success": False, "error": f"Agent not authorized for tool '{tool_name}'"}

        start_time = None
        if debug_mode:
            import time
            start_time = time.monotonic()

        try:
            result: ToolResult = await tool.execute(
                **parameters,
                session_id=session_id,
                agent_name=self.name,
                task_id=task_id,
            )

            # P2-F-005: Record tool checkpoint in debug_mode
            if debug_mode and start_time:
                duration_ms = int((time.monotonic() - start_time) * 1000)
                try:
                    from app.services.execution import tool_checkpoint_recorder
                    await tool_checkpoint_recorder.record_tool_execution(
                        session_id=session_id,
                        session_type="planning",
                        tool_name=tool_name,
                        agent_name=self.name,
                        input_params=parameters,
                        result=result.to_dict() if result.success else None,
                        error=result.error if not result.success else None,
                        duration_ms=duration_ms,
                        task_id=task_id,
                    )
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).debug(f"Tool checkpoint recording failed: {e}")

            return result.to_dict()
        except Exception as e:
            # P2-F-005: Record tool error checkpoint
            if debug_mode and start_time:
                duration_ms = int((time.monotonic() - start_time) * 1000)
                try:
                    from app.services.execution import tool_checkpoint_recorder
                    await tool_checkpoint_recorder.record_tool_execution(
                        session_id=session_id,
                        session_type="planning",
                        tool_name=tool_name,
                        agent_name=self.name,
                        input_params=parameters,
                        error=str(e),
                        duration_ms=duration_ms,
                        task_id=task_id,
                    )
                except Exception:
                    pass
            return {"success": False, "error": str(e)}


class LeaderAgent(BaseAgent):
    """
    The leader agent drives the planning flow:
    CREATED -> ANALYZING -> GENERATING_PROPOSAL -> AWAITING_APPROVAL -> GENERATING_PLAN -> COMPLETED
    """

    ANALYSIS_SYSTEM_PROMPT = """你是一个资深的软件架构师和需求分析师。你的任务是分析用户的需求，从以下维度进行深入分析：

1. **需求理解**：核心功能需求是什么？用户真正想要解决什么问题？
2. **技术可行性**：有哪些技术方案可以实现？各方案的优劣？
3. **关键风险**：可能遇到的技术难点和风险？
4. **建议方案**：推荐的技术方案及理由

请用中文回答，结构清晰，使用 Markdown 格式。"""

    PROPOSAL_SYSTEM_PROMPT = """你是一个技术方案专家。根据需求分析，生成一份完整的技术方案提案，包含：

1. **方案概述**：一句话描述方案
2. **架构设计**：系统架构和技术选型
3. **核心模块**：需要开发的核心模块列表
4. **实现步骤**：分阶段的实现计划
5. **风险与缓解**：潜在风险及应对措施

请用中文回答，使用 Markdown 格式，方案要具体可执行。"""

    PLAN_SYSTEM_PROMPT = """你是一个交付整理专家。根据已审批的技术方案，生成一份详细的交接清单，格式为 JSON：

生成一个任务列表，每个任务包含：
- title: 任务标题
- description: 任务详细描述
- assigned_agent: 负责的 Agent 角色（如 analyst, spec_writer, reviewer）
- dependencies: 依赖的任务序号列表（从0开始）
- target_paths: 涉及的文件路径列表
- validation_commands: 可作为验收或交接提醒的要点列表

边界约束：
- 不要输出代码执行命令
- 不要把任务写成自动改仓库流程
- 要聚焦交接、范围、依赖、完成判断和待确认项

请直接输出 JSON 数组，不要包含其他内容。格式示例：
[
  {
    "title": "整理交付准备说明",
    "description": "汇总模块边界、依赖和完成判断...",
    "assigned_agent": "spec_writer",
    "dependencies": [],
    "target_paths": ["模块说明", "接口约束", "完成判断"],
    "validation_commands": ["确认是否还有未明确依赖"]
  }
]"""

    def __init__(self, llm_router: LLMRouter, event_bus: EventBus, model: Optional[str] = None, provider: Optional[str] = None):
        super().__init__(
            name="leader",
            display_name="Leader",
            llm_router=llm_router,
            event_bus=event_bus,
            role="coordinator",
            goal="协调 Agent 团队完成需求分析、技术方案生成和交接清单整理",
            system_prompt=self.ANALYSIS_SYSTEM_PROMPT,
            model=model,
            provider=provider,
            capabilities=[
                {"name": "requirement_analysis", "description": "需求分析"},
                {"name": "proposal_generation", "description": "方案生成"},
                {"name": "handoff_generation", "description": "交接清单整理"},
            ],
            constraints=["必须先完成需求分析再生成方案", "方案需要用户审批后才能生成交接清单"],
            participation_modes=["planning"],
        )

    async def run_analysis(self, session_id: str, user_input: str, research_context: str = ""):
        """Phase 1: Analyze the user's requirement, optionally incorporating research results."""
        self.emit_status(session_id, "analyzing", "正在分析需求...")
        self.emit_typing(session_id, True)

        user_content = f"请分析以下需求：\n\n{user_input}"
        if research_context:
            user_content += f"\n\n研究员的调研结果：\n{research_context}\n\n请结合调研结果进行深入分析。"

        messages = [
            LLMMessage(role="system", content=self.ANALYSIS_SYSTEM_PROMPT),
            LLMMessage(role="user", content=user_content),
        ]

        full_content = []
        try:
            async for chunk in self.call_llm_stream(messages, session_id, max_tokens=4096, temperature=0.7):
                full_content.append(chunk)
                self.emit_stream_chunk(session_id, chunk)
        except LLMError:
            content = await self.call_llm(messages, session_id, max_tokens=4096, temperature=0.7)
            full_content.append(content)
            self.emit_stream_chunk(session_id, content)

        analysis_text = "".join(full_content)
        msg = await self.save_message(
            session_id, analysis_text,
            message_type=MessageType.CHAT,
            category="analysis",
        )
        self.emit_message(session_id, msg)

        self.emit_stream_end(session_id)
        self.emit_typing(session_id, False)

        return analysis_text

    async def run_proposal(self, session_id: str, user_input: str, analysis: str):
        """Phase 2: Generate a technical proposal."""
        self.emit_status(session_id, "generating_proposal", "正在生成技术方案...")
        self.emit_typing(session_id, True)

        messages = [
            LLMMessage(role="system", content=self.PROPOSAL_SYSTEM_PROMPT),
            LLMMessage(role="user", content=f"原始需求：\n{user_input}\n\n需求分析：\n{analysis}\n\n请生成技术方案。"),
        ]

        full_content = []
        try:
            async for chunk in self.call_llm_stream(messages, session_id, max_tokens=4096, temperature=0.7):
                full_content.append(chunk)
                self.emit_stream_chunk(session_id, chunk)
        except LLMError:
            content = await self.call_llm(messages, session_id, max_tokens=4096, temperature=0.7)
            full_content.append(content)
            self.emit_stream_chunk(session_id, content)

        proposal_text = "".join(full_content)
        msg = await self.save_message(
            session_id, proposal_text,
            message_type=MessageType.PROPOSAL,
            category="proposal",
        )
        self.emit_message(session_id, msg)

        self.emit_stream_end(session_id)
        self.emit_typing(session_id, False)

        return proposal_text

    async def run_plan(self, session_id: str, proposal: str):
        """Phase 3: Generate handoff checklist from approved proposal."""
        self.emit_status(session_id, "generating_plan", "正在整理交接清单...")
        self.emit_typing(session_id, True)

        messages = [
            LLMMessage(role="system", content=self.PLAN_SYSTEM_PROMPT),
            LLMMessage(role="user", content=f"已审批的技术方案：\n{proposal}\n\n请生成交接清单。"),
        ]

        plan_text = await self.call_llm(messages, session_id, max_tokens=4096, temperature=0.3)

        # Parse tasks from JSON
        tasks_data = []
        try:
            text = plan_text.strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            tasks_data = json.loads(text)
        except json.JSONDecodeError:
            tasks_data = [{"title": "交接清单", "description": plan_text, "assigned_agent": "spec_writer", "dependencies": []}]

        # Save plan as message
        msg = await self.save_message(
            session_id, plan_text,
            message_type=MessageType.PLAN,
            category="planning_summary",
        )
        self.emit_message(session_id, msg)

        # Save tasks to database
        saved_tasks = []
        async with async_session() as db:
            for i, td in enumerate(tasks_data):
                task = Task(
                    session_type="planning",
                    session_id=session_id,
                    title=td.get("title", f"Task {i+1}"),
                    description=td.get("description", ""),
                    status=TaskStatus.PENDING,
                    assigned_agent=td.get("assigned_agent"),
                    owner_role=td.get("assigned_agent"),
                    dependencies_json=json.dumps(td.get("dependencies", [])),
                    target_paths_json=json.dumps(td.get("target_paths", [])),
                    validation_commands_json=json.dumps(td.get("validation_commands", [])),
                    order=i,
                )
                db.add(task)
                saved_tasks.append(task)

            await db.commit()
            for t in saved_tasks:
                await db.refresh(t)

        self.event_bus.publish(session_id, Event(
            event="tasks",
            data=[{
                "id": t.id,
                "title": t.title,
                "description": t.description,
                "assigned_agent": t.assigned_agent,
                "status": t.status.value,
                "order": t.order,
            } for t in saved_tasks],
        ))

        self.emit_typing(session_id, False)
        return plan_text, saved_tasks

    async def _refine_proposal_with_review(
        self, session_id: str, proposal: str, review: str
    ) -> Optional[str]:
        """Refine the proposal based on reviewer feedback."""
        self.emit_typing(session_id, True)

        messages = [
            LLMMessage(role="system", content=self.PROPOSAL_SYSTEM_PROMPT),
            LLMMessage(role="user", content=(
                f"原始技术方案：\n{proposal}\n\n"
                f"审查意见：\n{review}\n\n"
                f"请根据审查意见优化技术方案，保留原有优点，改进指出的问题。"
            )),
        ]

        full_content = []
        try:
            async for chunk in self.call_llm_stream(messages, session_id, max_tokens=4096, temperature=0.5):
                full_content.append(chunk)
                self.emit_stream_chunk(session_id, chunk)
        except LLMError:
            content = await self.call_llm(messages, session_id, max_tokens=4096, temperature=0.5)
            full_content.append(content)
            self.emit_stream_chunk(session_id, content)

        refined_text = "".join(full_content)
        if refined_text:
            # Update the proposal message
            msg = await self.save_message(
                session_id, refined_text,
                message_type=MessageType.PROPOSAL,
                category="proposal_refined",
            )
            self.emit_message(session_id, msg)

        self.emit_stream_end(session_id)
        self.emit_typing(session_id, False)
        return refined_text if refined_text else None

    async def _refine_plan_with_review(
        self, session_id: str, plan_text: str, review: str
    ) -> Optional[str]:
        """Refine the handoff checklist based on reviewer feedback."""
        self.emit_typing(session_id, True)

        messages = [
            LLMMessage(role="system", content=self.PLAN_SYSTEM_PROMPT),
            LLMMessage(role="user", content=(
                f"原始交接清单：\n{plan_text}\n\n"
                f"审查意见：\n{review}\n\n"
                f"请根据审查意见优化交接清单，保留合理的任务分解，改进指出的问题。"
                f"输出格式与原始计划相同（JSON 数组）。"
            )),
        ]

        full_content = []
        try:
            async for chunk in self.call_llm_stream(messages, session_id, max_tokens=4096, temperature=0.3):
                full_content.append(chunk)
                self.emit_stream_chunk(session_id, chunk)
        except LLMError:
            content = await self.call_llm(messages, session_id, max_tokens=4096, temperature=0.3)
            full_content.append(content)
            self.emit_stream_chunk(session_id, content)

        refined_text = "".join(full_content)
        if refined_text:
            msg = await self.save_message(
                session_id, refined_text,
                message_type=MessageType.PLAN,
                category="planning_summary_refined",
            )
            self.emit_message(session_id, msg)

        self.emit_stream_end(session_id)
        self.emit_typing(session_id, False)
        return refined_text if refined_text else None


class TemplateAgent(BaseAgent):
    """An agent created from an AgentTemplate with custom system prompt and behavior.

    This is used for roundtable discussions and custom agent participation.
    The agent uses its system_prompt to define its behavior and perspective.
    """

    async def respond(self, session_id: str, context: str, question: str) -> str:
        """Respond to a question from the agent's perspective."""
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"[Roundtable] {self.display_name} starting respond for session {session_id}")
        self.emit_typing(session_id, True)

        messages = [
            self.build_system_message(context=context),
            LLMMessage(role="user", content=question),
        ]

        full_content = []
        chunk_count = 0
        try:
            async for chunk in self.call_llm_stream(messages, session_id, max_tokens=2048, temperature=0.7):
                full_content.append(chunk)
                chunk_count += 1
                self.emit_stream_chunk(session_id, chunk)
            logger.info(f"[Roundtable] {self.display_name} stream completed: {chunk_count} chunks, {len(''.join(full_content))} chars")
        except LLMError as e:
            logger.warning(f"[Roundtable] {self.display_name} stream failed, falling back: {e}")
            content = await self.call_llm(messages, session_id, max_tokens=2048, temperature=0.7)
            full_content.append(content)
            self.emit_stream_chunk(session_id, content)

        response_text = "".join(full_content)
        msg = await self.save_message(
            session_id, response_text,
            message_type=MessageType.CHAT,
            category="discussion",
        )
        self.emit_message(session_id, msg)

        self.emit_stream_end(session_id)
        self.emit_typing(session_id, False)

        return response_text


# ===== P1-6: Specialized Agent Classes =====

class ResearcherAgent(BaseAgent):
    """Agent specialized in research, analysis, and information gathering.

    Per requirements: ResearcherAgent focuses on understanding requirements,
    analyzing technical feasibility, and gathering relevant information.
    """

    RESEARCH_SYSTEM_PROMPT = """你是一个专业的研究员。你的职责是：
1. 深入分析需求的技术可行性
2. 调研相关技术方案和最佳实践
3. 识别潜在风险和挑战
4. 提供数据驱动的分析结论

请用中文回答，注重事实依据和逻辑推理。"""

    def __init__(self, llm_router: LLMRouter, event_bus: EventBus, model: Optional[str] = None, provider: Optional[str] = None):
        super().__init__(
            name="researcher",
            display_name="Researcher",
            llm_router=llm_router,
            event_bus=event_bus,
            role="researcher",
            goal="深入分析需求、调研技术方案、识别风险",
            system_prompt=self.RESEARCH_SYSTEM_PROMPT,
            model=model,
            provider=provider,
            capabilities=[
                {"name": "requirement_analysis", "description": "需求分析"},
                {"name": "tech_research", "description": "技术调研"},
                {"name": "risk_identification", "description": "风险识别"},
                {"tools": ["web_search", "file_read", "file_list"]},
            ],
            constraints=["只做分析和调研，不生成交接清单"],
            participation_modes=["planning", "roundtable"],
            risk_level="low",
        )

    async def research(self, session_id: str, topic: str, context: str = "") -> str:
        """Conduct research on a topic."""
        self.emit_status(session_id, "researching", "正在调研...")
        self.emit_typing(session_id, True)

        messages = [
            LLMMessage(role="system", content=self.RESEARCH_SYSTEM_PROMPT),
            LLMMessage(role="user", content=f"请对以下主题进行深入调研：\n\n{topic}\n\n{f'背景信息：{context}' if context else ''}"),
        ]

        full_content = []
        try:
            async for chunk in self.call_llm_stream(messages, session_id, max_tokens=4096):
                full_content.append(chunk)
                self.emit_stream_chunk(session_id, chunk)
        except LLMError:
            content = await self.call_llm(messages, session_id, max_tokens=4096)
            full_content.append(content)

        research_text = "".join(full_content)
        msg = await self.save_message(session_id, research_text, message_type=MessageType.CHAT, category="research")
        self.emit_message(session_id, msg)

        self.emit_stream_end(session_id)
        self.emit_typing(session_id, False)
        return research_text


class PlannerAgent(BaseAgent):
    """Agent specialized in execution plan generation.

    Per requirements: PlannerAgent focuses on breaking down proposals
    into actionable, sequenced tasks with dependencies.
    """

    PLANNER_SYSTEM_PROMPT = """你是一个项目计划专家。你的职责是：
1. 将技术方案分解为具体的、可执行的任务
2. 确定任务之间的依赖关系和执行顺序
3. 为每个任务指定合适的 Agent 角色
4. 定义验证标准
5. 识别关键路径和高风险任务

请用中文回答，输出结构化的任务列表。"""

    def __init__(self, llm_router: LLMRouter, event_bus: EventBus, model: Optional[str] = None, provider: Optional[str] = None):
        super().__init__(
            name="planner",
            display_name="Planner",
            llm_router=llm_router,
            event_bus=event_bus,
            role="planner",
            goal="将技术方案转化为可执行的详细任务计划",
            system_prompt=self.PLANNER_SYSTEM_PROMPT,
            model=model,
            provider=provider,
            capabilities=[
                {"name": "plan_generation", "description": "计划生成"},
                {"name": "task_decomposition", "description": "任务分解"},
                {"name": "dependency_analysis", "description": "依赖分析"},
                {"tools": ["file_read", "file_list"]},
            ],
            constraints=["必须基于已审批的方案生成计划", "任务粒度要适中"],
            participation_modes=["planning"],
            risk_level="low",
        )


class ReviewerAgent(BaseAgent):
    """Agent specialized in code and plan review.

    Per requirements: ReviewerAgent focuses on quality assurance,
    identifying issues, and suggesting improvements.
    """

    REVIEW_SYSTEM_PROMPT = """你是一个资深的代码审查和方案评审专家。你的职责是：
1. 审查技术方案的完整性和可行性
2. 检查代码质量和潜在问题
3. 识别安全风险和性能瓶颈
4. 提出具体的改进建议
5. 确保方案符合最佳实践

【审查结论格式 — 必须遵守】
你的审查输出必须以以下格式结尾（这是自动解析的唯一格式）：

---REVIEW_VERDICT: APPROVE---
或
---REVIEW_VERDICT: NEEDS_REVISION---

判断标准：
- APPROVE：方案整体可行，仅有微小建议，不需要修改即可进入下一阶段
- NEEDS_REVISION：方案存在明显不足（需求遗漏、架构缺陷、风险未缓解、关键模块缺失等），必须修改后重新审查

在结论之前，请用中文详细说明审查发现的问题和改进建议。"""

    def __init__(self, llm_router: LLMRouter, event_bus: EventBus, model: Optional[str] = None, provider: Optional[str] = None):
        super().__init__(
            name="reviewer",
            display_name="Reviewer",
            llm_router=llm_router,
            event_bus=event_bus,
            role="reviewer",
            goal="审查技术方案和代码质量，提出改进建议",
            system_prompt=self.REVIEW_SYSTEM_PROMPT,
            model=model,
            provider=provider,
            capabilities=[
                {"name": "proposal_review", "description": "方案评审"},
                {"name": "code_review", "description": "代码审查"},
                {"name": "quality_assurance", "description": "质量保证"},
                {"tools": ["file_read", "file_list", "shell_execute"]},
            ],
            constraints=["只提供建议，不直接修改代码"],
            participation_modes=["planning", "roundtable"],
            risk_level="low",
        )

    async def review(self, session_id: str, content: str, review_type: str = "proposal") -> str:
        """Review content and provide feedback."""
        self.emit_status(session_id, "reviewing", "正在审查...")
        self.emit_typing(session_id, True)

        type_label = "技术方案" if review_type == "proposal" else "交接清单" if review_type == "plan" else "代码"
        messages = [
            LLMMessage(role="system", content=self.REVIEW_SYSTEM_PROMPT),
            LLMMessage(role="user", content=f"请审查以下{type_label}：\n\n{content}"),
        ]

        try:
            timeout_seconds = int(os.getenv("TEAM_AGENT_REVIEW_TIMEOUT_SECONDS", "90"))
            review_text = await asyncio.wait_for(
                self._review_with_llm(session_id, messages),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            logger.warning("Reviewer timed out for session %s", session_id)
            self.emit_status(session_id, "reviewing", "Reviewer 审查超时，使用兜底审查继续流程...")
            review_text = self._fallback_review_text(type_label, "Reviewer 审查超时")
        except Exception as err:
            logger.warning("Reviewer failed for session %s: %s", session_id, err)
            self.emit_status(session_id, "reviewing", "Reviewer 暂时不可用，使用兜底审查继续流程...")
            review_text = self._fallback_review_text(type_label, str(err))
        finally:
            self.emit_stream_end(session_id)
            self.emit_typing(session_id, False)

        msg = await self.save_message(session_id, review_text, message_type=MessageType.CHAT, category="review")
        self.emit_message(session_id, msg)
        return review_text

    async def _review_with_llm(self, session_id: str, messages: List[LLMMessage]) -> str:
        full_content = []
        try:
            async for chunk in self.call_llm_stream(messages, session_id, max_tokens=2048):
                full_content.append(chunk)
                self.emit_stream_chunk(session_id, chunk)
        except LLMError:
            content = await self.call_llm(messages, session_id, max_tokens=2048)
            full_content.append(content)

        return "".join(full_content)

    def _fallback_review_text(self, type_label: str, reason: str) -> str:
        return (
            f"Reviewer 未能在限定时间内完成{type_label}审查，原因：{reason}。\n\n"
            "为避免流程卡住，本轮采用兜底审查结论：当前内容先进入用户确认环节。"
            "请用户在确认页重点检查需求覆盖、功能边界、交互预期和风险说明；如有不满意，可以直接退回修改。\n\n"
            "---REVIEW_VERDICT: APPROVE---"
        )

    @staticmethod
    def parse_verdict(review_text: str) -> str:
        """Parse the review verdict from review output.

        Returns:
            'APPROVE' or 'NEEDS_REVISION'
        """
        import re
        match = re.search(r'---REVIEW_VERDICT:\s*(APPROVE|NEEDS_REVISION)---', review_text)
        if match:
            return match.group(1)
        # Fallback: if no explicit verdict, treat as needing revision if text is long
        return "NEEDS_REVISION" if len(review_text) > 200 else "APPROVE"


class AgentFactory:
    """Factory for creating Agent instances from AgentTemplate records."""

    def __init__(self, llm_router: LLMRouter, event_bus: EventBus):
        self.llm_router = llm_router
        self.event_bus = event_bus

    async def create_from_template(
        self,
        template: AgentTemplate,
        model_override: Optional[str] = None,
        provider_override: Optional[str] = None,
    ) -> BaseAgent:
        """Create an agent from an AgentTemplate ORM object."""
        capabilities = json.loads(template.capabilities_json) if template.capabilities_json else []
        constraints = json.loads(template.constraints_json) if template.constraints_json else []
        participation_modes = json.loads(template.participation_modes_json) if template.participation_modes_json else []

        common_kwargs = dict(
            name=template.name,
            display_name=template.display_name,
            llm_router=self.llm_router,
            event_bus=self.event_bus,
            role=template.role,
            goal=template.goal,
            system_prompt=template.system_prompt,
            model=model_override or template.model,
            provider=provider_override or template.provider,
            capabilities=capabilities,
            constraints=constraints,
            participation_modes=participation_modes,
            risk_level=template.risk_level,
        )

        # Specialized classes for built-in roles
        if template.name == "leader":
            agent = LeaderAgent(
                llm_router=self.llm_router,
                event_bus=self.event_bus,
                model=model_override or template.model,
                provider=provider_override or template.provider,
            )
            return agent

        # P1-6: Use specialized agent classes for built-in roles
        if template.name == "researcher":
            return ResearcherAgent(
                llm_router=self.llm_router,
                event_bus=self.event_bus,
                model=model_override or template.model,
                provider=provider_override or template.provider,
            )
        if template.name == "planner":
            return PlannerAgent(
                llm_router=self.llm_router,
                event_bus=self.event_bus,
                model=model_override or template.model,
                provider=provider_override or template.provider,
            )
        if template.name == "reviewer":
            return ReviewerAgent(
                llm_router=self.llm_router,
                event_bus=self.event_bus,
                model=model_override or template.model,
                provider=provider_override or template.provider,
            )

        # Generic template-based agent
        return TemplateAgent(**common_kwargs)

    async def create_by_name(
        self,
        agent_name: str,
        model_override: Optional[str] = None,
        provider_override: Optional[str] = None,
    ) -> BaseAgent:
        """Create an agent by looking up its template name in the database."""
        async with async_session() as db:
            from sqlalchemy import select
            result = await db.execute(
                select(AgentTemplate).where(AgentTemplate.name == agent_name)
            )
            template = result.scalars().first()
            if not template:
                raise ValueError(f"Agent template '{agent_name}' not found")
            return await self.create_from_template(
                template,
                model_override=model_override,
                provider_override=provider_override,
            )
