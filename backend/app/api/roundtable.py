"""Roundtable Discussion API - multi-agent discussion sessions."""

import asyncio
import json
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.models import (
    Message,
    MessageType,
    PlanningSession,
    PlanningStatus,
    RoundtableSession,
    RoundtableStatus,
    Task,
    TaskStatus,
    User,
    Workspace,
    WorkspaceMember,
    WorkspaceMemberRole,
    WorkspaceStageKey,
)
from app.api.workspaces import _new_binding_id, _seed_stages
from app.services.event_bus import event_bus, Event

router = APIRouter()

# Track running background tasks per session for interrupt support
_running_tasks: dict[str, asyncio.Task] = {}


# ===== Schemas =====

class CreateRoundtableRequest(BaseModel):
    topic: str = Field(..., min_length=1, max_length=500)
    max_rounds: int = Field(default=5, ge=1, le=20)


class RoundtableSessionResponse(BaseModel):
    id: str
    user_id: str
    topic: str
    status: RoundtableStatus
    max_rounds: int
    current_round: int
    summary: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class SendMessageRoundtableRequest(BaseModel):
    content: str = Field(..., min_length=1)
    sender: str = Field(default="user")
    message_type: str = Field(default="chat")
    category: Optional[str] = None


class RoundtableMessageResponse(BaseModel):
    id: str
    seq: int
    sender: str
    sender_display: Optional[str] = None
    receiver: Optional[str] = None
    message_type: str
    category: Optional[str] = None
    content: str
    created_at: Optional[str] = None


# Display name mapping (reuse from messages.py)
DISPLAY_NAMES = {
    "user": "你",
    "leader": "Leader",
    "researcher": "Researcher",
    "analyst": "Analyst",
    "planner": "Planner",
    "architect": "Architect",
    "developer": "Developer",
    "reviewer": "Reviewer",
    "tester": "Tester",
    "system": "System",
    "tool": "Tool",
}


# ===== Endpoints =====

