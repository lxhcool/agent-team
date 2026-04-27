"""Message API + SSE streaming for real-time chat in planning sessions.

Implements:
- C-005: Interrupt/Command message types with receiver field
- UF-008: File upload with actual content storage
"""

import asyncio
import base64
import hashlib
import json
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.core.config import settings
from app.core.database import get_db
from app.models.models import Message, MessageType, PlanningSession, PlanningStatus, Task, Artifact
from app.services.event_bus import event_bus, Event
from app.services.orchestrator import get_orchestrator

router = APIRouter()

# Track running background tasks per session for interrupt support
_running_planning_tasks: dict = {}


# ===== Schemas =====

class SendMessageRequest(BaseModel):
    content: str = Field(..., min_length=1)
    sender: str = Field(default="user")
    message_type: str = Field(default="chat")
    category: Optional[str] = None
    receiver: Optional[str] = None  # C-005: Target agent for command/interrupt


class MessageResponse(BaseModel):
    id: str
    seq: int
    sender: str
    sender_display: Optional[str] = None
    receiver: Optional[str] = None
    message_type: str
    category: Optional[str] = None
    content: str
    created_at: Optional[str] = None

    class Config:
        from_attributes = True


class TaskResponse(BaseModel):
    id: str
    title: str
    description: Optional[str] = None
    status: str
    assigned_agent: Optional[str] = None
    owner_role: Optional[str] = None
    order: int
    dependencies: List[int] = []
    target_paths: List[str] = []
    validation_commands: List[str] = []


# Display name mapping
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


def _map_message_type(msg_type: str) -> MessageType:
    """Map string message type to MessageType enum."""
    mapping = {
        "chat": MessageType.CHAT,
        "system": MessageType.SYSTEM,
        "tool_call": MessageType.TOOL_CALL,
        "tool_result": MessageType.TOOL_RESULT,
        "proposal": MessageType.PROPOSAL,
        "plan": MessageType.PLAN,
        "interrupt": MessageType.INTERRUPT,  # C-005
        "command": MessageType.COMMAND,      # C-005
    }
    return mapping.get(msg_type, MessageType.CHAT)


# ===== Endpoints =====

