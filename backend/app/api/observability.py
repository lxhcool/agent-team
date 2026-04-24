"""Observability API - Heartbeat, Checkpoint, and Memory endpoints."""

import asyncio
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.core.database import get_db
from app.models.models import AgentHeartbeat, Checkpoint, MemoryEntry
from app.services.heartbeat import heartbeat_service
from app.services.memory import memory_service

router = APIRouter()


# ===== Heartbeat Schemas & Endpoints =====

class HeartbeatRequest(BaseModel):
    agent_name: str
    agent_type: str = Field(default="server")
    current_task_id: Optional[str] = None
    current_session_id: Optional[str] = None
    status: Optional[str] = None


class HeartbeatResponse(BaseModel):
    agent_name: str
    agent_type: str
    status: str
    current_task_id: Optional[str] = None
    current_session_id: Optional[str] = None
    heartbeat_at: Optional[str] = None
    last_progress_at: Optional[str] = None


class LivenessReport(BaseModel):
    agent_name: str
    agent_type: str
    status: str
    current_task_id: Optional[str] = None
    current_session_id: Optional[str] = None
    heartbeat_at: Optional[str] = None
    last_progress_at: Optional[str] = None
    is_unresponsive: bool


@router.post("/heartbeat", response_model=HeartbeatResponse)
async def send_heartbeat(
    req: HeartbeatRequest,
    db: AsyncSession = Depends(get_db),
):
    """Send an agent heartbeat to report liveness."""
    hb = await heartbeat_service.register(req.agent_name, req.agent_type)
    hb = await heartbeat_service.heartbeat(
        agent_name=req.agent_name,
        current_task_id=req.current_task_id,
        current_session_id=req.current_session_id,
        status=req.status,
    )
    if not hb:
        raise HTTPException(status_code=404, detail="Agent not registered")

    return HeartbeatResponse(
        agent_name=hb.agent_name,
        agent_type=hb.agent_type,
        status=hb.status,
        current_task_id=hb.current_task_id,
        current_session_id=hb.current_session_id,
        heartbeat_at=hb.heartbeat_at.isoformat() if hb.heartbeat_at else None,
        last_progress_at=hb.last_progress_at.isoformat() if hb.last_progress_at else None,
    )


@router.get("/heartbeat/liveness", response_model=List[LivenessReport])
async def check_liveness(
    timeout_seconds: int = 120,
):
    """Check which agents are unresponsive."""
    reports = await heartbeat_service.check_liveness(timeout_seconds)
    return [LivenessReport(**r) for r in reports]


@router.get("/heartbeat", response_model=List[HeartbeatResponse])
async def list_heartbeats(
    agent_type: Optional[str] = None,
):
    """List all agent heartbeats."""
    heartbeats = await heartbeat_service.list_heartbeats(agent_type)
    return [
        HeartbeatResponse(
            agent_name=hb.agent_name,
            agent_type=hb.agent_type,
            status=hb.status,
            current_task_id=hb.current_task_id,
            current_session_id=hb.current_session_id,
            heartbeat_at=hb.heartbeat_at.isoformat() if hb.heartbeat_at else None,
            last_progress_at=hb.last_progress_at.isoformat() if hb.last_progress_at else None,
        )
        for hb in heartbeats
    ]


# ===== Checkpoint Schemas & Endpoints =====

class CheckpointResponse(BaseModel):
    id: str
    session_type: str
    session_id: str
    task_id: Optional[str] = None
    checkpoint_type: str
    label: Optional[str] = None
    state_json: Optional[str] = None
    created_by: Optional[str] = None
    created_at: Optional[str] = None


class CreateCheckpointRequest(BaseModel):
    session_type: str
    session_id: str
    task_id: Optional[str] = None
    checkpoint_type: str = Field(default="business")
    label: Optional[str] = None
    state_json: Optional[str] = None
    created_by: Optional[str] = None