@router.get("/roundtable-sessions", response_model=List[RoundtableSessionResponse])
async def list_roundtable_sessions(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List all roundtable sessions for the authenticated user."""
    result = await db.execute(
        select(RoundtableSession)
        .where(RoundtableSession.user_id == user.id)
        .order_by(RoundtableSession.created_at.desc())
    )
    sessions = result.scalars().all()
    return [_roundtable_to_response(s) for s in sessions]


@router.delete("/roundtable-sessions/{session_id}")
async def delete_roundtable_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Delete a roundtable session and its messages."""
    session = await db.get(RoundtableSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Roundtable session not found")
    # Delete associated messages
    from app.models.models import Message
    msg_result = await db.execute(
        select(Message).where(Message.session_id == session_id)
    )
    for msg in msg_result.scalars().all():
        await db.delete(msg)
    await db.delete(session)
    await db.commit()
    return {"ok": True}


@router.post("/roundtable-sessions", response_model=RoundtableSessionResponse)
async def create_roundtable_session(
    req: CreateRoundtableRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Create a new roundtable discussion session.

    P1-3: If participants are specified, auto-schedule multi-agent discussion.
    """
    session = RoundtableSession(
        user_id=user.id,
        topic=req.topic,
        max_rounds=req.max_rounds,
        current_round=0,
        status=RoundtableStatus.ACTIVE,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return _roundtable_to_response(session)


class AutoDiscussRequest(BaseModel):
    """P1-3: Request to auto-schedule multi-agent discussion."""
    participants: List[str] = Field(default=["architect", "developer", "reviewer"])
    rounds: int = Field(default=3, ge=1, le=10)


@router.post("/roundtable-sessions/{session_id}/auto-discuss")
async def auto_discuss(
    session_id: str,
    req: AutoDiscussRequest,
    db: AsyncSession = Depends(get_db),
):
    """P1-3: Auto-schedule multi-agent roundtable discussion.

    Automatically orchestrates multiple agents to take turns discussing
    the roundtable topic, with per-round summaries.
    """
    import asyncio
    from app.services.agents import AgentFactory
    from app.llm.router import llm_router
    from app.services.event_bus import event_bus, Event

    session = await db.get(RoundtableSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Roundtable session not found")
    if session.status != RoundtableStatus.ACTIVE:
        raise HTTPException(status_code=400, detail=f"Session is not active (status: {session.status.value})")

    # Start the auto-discussion in background
    task = asyncio.create_task(_run_auto_discussion(
        session_id=session_id,
        topic=session.topic,
        participants=req.participants,
        max_rounds=min(req.rounds, session.max_rounds),
        user_id=session.user_id,
    ))
    _running_tasks[session_id] = task

    return {"status": "started", "session_id": session_id, "participants": req.participants}


@router.post("/roundtable-sessions/{session_id}/interrupt")
async def interrupt_roundtable(
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Interrupt an ongoing roundtable discussion — cancels background task and sets status to completed."""
    # Cancel the background task if running
    task = _running_tasks.pop(session_id, None)
    if task and not task.done():
        task.cancel()

    # Update session status
    session = await db.get(RoundtableSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Roundtable session not found")
    if session.status == RoundtableStatus.ACTIVE:
        session.status = RoundtableStatus.COMPLETED
        session.summary = "用户中断了讨论"
        await db.commit()

    # Notify SSE clients
    event_bus.publish(session_id, Event(
        event="roundtable_completed",
        data={"session_id": session_id, "reason": "user_interrupted"},
    ))

    return {"status": "interrupted", "session_id": session_id}


async def _run_auto_discussion(
    session_id: str,
    topic: str,
    participants: List[str],
    max_rounds: int,
    user_id: Optional[str] = None,
):
    """P1-3: Run multi-agent auto-discussion in the background."""
    import asyncio
    import logging
    from app.services.agents import AgentFactory
    from app.llm.router import llm_router
    from app.services.event_bus import event_bus, Event
    from app.core.database import async_session as db_session

    logger = logging.getLogger(__name__)
    logger.info(f"[Roundtable] Starting auto-discussion for session {session_id}, participants={participants}")

    # Ensure providers are loaded before creating agents
    try:
        async with db_session() as db:
            await llm_router.load_providers(db, user_id=user_id)
    except Exception as e:
        logger.warning(f"[Roundtable] Failed to load providers: {e}")

    # Resolve model and provider the same way planning mode does
    # (reads from ModelSettings table, falls back to first enabled provider with API key)
    from app.services.orchestrator import PlanningOrchestrator
    orchestrator = PlanningOrchestrator(llm_router, event_bus)
    model, provider = await orchestrator._resolve_model_and_provider(user_id=user_id)
    logger.info(f"[Roundtable] Resolved model={model}, provider={provider}")

    factory = AgentFactory(llm_router, event_bus)

    # Create agent instances for participants, with resolved model/provider overrides
    # First check if templates exist; if not, try to initialize them
    agents = []
    missing_templates = []
    for name in participants:
        try:
            agent = await factory.create_by_name(
                name,
                model_override=model,
                provider_override=provider,
            )
            agents.append(agent)
        except ValueError:
            missing_templates.append(name)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Failed to create agent {name}: {e}")

    # If some templates are missing, try to initialize built-in agents and retry
    if missing_templates:
        logger.warning(f"[Roundtable] Missing agent templates: {missing_templates}, attempting init...")
        try:
            async with db_session() as db:
                from app.api.agents import init_builtin_agents
                await init_builtin_agents(db)
            # Retry creating missing agents
            for name in missing_templates:
                try:
                    agent = await factory.create_by_name(
                        name,
                        model_override=model,
                        provider_override=provider,
                    )
                    agents.append(agent)
                    logger.info(f"[Roundtable] Successfully created agent {name} after init")
                except Exception as e:
                    logging.getLogger(__name__).warning(f"Still failed to create agent {name} after init: {e}")
        except Exception as e:
            logging.getLogger(__name__).warning(f"Failed to init built-in agents: {e}")

    if not agents:
        async with db_session() as db:
            result = await db.execute(
                select(RoundtableSession).where(RoundtableSession.id == session_id)
            )
            s = result.scalars().first()
            if s and s.status == RoundtableStatus.ACTIVE:
                s.status = RoundtableStatus.COMPLETED
                await db.commit()
        event_bus.publish(session_id, Event(
            event="error",
            data={"message": f"无法创建讨论 Agent（{', '.join(participants)}），请检查 LLM 配置是否正确，或尝试重启后端服务以初始化 Agent 模板"},
        ))
        event_bus.publish(session_id, Event(
            event="roundtable_completed",
            data={"session_id": session_id, "reason": "no_agents_available"},
        ))
        _running_tasks.pop(session_id, None)
        return

    # Build initial context
    context = f"讨论主题：{topic}\n\n请从你的专业角度出发，对上述主题发表看法。"

    for round_num in range(1, max_rounds + 1):
        # Check if session is still active
        async with db_session() as db:
            from sqlalchemy import select
            result = await db.execute(
                select(RoundtableSession).where(RoundtableSession.id == session_id)
            )
            session = result.scalars().first()
            if not session or session.status != RoundtableStatus.ACTIVE:
                break

            # Update round counter
            session.current_round = round_num
            await db.commit()

        # Add system message for round start
        async with db_session() as db:
            from sqlalchemy import select, func
            result = await db.execute(
                select(func.max(Message.seq)).where(Message.session_id == session_id)
            )
            max_seq = result.scalar() or 0

            round_msg = Message(
                session_type="roundtable",
                session_id=session_id,
                seq=max_seq + 1,
                sender="system",
                message_type=MessageType.SYSTEM,
                content=f"--- Round {round_num} started ---",
            )
            db.add(round_msg)
            await db.commit()

        event_bus.publish(session_id, Event(
            event="new_round",
            data={"session_id": session_id, "current_round": round_num, "max_rounds": max_rounds},
        ))

        # Each agent takes a turn
        for agent in agents:
            try:
                # Collect previous messages for context
                prev_context = context
                async with db_session() as db:
                    result = await db.execute(
                        select(Message).where(
                            Message.session_id == session_id,
                            Message.sender != "system",
                        ).order_by(Message.seq.desc()).limit(10)
                    )
                    prev_msgs = list(reversed(result.scalars().all()))
                    if prev_msgs:
                        prev_context = "已有讨论：\n"
                        for pm in prev_msgs:
                            prev_context += f"[{pm.sender}]: {pm.content[:300]}\n"
                        prev_context += f"\n讨论主题：{topic}\n\n请基于以上讨论，继续从你的专业角度发表看法。"

                response = await agent.respond(session_id, prev_context, topic)
                # Small delay between agents
                await asyncio.sleep(0.5)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"Agent {agent.name} failed in round {round_num}: {e}")
                # Publish error event so frontend can see the failure
                error_msg = str(e)
                if "No LLM provider" in error_msg:
                    error_msg = "未配置 LLM 服务，请先在设置中配置 API Key"
                elif "API Key" in error_msg or "Bearer" in error_msg or "Illegal header" in error_msg:
                    error_msg = "API Key 未配置或无效，请在设置中检查 LLM 服务的 API Key"
                elif "Model does not exist" in error_msg or "model_not_found" in error_msg.lower():
                    error_msg = "模型名称不存在，请在设置中检查 LLM 服务的默认模型名称是否正确"
                elif "not found" in error_msg.lower():
                    error_msg = f"Agent '{agent.name}' 模板未找到，请重新初始化"
                event_bus.publish(session_id, Event(
                    event="error",
                    data={"message": f"Agent {agent.display_name} 回复失败: {error_msg}"},
                ))
                continue

    # Mark session as completed
    async with db_session() as db:
        result = await db.execute(
            select(RoundtableSession).where(RoundtableSession.id == session_id)
        )
        session = result.scalars().first()
        if session and session.status == RoundtableStatus.ACTIVE:
            session.status = RoundtableStatus.COMPLETED
            await db.commit()

    event_bus.publish(session_id, Event(
        event="roundtable_completed",
        data={"session_id": session_id, "reason": "auto_discuss_finished"},
    ))

    # Clean up task reference
    _running_tasks.pop(session_id, None)


@router.get("/roundtable-sessions/{session_id}", response_model=RoundtableSessionResponse)
async def get_roundtable_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get a roundtable session by ID, including messages."""
    session = await db.get(RoundtableSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Roundtable session not found")
    return _roundtable_to_response(session)


@router.get("/roundtable-sessions/{session_id}/messages", response_model=List[RoundtableMessageResponse])
async def get_roundtable_messages(
    session_id: str,
    limit: int = 100,
    after_seq: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
):
    """Get messages for a roundtable session."""
    session = await db.get(RoundtableSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Roundtable session not found")

    query = (
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.seq.asc())
    )
    if after_seq is not None:
        query = query.where(Message.seq > after_seq)
    query = query.limit(limit)

    result = await db.execute(query)
    messages = result.scalars().all()

    return [
        RoundtableMessageResponse(
            id=m.id,
            seq=m.seq,
            sender=m.sender,
            sender_display=DISPLAY_NAMES.get(m.sender, m.sender),
            receiver=m.receiver,
            message_type=m.message_type.value if m.message_type else "chat",
            category=m.category,
            content=m.content,
            created_at=m.created_at.isoformat() if m.created_at else None,
        )
        for m in messages
    ]


@router.post("/roundtable-sessions/{session_id}/messages", response_model=RoundtableMessageResponse)
async def send_roundtable_message(
    session_id: str,
    req: SendMessageRoundtableRequest,
    db: AsyncSession = Depends(get_db),
):
    """Send a message to a roundtable session."""
    session = await db.get(RoundtableSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Roundtable session not found")

    if session.status != RoundtableStatus.ACTIVE:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot send message to session in status: {session.status.value}",
        )

    # Get next seq
    result = await db.execute(
        select(func.max(Message.seq)).where(Message.session_id == session_id)
    )
    max_seq = result.scalar() or 0

    msg = Message(
        session_type="roundtable",
        session_id=session_id,
        seq=max_seq + 1,
        sender=req.sender,
        message_type=MessageType.CHAT if req.message_type == "chat" else MessageType.SYSTEM,
        category=req.category,
        content=req.content,
    )
    db.add(msg)
    await db.commit()
    await db.refresh(msg)

    # Publish SSE event
    event_bus.publish(session_id, Event(
        event="message",
        data={
            "id": msg.id,
            "seq": msg.seq,
            "sender": msg.sender,
            "sender_display": DISPLAY_NAMES.get(msg.sender, msg.sender),
            "receiver": msg.receiver,
            "message_type": msg.message_type.value if msg.message_type else "chat",
            "category": msg.category,
            "content": msg.content,
            "created_at": msg.created_at.isoformat() if msg.created_at else None,
        },
    ))

    return RoundtableMessageResponse(
        id=msg.id,
        seq=msg.seq,
        sender=msg.sender,
        sender_display=DISPLAY_NAMES.get(msg.sender, msg.sender),
        receiver=msg.receiver,
        message_type=msg.message_type.value if msg.message_type else "chat",
        category=msg.category,
        content=msg.content,
        created_at=msg.created_at.isoformat() if msg.created_at else None,
    )


@router.post("/roundtable-sessions/{session_id}/round", response_model=RoundtableSessionResponse)
async def start_new_round(
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Start a new round of discussion in the roundtable session."""
    session = await db.get(RoundtableSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Roundtable session not found")

    if session.status != RoundtableStatus.ACTIVE:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot start new round for session in status: {session.status.value}",
        )

    if session.current_round >= session.max_rounds:
        # Auto-complete when max rounds reached
        session.status = RoundtableStatus.COMPLETED
        await db.commit()
        await db.refresh(session)

        event_bus.publish(session_id, Event(
            event="roundtable_completed",
            data={"session_id": session_id, "reason": "max_rounds_reached"},
        ))

        return _roundtable_to_response(session)

    session.current_round += 1
    await db.commit()
    await db.refresh(session)

    # Add system message for new round
    result = await db.execute(
        select(func.max(Message.seq)).where(Message.session_id == session_id)
    )
    max_seq = result.scalar() or 0

    round_summary = ""
    if session.current_round > 1:
        # Generate summary of previous round's messages
        prev_round_start_seq = 0
        prev_msgs_result = await db.execute(
            select(Message).where(
                Message.session_id == session_id,
                Message.message_type == MessageType.SYSTEM,
                Message.content.like("%Round%started%"),
            ).order_by(Message.seq.desc())
        )
        prev_round_msgs = prev_msgs_result.scalars().all()
        round_starts = [m.seq for m in prev_round_msgs]
        if len(round_starts) >= 1:
            prev_round_start_seq = round_starts[0]

        prev_result = await db.execute(
            select(Message).where(
                Message.session_id == session_id,
                Message.seq >= prev_round_start_seq,
            ).order_by(Message.seq.asc())
        )
        prev_messages = prev_result.scalars().all()
        summary_parts = []
        for pm in prev_messages:
            if pm.sender != "system":
                summary_parts.append(f"[{pm.sender}]: {pm.content[:200]}")
        if summary_parts:
            # X-012: Try LLM-generated summary, fallback to simple truncation
            round_summary_text = "; ".join(summary_parts[-5:])
            try:
                from app.services.session_manager import generate_round_summary
                from app.llm.router import llm_router
                round_summary = f" | Previous round summary: {await generate_round_summary(llm_router, round_summary_text, session.current_round)}"
            except Exception:
                round_summary = f" | Previous round summary: {round_summary_text}"

    msg = Message(
        session_type="roundtable",
        session_id=session_id,
        seq=max_seq + 1,
        sender="system",
        message_type=MessageType.SYSTEM,
        content=f"--- Round {session.current_round} started ---{round_summary}",
    )
    db.add(msg)
    await db.commit()

    # Publish SSE event
    event_bus.publish(session_id, Event(
        event="new_round",
        data={
            "session_id": session_id,
            "current_round": session.current_round,
            "max_rounds": session.max_rounds,
        },
    ))

    return _roundtable_to_response(session)


@router.post("/roundtable-sessions/{session_id}/promote")
async def promote_to_planning(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Promote a roundtable session into a workspace-backed planning record."""
    session = await db.get(RoundtableSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Roundtable session not found")
    if session.user_id != user.id:
        raise HTTPException(status_code=404, detail="Roundtable session not found")

    # Allow re-promoting even if already converted (e.g. planning session was deleted)

    # Get all messages for the roundtable
    result = await db.execute(
        select(Message).where(Message.session_id == session_id).order_by(Message.seq.asc())
    )
    messages = result.scalars().all()

    # Build context from messages
    context_parts = [f"Topic: {session.topic}"]
    for msg in messages:
        context_parts.append(f"[{msg.sender}]: {msg.content}")
    context = "\n\n".join(context_parts)

    # Create a workspace so the discussion result lands in the platform's main task container.
    workspace = Workspace(
        owner_id=session.user_id,
        name=session.topic[:255],
        description=session.summary or session.topic,
        target_platform="general",
        binding_id=_new_binding_id(),
        storage_mode="server",
        created_by=session.user_id,
        current_stage=WorkspaceStageKey.REQUIREMENTS,
    )
    db.add(workspace)
    await db.flush()

    db.add(WorkspaceMember(
        workspace_id=workspace.id,
        user_id=session.user_id,
        role=WorkspaceMemberRole.OWNER,
    ))
    db.add_all(_seed_stages(workspace, session.summary or session.topic))

    # Create a new Planning Session
    planning = PlanningSession(
        workspace_id=workspace.id,
        title=f"From Roundtable: {session.topic}",
        user_id=session.user_id,
        input_text=context,
        mode="planning",
        status=PlanningStatus.CREATED,
    )
    db.add(planning)
    await db.flush()  # Flush to get planning.id before commit

    # Mark roundtable as converted
    session.status = RoundtableStatus.CONVERTED
    session.summary = f"Promoted to Workspace {workspace.id} and Planning Session {planning.id}"
    await db.commit()
    await db.refresh(planning)

    return {
        "status": "promoted",
        "roundtable_id": session_id,
        "workspace_id": workspace.id,
        "planning_session_id": planning.id,
    }


@router.post("/roundtable-sessions/{session_id}/complete")
async def complete_roundtable_session(
    session_id: str,
    summary: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """Manually complete a roundtable session."""
    session = await db.get(RoundtableSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Roundtable session not found")

    if session.status != RoundtableStatus.ACTIVE:
        raise HTTPException(
            status_code=400,
            detail=f"Session is not active (status: {session.status.value})",
        )

    session.status = RoundtableStatus.COMPLETED
    if summary is not None:
        session.summary = summary
    await db.commit()

    event_bus.publish(session_id, Event(
        event="roundtable_completed",
        data={"session_id": session_id, "summary": summary},
    ))

    return {"status": "completed", "session_id": session_id}


@router.post("/roundtable-sessions/{session_id}/consensus")
async def detect_consensus(
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """P2-I-006: Detect if roundtable participants have reached consensus."""
    session = await db.get(RoundtableSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Roundtable session not found")

    from app.services.execution import consensus_detector
    analysis = await consensus_detector.detect_consensus(session_id)

    if analysis is None:
        raise HTTPException(status_code=500, detail="Consensus detection failed")

    return analysis


@router.get("/roundtable-sessions/{session_id}/stream")
async def roundtable_sse_stream(session_id: str):
    """SSE endpoint for real-time event streaming in roundtable sessions."""
    async def event_generator():
        queue = event_bus.subscribe(session_id)
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield {
                        "event": event.event,
                        "data": event.data,
                    }
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": ""}
        except asyncio.CancelledError:
            pass
        finally:
            event_bus.unsubscribe(session_id, queue)

    return EventSourceResponse(event_generator())


# ===== Helpers =====

def _roundtable_to_response(session: RoundtableSession) -> RoundtableSessionResponse:
    return RoundtableSessionResponse(
        id=session.id,
        user_id=session.user_id,
        topic=session.topic,
        status=session.status,
        max_rounds=session.max_rounds,
        current_round=session.current_round,
        summary=session.summary,
        created_at=session.created_at.isoformat() if session.created_at else None,
        updated_at=session.updated_at.isoformat() if session.updated_at else None,
    )