@router.get("/planning-sessions/{session_id}/messages", response_model=List[MessageResponse])
async def get_messages(
    session_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    after_seq: Optional[int] = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Get messages for a planning session, optionally after a sequence number."""
    session = await db.get(PlanningSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

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
        MessageResponse(
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


@router.post("/planning-sessions/{session_id}/messages", response_model=MessageResponse)
async def send_message(
    session_id: str,
    req: SendMessageRequest,
    db: AsyncSession = Depends(get_db),
):
    """Send a user message to a planning session and trigger agent response if needed."""
    session = await db.get(PlanningSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Get next seq
    result = await db.execute(
        select(func.max(Message.seq)).where(Message.session_id == session_id)
    )
    max_seq = result.scalar() or 0

    msg = Message(
        session_type="planning",
        session_id=session_id,
        seq=max_seq + 1,
        sender=req.sender,
        receiver=req.receiver,
        message_type=_map_message_type(req.message_type),
        category=req.category,
        content=req.content,
    )
    db.add(msg)
    await db.commit()
    await db.refresh(msg)

    # Emit the message to SSE subscribers
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

    # If session is in CREATED status, auto-start the planning
    if session.status == PlanningStatus.CREATED:
        orchestrator = get_orchestrator()
        task = asyncio.create_task(orchestrator.start_planning(session_id))
        _running_planning_tasks[session_id] = task

    return MessageResponse(
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


@router.get("/planning-sessions/{session_id}/tasks", response_model=List[TaskResponse])
async def get_tasks(
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get tasks for a planning session."""
    session = await db.get(PlanningSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    result = await db.execute(
        select(Task)
        .where(Task.session_id == session_id)
        .order_by(Task.order.asc())
    )
    tasks = result.scalars().all()

    return [
        TaskResponse(
            id=t.id,
            title=t.title,
            description=t.description,
            status=t.status.value if t.status else "pending",
            assigned_agent=t.assigned_agent,
            owner_role=t.owner_role,
            order=t.order,
            dependencies=json.loads(t.dependencies_json) if t.dependencies_json else [],
            target_paths=json.loads(t.target_paths_json) if t.target_paths_json else [],
            validation_commands=json.loads(t.validation_commands_json) if t.validation_commands_json else [],
        )
        for t in tasks
    ]


@router.post("/planning-sessions/{session_id}/approve")
async def approve_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Approve the proposal and trigger execution plan generation."""
    session = await db.get(PlanningSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.status != PlanningStatus.AWAITING_APPROVAL:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot approve session in status: {session.status}",
        )

    # Update status to GENERATING_PLAN (sub-state of READY_FOR_EXPORT)
    session.status = PlanningStatus.GENERATING_PLAN
    await db.commit()

    # Kick off plan generation in background
    orchestrator = get_orchestrator()
    task = asyncio.create_task(orchestrator.approve_and_plan(session_id))
    _running_planning_tasks[session_id] = task

    return {"status": "approved", "session_id": session_id}


class ReviseRequest(BaseModel):
    """Request to revise the proposal with user feedback."""
    feedback: str = Field(..., min_length=1, description="用户的修改意见")


@router.post("/planning-sessions/{session_id}/revise")
async def revise_session(
    session_id: str,
    req: ReviseRequest,
    db: AsyncSession = Depends(get_db),
):
    """Reject the proposal and send it back for revision with user feedback.

    The session goes back to GENERATING_PROPOSAL state, and the agents
    will refine the proposal based on the user's feedback, then re-review.
    """
    session = await db.get(PlanningSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.status != PlanningStatus.AWAITING_APPROVAL:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot revise session in status: {session.status}",
        )

    # Save user's revision feedback as a message
    result = await db.execute(
        select(func.max(Message.seq)).where(Message.session_id == session_id)
    )
    max_seq = result.scalar() or 0

    feedback_msg = Message(
        session_type="planning",
        session_id=session_id,
        seq=max_seq + 1,
        sender="user",
        message_type=MessageType.CHAT,
        category="revision_feedback",
        content=f"【退回修改】{req.feedback}",
    )
    db.add(feedback_msg)

    # Transition back to GENERATING_PROPOSAL
    session.status = PlanningStatus.GENERATING_PROPOSAL
    await db.commit()

    # Publish SSE event
    event_bus.publish(session_id, Event(
        event="status",
        data={"status": "generating_proposal", "main_status": "planning", "detail": "用户退回修改，正在根据反馈优化方案..."},
    ))

    # Kick off revision in background
    orchestrator = get_orchestrator()
    task = asyncio.create_task(orchestrator.revise_proposal(session_id, req.feedback))
    _running_planning_tasks[session_id] = task

    return {"status": "revising", "session_id": session_id}


@router.post("/planning-sessions/{session_id}/start")
async def start_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Explicitly start the planning flow for a session."""
    session = await db.get(PlanningSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.status != PlanningStatus.CREATED:
        raise HTTPException(
            status_code=400,
            detail=f"Session already started (status: {session.status})",
        )

    orchestrator = get_orchestrator()
    task = asyncio.create_task(orchestrator.start_planning(session_id))
    _running_planning_tasks[session_id] = task

    return {"status": "started", "session_id": session_id}


@router.post("/planning-sessions/{session_id}/retry")
async def retry_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Retry a failed planning session by resetting status and restarting."""
    session = await db.get(PlanningSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.status != PlanningStatus.FAILED:
        raise HTTPException(
            status_code=400,
            detail=f"Can only retry failed sessions (current: {session.status.value})",
        )

    # Reset to created status
    session.status = PlanningStatus.CREATED
    await db.commit()

    # Restart planning
    orchestrator = get_orchestrator()
    task = asyncio.create_task(orchestrator.start_planning(session_id))
    _running_planning_tasks[session_id] = task

    return {"status": "restarted", "session_id": session_id}


@router.post("/planning-sessions/{session_id}/interrupt")
async def interrupt_planning_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Interrupt an ongoing planning session — cancels background task and sets status to cancelled."""
    # Cancel the background task if running
    task = _running_planning_tasks.pop(session_id, None)
    if task and not task.done():
        task.cancel()

    # Also remove from orchestrator's running set
    orchestrator = get_orchestrator()
    orchestrator._running_sessions.discard(session_id)

    # Update session status
    session = await db.get(PlanningSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.status in (PlanningStatus.PLANNING, PlanningStatus.CREATED):
        session.status = PlanningStatus.CANCELLED
        await db.commit()

    # Notify SSE clients
    event_bus.publish(session_id, Event(
        event="status",
        data={"session_id": session_id, "status": "cancelled"},
    ))

    return {"status": "interrupted", "session_id": session_id}


@router.get("/planning-sessions/{session_id}/stream")
async def sse_stream(session_id: str):
    """SSE endpoint for real-time event streaming."""
    async def event_generator():
        queue = event_bus.subscribe(session_id)
        try:
            while True:
                try:
                    # Wait for event with timeout for keep-alive
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield {
                        "event": event.event,
                        "data": event.data,
                    }
                except asyncio.TimeoutError:
                    # Send keep-alive
                    yield {"event": "ping", "data": ""}
        except asyncio.CancelledError:
            pass
        finally:
            event_bus.unsubscribe(session_id, queue)

    return EventSourceResponse(event_generator())


# ===== UF-008: File Upload with Actual Content Storage =====

# File upload whitelist (same as frontend)
ALLOWED_EXTENSIONS = {
    ".md", ".txt", ".json", ".yaml", ".yml", ".py", ".js", ".ts", ".tsx",
    ".jsx", ".css", ".html", ".sql", ".sh", ".toml", ".xml", ".csv",
    ".env", ".gitignore", ".dockerfile", ".makefile",
    # Image formats for multimodal support
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg",
}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB


@router.post("/planning-sessions/{session_id}/upload")
async def upload_file(
    session_id: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Upload a file attachment to a planning session (UF-008).

    Per UF-008: Actually stores the file content and creates an Artifact record.
    The file is saved to the artifacts directory and referenced by the message.

    File validation:
    - Max 10MB per file (UF-006)
    - Only allowed extensions (UF-007)
    """
    # Validate session exists
    session = await db.get(PlanningSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Validate file extension (UF-007)
    filename = file.filename or "unknown"
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_EXTENSIONS and filename.lower() not in {".gitignore", ".dockerfile", ".makefile"}:
        raise HTTPException(
            status_code=400,
            detail=f"File type '{ext}' not allowed. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
        )

    # Read and validate file size (UF-006)
    content_bytes = await file.read()
    if len(content_bytes) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"File too large: {len(content_bytes)} bytes (max {MAX_FILE_SIZE} bytes)"
        )

    # Determine MIME type
    mime_type = file.content_type or "application/octet-stream"
    text_extensions = {".md", ".txt", ".json", ".yaml", ".yml", ".py", ".js", ".ts",
                       ".tsx", ".jsx", ".css", ".html", ".sql", ".sh", ".toml", ".xml",
                       ".csv", ".env", ".gitignore", ".dockerfile", ".makefile"}
    if ext in text_extensions:
        mime_type = "text/plain"

    # Save file to artifacts directory
    artifact_dir = settings.artifacts_dir / session_id / "uploads"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    file_path = artifact_dir / filename

    # Handle duplicate filenames
    counter = 1
    original_stem = file_path.stem
    original_suffix = file_path.suffix
    while file_path.exists():
        file_path = artifact_dir / f"{original_stem}_{counter}{original_suffix}"
        counter += 1

    # Write file content
    try:
        content_str = content_bytes.decode("utf-8")
        file_path.write_text(content_str, encoding="utf-8")
    except UnicodeDecodeError:
        file_path.write_bytes(content_bytes)

    # Compute checksum
    checksum = hashlib.sha256(content_bytes).hexdigest()

    # Create Artifact record
    artifact = Artifact(
        session_type="planning",
        session_id=session_id,
        artifact_type="attachment",
        filename=file_path.name,
        path=str(file_path),
        mime_type=mime_type,
        size_bytes=len(content_bytes),
        checksum=checksum,
        source="upload",
        created_by="user",
    )
    db.add(artifact)
    await db.commit()
    await db.refresh(artifact)

    # Emit event for real-time UI update
    event_bus.publish(session_id, Event(
        event="file_uploaded",
        data={
            "artifact_id": artifact.id,
            "filename": file_path.name,
            "size_bytes": len(content_bytes),
            "mime_type": mime_type,
        },
    ))

    return {
        "id": artifact.id,
        "filename": file_path.name,
        "size_bytes": len(content_bytes),
        "mime_type": mime_type,
        "checksum": checksum,
    }