@router.get("/checkpoints", response_model=List[CheckpointResponse])
async def list_checkpoints(
    session_id: Optional[str] = None,
    session_type: Optional[str] = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    """List checkpoints, optionally filtered by session."""
    query = select(Checkpoint)
    if session_id:
        query = query.where(Checkpoint.session_id == session_id)
    if session_type:
        query = query.where(Checkpoint.session_type == session_type)
    query = query.order_by(Checkpoint.created_at.desc()).limit(limit)

    result = await db.execute(query)
    checkpoints = result.scalars().all()
    return [
        CheckpointResponse(
            id=c.id,
            session_type=c.session_type,
            session_id=c.session_id,
            task_id=c.task_id,
            checkpoint_type=c.checkpoint_type,
            label=c.label,
            state_json=c.state_json,
            created_by=c.created_by,
            created_at=c.created_at.isoformat() if c.created_at else None,
        )
        for c in checkpoints
    ]


@router.post("/checkpoints", response_model=CheckpointResponse)
async def create_checkpoint(
    req: CreateCheckpointRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create a manual checkpoint."""
    checkpoint = Checkpoint(
        session_type=req.session_type,
        session_id=req.session_id,
        task_id=req.task_id,
        checkpoint_type=req.checkpoint_type,
        label=req.label,
        state_json=req.state_json,
        created_by=req.created_by,
    )
    db.add(checkpoint)
    await db.commit()
    await db.refresh(checkpoint)

    return CheckpointResponse(
        id=checkpoint.id,
        session_type=checkpoint.session_type,
        session_id=checkpoint.session_id,
        task_id=checkpoint.task_id,
        checkpoint_type=checkpoint.checkpoint_type,
        label=checkpoint.label,
        state_json=checkpoint.state_json,
        created_by=checkpoint.created_by,
        created_at=checkpoint.created_at.isoformat() if checkpoint.created_at else None,
    )


# ===== Memory Schemas & Endpoints =====

class MemoryEntryResponse(BaseModel):
    id: str
    session_type: str
    session_id: Optional[str] = None
    entry_type: str
    category: Optional[str] = None
    content: str
    metadata_json: Optional[str] = None
    retention_policy: str
    created_at: Optional[str] = None


class WriteMemoryRequest(BaseModel):
    session_type: str
    session_id: Optional[str] = None
    entry_type: str = Field(default="summary")
    category: Optional[str] = None
    content: str
    retention_policy: str = Field(default="session")


@router.get("/memory", response_model=List[MemoryEntryResponse])
async def query_memory(
    session_type: Optional[str] = None,
    session_id: Optional[str] = None,
    entry_type: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = 20,
):
    """Query memory entries. Per requirements: on-demand retrieval only."""
    entries = await memory_service.query(
        session_type=session_type,
        session_id=session_id,
        entry_type=entry_type,
        category=category,
        limit=limit,
    )
    return [
        MemoryEntryResponse(
            id=e.id,
            session_type=e.session_type,
            session_id=e.session_id,
            entry_type=e.entry_type,
            category=e.category,
            content=e.content,
            metadata_json=e.metadata_json,
            retention_policy=e.retention_policy,
            created_at=e.created_at.isoformat() if e.created_at else None,
        )
        for e in entries
    ]


@router.post("/memory", response_model=MemoryEntryResponse)
async def write_memory(req: WriteMemoryRequest):
    """Write a memory entry."""
    entry = await memory_service.write(
        session_type=req.session_type,
        session_id=req.session_id,
        entry_type=req.entry_type,
        content=req.content,
        category=req.category,
        retention_policy=req.retention_policy,
    )
    return MemoryEntryResponse(
        id=entry.id,
        session_type=entry.session_type,
        session_id=entry.session_id,
        entry_type=entry.entry_type,
        category=entry.category,
        content=entry.content,
        metadata_json=entry.metadata_json,
        retention_policy=entry.retention_policy,
        created_at=entry.created_at.isoformat() if entry.created_at else None,
    )


@router.get("/memory/conclusions", response_model=List[MemoryEntryResponse])
async def get_conclusions(
    session_type: Optional[str] = None,
    limit: int = 10,
):
    """Get conclusion-type memories (most important for reuse)."""
    entries = await memory_service.get_conclusions(
        session_type=session_type,
        limit=limit,
    )
    return [
        MemoryEntryResponse(
            id=e.id,
            session_type=e.session_type,
            session_id=e.session_id,
            entry_type=e.entry_type,
            category=e.category,
            content=e.content,
            metadata_json=e.metadata_json,
            retention_policy=e.retention_policy,
            created_at=e.created_at.isoformat() if e.created_at else None,
        )
        for e in entries
    ]
