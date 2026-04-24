"""Session Management Service.

Implements:
- SS-008: Session auto-title (LLM-generated instead of first 50 chars)
- DM-005: Planning Session archival trigger
- DM-006: High-quality archival summary (LLM-generated)
- M-005: Archive summary sync (DB -> Markdown)
- C-007~C-011: Key message persistence, ACK, idempotent consumption, dedup, TTL
- O-009: Agent exclusive (only one main task at a time)
- O-010: Available Agent pool
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

from sqlalchemy import select, func, and_

from app.core.database import async_session
from app.models.models import (
    AgentHeartbeat, AgentTemplate, Message, MessageType,
    PlanningSession, PlanningStatus, MemoryEntry,
)
from app.services.event_bus import event_bus, Event
from app.llm.router import LLMRouter, LLMMessage

logger = logging.getLogger(__name__)


# ===== SS-008: Session Auto-Title =====

async def generate_session_title(
    llm_router: LLMRouter,
    user_input: str,
    model: str = "gpt-4o-mini",
) -> str:
    """Generate a concise title for a session using LLM.

    Per SS-008: Instead of taking first 50 chars, generate a meaningful title.
    """
    try:
        messages = [
            LLMMessage(role="system", content="你是一个标题生成器。根据用户的需求描述，生成一个简洁的中文标题（10个字以内），只输出标题本身，不要加引号或其他标点。"),
            LLMMessage(role="user", content=f"需求描述：{user_input[:500]}"),
        ]
        result = await llm_router.call(
            messages=messages,
            model=model,
            max_tokens=50,
            temperature=0.3,
        )
        title = result.content.strip().strip('"').strip("'")[:50]
        return title if title else user_input[:50]
    except Exception as e:
        logger.warning(f"Failed to generate session title: {e}")
        # Fallback: use first meaningful line
        first_line = user_input.split("\n")[0].strip()
        return first_line[:50] if first_line else "新会话"


# ===== C-007~C-011: Message Reliability =====

class MessageReliabilityService:
    """Implements message persistence, ACK, idempotent consumption, dedup, TTL.

    Per C-007: Key messages must be persisted to DB
    Per C-008: ACK for critical messages
    Per C-009: Idempotent consumption (dedupe_key)
    Per C-010: Message deduplication
    Per C-011: TTL for ephemeral messages
    """

    @staticmethod
    async def check_duplicate(session_id: str, dedupe_key: str) -> bool:
        """Check if a message with this dedupe_key already exists (C-010)."""
        async with async_session() as db:
            result = await db.execute(
                select(Message).where(
                    and_(
                        Message.session_id == session_id,
                        Message.dedupe_key == dedupe_key,
                    )
                )
            )
            return result.scalars().first() is not None

    @staticmethod
    async def ack_message(message_id: str) -> bool:
        """Acknowledge a critical message (C-008)."""
        async with async_session() as db:
            msg = await db.get(Message, message_id)
            if msg and msg.ack_at is None:
                msg.ack_at = datetime.now(timezone.utc)
                await db.commit()
                return True
            return False

    @staticmethod
    async def save_critical_message(
        session_type: str,
        session_id: str,
        sender: str,
        content: str,
        message_type: MessageType = MessageType.CHAT,
        category: Optional[str] = None,
        receiver: Optional[str] = None,
        dedupe_key: Optional[str] = None,
    ) -> Optional[Message]:
        """Save a critical message with dedup check (C-007, C-009, C-010)."""
        async with async_session() as db:
            # Dedup check
            if dedupe_key:
                existing = await db.execute(
                    select(Message).where(
                        and_(
                            Message.session_id == session_id,
                            Message.dedupe_key == dedupe_key,
                        )
                    )
                )
                if existing.scalars().first():
                    logger.info(f"Duplicate message skipped: {dedupe_key}")
                    return None

            # Get next seq
            result = await db.execute(
                select(func.max(Message.seq)).where(Message.session_id == session_id)
            )
            max_seq = result.scalar() or 0

            msg = Message(
                session_type=session_type,
                session_id=session_id,
                seq=max_seq + 1,
                sender=sender,
                receiver=receiver,
                message_type=message_type,
                category=category,
                content=content,
                dedupe_key=dedupe_key,
            )
            db.add(msg)
            await db.commit()
            await db.refresh(msg)
            return msg

    @staticmethod
    async def cleanup_expired_messages(session_id: str, ttl_hours: int = 24):
        """Clean up ephemeral messages past TTL (C-011)."""
        async with async_session() as db:
            from sqlalchemy import delete
            cutoff = datetime.now(timezone.utc).timestamp() - (ttl_hours * 3600)

            # Delete old system messages that are not critical
            result = await db.execute(
                select(Message).where(
                    and_(
                        Message.session_id == session_id,
                        Message.message_type == MessageType.SYSTEM,
                        Message.category.in_(["typing", "heartbeat", "ping"]),
                    )
                )
            )
            for msg in result.scalars().all():
                if msg.created_at and msg.created_at.timestamp() < cutoff:
                    await db.delete(msg)
            await db.commit()


# ===== O-009, O-010: Agent Scheduling =====

class AgentScheduler:
    """Agent exclusive execution and available pool management.

    Per O-009: Each agent handles only one main task at a time
    Per O-010: Available agent pool
    """

    @staticmethod
    async def is_agent_available(agent_name: str) -> bool:
        """Check if an agent is available (idle) for task assignment."""
        async with async_session() as db:
            result = await db.execute(
                select(AgentHeartbeat).where(AgentHeartbeat.agent_name == agent_name)
            )
            hb = result.scalars().first()
            if not hb:
                return True  # Not registered = potentially available
            return hb.status == "idle"

    @staticmethod
    async def acquire_agent(agent_name: str, task_id: str, session_id: str) -> bool:
        """Try to acquire an agent for a task (O-009 exclusive)."""
        async with async_session() as db:
            result = await db.execute(
                select(AgentHeartbeat).where(AgentHeartbeat.agent_name == agent_name)
            )
            hb = result.scalars().first()
            if not hb:
                # Register the agent first
                from app.services.heartbeat import heartbeat_service
                await heartbeat_service.register(agent_name, "server")
                result = await db.execute(
                    select(AgentHeartbeat).where(AgentHeartbeat.agent_name == agent_name)
                )
                hb = result.scalars().first()

            if hb.status != "idle":
                return False  # Agent is busy

            hb.status = "busy"
            hb.current_task_id = task_id
            hb.current_session_id = session_id
            hb.last_progress_at = datetime.now(timezone.utc)
            await db.commit()
            return True

    @staticmethod
    async def release_agent(agent_name: str):
        """Release an agent back to the available pool."""
        async with async_session() as db:
            result = await db.execute(
                select(AgentHeartbeat).where(AgentHeartbeat.agent_name == agent_name)
            )
            hb = result.scalars().first()
            if hb:
                hb.status = "idle"
                hb.current_task_id = None
                hb.last_progress_at = datetime.now(timezone.utc)
                await db.commit()

    @staticmethod
    async def get_available_agents(role: Optional[str] = None) -> List[dict]:
        """Get list of available agents, optionally filtered by role (O-010)."""
        async with async_session() as db:
            # Get heartbeats for idle agents
            hb_result = await db.execute(
                select(AgentHeartbeat).where(AgentHeartbeat.status == "idle")
            )
            idle_agents = {hb.agent_name for hb in hb_result.scalars().all()}

            # Get agent templates
            query = select(AgentTemplate)
            if role:
                query = query.where(AgentTemplate.role == role)
            result = await db.execute(query)
            templates = result.scalars().all()

            available = []
            for t in templates:
                if t.name in idle_agents:
                    available.append({
                        "name": t.name,
                        "display_name": t.display_name,
                        "role": t.role,
                        "capabilities": json.loads(t.capabilities_json) if t.capabilities_json else [],
                    })
            return available


# ===== DM-005, DM-006, M-005: Archival =====

async def archive_planning_session(
    session_id: str,
    llm_router: LLMRouter,
    model: str = "gpt-4o-mini",
) -> Optional[str]:
    """Archive a completed planning session.

    Per DM-005: Auto-trigger when session reaches COMPLETED
    Per DM-006: Generate high-quality LLM summary
    Per M-005: Sync summary to both DB and Markdown
    """
    try:
        async with async_session() as db:
            session = await db.get(PlanningSession, session_id)
            if not session:
                return None

            # Collect session data for summarization
            result = await db.execute(
                select(Message).where(
                    Message.session_id == session_id,
                    Message.message_type.in_([
                        MessageType.CHAT, MessageType.PROPOSAL, MessageType.PLAN
                    ]),
                ).order_by(Message.seq.asc())
            )
            messages = result.scalars().all()

            # Build context for LLM summary
            context_parts = [f"原始需求：{session.input_text[:500]}"]
            for msg in messages[-10:]:  # Last 10 meaningful messages
                context_parts.append(f"[{msg.sender}]: {msg.content[:300]}")

            context = "\n\n".join(context_parts)

            # Generate high-quality summary (DM-006)
            summary_messages = [
                LLMMessage(role="system", content="你是一个会议总结专家。请根据以下规划会话内容，生成一份结构化的归档摘要，包含：\n1. 目标\n2. 关键决策\n3. 最终方案要点\n4. 待办事项\n\n用中文输出，200字以内。"),
                LLMMessage(role="user", content=f"请总结以下规划会话：\n\n{context}"),
            ]

            summary_result = await llm_router.call(
                messages=summary_messages,
                model=model,
                max_tokens=500,
                temperature=0.3,
            )
            summary = summary_result.content.strip()

            # Update session summary
            session.summary = summary
            await db.commit()

            # Write memory (M-005: sync to DB -> already in DB via MemoryEntry)
            from app.services.memory import memory_service
            await memory_service.write_planning_summary(
                session_id=session_id,
                goals=session.input_text[:500],
                conclusion=summary,
                proposal_summary=summary[:300],
            )

            # Also write to Markdown file (M-005)
            try:
                from app.core.config import settings
                archive_dir = settings.data_dir / "archives"
                archive_dir.mkdir(parents=True, exist_ok=True)
                archive_file = archive_dir / f"{session_id}.md"

                md_content = f"""# 规划会话归档

