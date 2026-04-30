"""Planning Session API endpoints."""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.database import get_db
from app.api.authz import get_owned_planning_session
from app.models.models import PlanningSession, PlanningStatus, User, WorkspaceMember

router = APIRouter()


# ===== Request / Response schemas =====

class CreatePlanningRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    input_text: str = Field(..., min_length=1)
    mode: str = Field(default="planning")
    workspace_id: Optional[str] = None


class PlanningSessionResponse(BaseModel):
    id: str
    workspace_id: Optional[str] = None
    title: str
    user_id: str
    status: PlanningStatus
    mode: str
    input_text: str
    summary: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    class Config:
        from_attributes = True

    @classmethod
    def from_orm(cls, obj):
        return cls(
            id=obj.id,
            workspace_id=obj.workspace_id,
            title=obj.title,
            user_id=obj.user_id,
            status=obj.status,
            mode=obj.mode,
            input_text=obj.input_text,
            summary=obj.summary,
            created_at=obj.created_at.isoformat() if obj.created_at else None,
            updated_at=obj.updated_at.isoformat() if obj.updated_at else None,
        )


# ===== Endpoints =====

@router.post("/planning-sessions", response_model=PlanningSessionResponse)
async def create_planning_session(
    req: CreatePlanningRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Create a new Planning Session with user requirement."""
    if req.workspace_id:
        member_result = await db.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == req.workspace_id,
                WorkspaceMember.user_id == user.id,
            )
        )
        if not member_result.scalars().first():
            raise HTTPException(status_code=404, detail="Workspace not found")

    session = PlanningSession(
        workspace_id=req.workspace_id,
        title=req.title,
        user_id=user.id,
        input_text=req.input_text,
        mode=req.mode,
        status=PlanningStatus.CREATED,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return PlanningSessionResponse.from_orm(session)


@router.get("/planning-sessions", response_model=List[PlanningSessionResponse])
async def list_planning_sessions(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List all Planning Sessions for the authenticated user."""
    result = await db.execute(
        select(PlanningSession)
        .where(PlanningSession.user_id == user.id)
        .order_by(PlanningSession.created_at.desc())
    )
    sessions = result.scalars().all()
    return [PlanningSessionResponse.from_orm(s) for s in sessions]


@router.get("/planning-sessions/{session_id}", response_model=PlanningSessionResponse)
async def get_planning_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get a Planning Session by ID."""
    session = await get_owned_planning_session(db, session_id, user)
    return PlanningSessionResponse.from_orm(session)


@router.post("/planning-sessions/{session_id}/cancel")
async def cancel_planning_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Cancel a Planning Session (P1-1: with state transition validation)."""
    from app.models.models import validate_planning_transition
    session = await get_owned_planning_session(db, session_id, user)
    if not validate_planning_transition(session.status, PlanningStatus.CANCELLED):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel session in status '{session.status.value}'",
        )
    session.status = PlanningStatus.CANCELLED
    await db.commit()
    return {"status": "cancelled", "session_id": session_id}


@router.delete("/planning-sessions/{session_id}")
async def delete_planning_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Delete a Planning Session and all related data."""
    session = await get_owned_planning_session(db, session_id, user)

    from app.models.models import Message, Task, Artifact, LLMCall
    # Delete related records
    for Model in [Message, Task, Artifact, LLMCall]:
        result = await db.execute(
            select(Model).where(Model.session_id == session_id)
        )
        for obj in result.scalars().all():
            await db.delete(obj)

    await db.delete(session)
    await db.commit()
    return {"status": "deleted"}


# ===== P2-SS-009: Session Pause/Resume API =====

@router.post("/planning-sessions/{session_id}/pause")
async def pause_planning_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """P2-SS-009: Pause a Planning Session (save checkpoint)."""
    await get_owned_planning_session(db, session_id, user)
    from app.services.execution import session_pause_service
    session = await session_pause_service.pause_planning_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Planning session not found")
    return {"status": "paused", "session_id": session_id}


# ===== P2-EX-001: Webhook Registration API =====

class RegisterWebhookRequest(BaseModel):
    url: str = Field(..., description="Webhook callback URL")
    events: Optional[List[str]] = Field(default=None, description="Event types to subscribe to")
    headers: Optional[dict] = Field(default=None, description="Custom HTTP headers")
    secret: Optional[str] = Field(default=None, description="Secret for payload signing")


@router.post("/planning-sessions/{session_id}/webhooks")
async def register_webhook(
    session_id: str,
    req: RegisterWebhookRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """P2-EX-001: Register a webhook for session events."""
    await get_owned_planning_session(db, session_id, user)
    from app.services.execution import webhook_notifier
    webhook_notifier.register_webhook(
        session_id=session_id,
        url=req.url,
        events=req.events,
        headers=req.headers,
        secret=req.secret,
    )
    return {"status": "registered", "session_id": session_id, "url": req.url}


@router.delete("/planning-sessions/{session_id}/webhooks")
async def unregister_webhooks(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """P2-EX-001: Remove all webhooks for a session."""
    await get_owned_planning_session(db, session_id, user)
    from app.services.execution import webhook_notifier
    webhook_notifier.unregister_webhooks(session_id)
    return {"status": "removed", "session_id": session_id}


# ===== P2-UF-010: File Cleanup API =====

@router.post("/admin/cleanup-uploads")
async def cleanup_expired_uploads(retention_days: int = 30):
    """P2-UF-010: Trigger cleanup of expired upload artifacts."""
    from app.services.execution import file_cleanup_service
    deleted = await file_cleanup_service.cleanup_expired_uploads(retention_days)
    return {"status": "completed", "deleted_count": deleted}


# ===== P2-SS-009: Execution Session Pause/Resume API =====

@router.post("/execution-sessions/{session_id}/pause")
async def pause_execution_session(session_id: str, reason: str = "User requested pause"):
    """P2-SS-009: Pause an Execution Session."""
    from app.services.execution import session_pause_service
    session = await session_pause_service.pause_execution_session(session_id, reason)
    if not session:
        raise HTTPException(status_code=404, detail="Execution session not found")
    return {"status": "paused", "session_id": session_id}


@router.post("/execution-sessions/{session_id}/resume")
async def resume_execution_session(session_id: str):
    """P2-SS-009: Resume a paused Execution Session."""
    from app.services.execution import session_pause_service
    session = await session_pause_service.resume_execution_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Execution session not found")
    return {"status": "resumed", "session_id": session_id}


# ===== P2-O-013: Parallel DAG Execution Waves API =====

@router.get("/planning-sessions/{session_id}/parallel-waves")
async def get_parallel_waves(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """P2-O-013: Get tasks organized in parallel execution waves."""
    await get_owned_planning_session(db, session_id, user)
    from app.services.execution import parallel_dag_scheduler
    waves = await parallel_dag_scheduler.get_parallel_waves(session_id)
    return {"session_id": session_id, "waves": waves, "total_waves": len(waves)}