> Session ID: `{session_id}`
> 标题: {session.title}
> 归档时间: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}

## 原始需求

{session.input_text}

## 归档摘要

{summary}
"""
                archive_file.write_text(md_content, encoding="utf-8")
            except Exception as e:
                logger.warning(f"Failed to write archive markdown: {e}")

            return summary

    except Exception as e:
        logger.error(f"Failed to archive session {session_id}: {e}")
        return None


# ===== Roundtable Summary Enhancement =====

async def generate_round_summary(
    llm_router: LLMRouter,
    messages_text: str,
    round_number: int,
    model: str = "gpt-4o-mini",
) -> str:
    """Generate a high-quality round summary for roundtable sessions.

    Per X-012: Replace simple 200-char truncation with LLM-generated summary.
    """
    try:
        summary_messages = [
            LLMMessage(role="system", content="你是一个讨论总结专家。请总结本轮讨论的关键观点和结论，100字以内，只输出摘要。"),
            LLMMessage(role="user", content=f"第 {round_number} 轮讨论内容：\n\n{messages_text[:2000]}"),
        ]
        result = await llm_router.call(
            messages=summary_messages,
            model=model,
            max_tokens=200,
            temperature=0.3,
        )
        return result.content.strip()
    except Exception as e:
        logger.warning(f"Failed to generate round summary: {e}")
        # Fallback to simple truncation
        return messages_text[:200] + "..." if len(messages_text) > 200 else messages_text


# ===== Global Singletons =====

message_reliability = MessageReliabilityService()
agent_scheduler = AgentScheduler()
