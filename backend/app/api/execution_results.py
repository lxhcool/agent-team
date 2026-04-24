"""Execution Results API - CLI reports execution results back to server."""

import json
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.config import settings
from app.models.models import (
    Artifact,
    ExecutionSession,
    ExecutionStatus,
    PlanningSession,
    Task,
    TaskStatus,
)
from app.services.event_bus import event_bus, Event

router = APIRouter()


# ===== Schemas =====

class TaskResultItem(BaseModel):
    task_id: str
    title: Optional[str] = None
    status: str = "completed"
    result_summary: Optional[str] = None


class ExecutionResultRequest(BaseModel):
    execution_id: str
    plan_id: str
    source_session_id: str
    status: str = Field(default="completed")
    project_path: Optional[str] = None
    tasks: List[TaskResultItem] = Field(default_factory=list)


class ExecutionResultResponse(BaseModel):
    execution_id: str
    plan_id: str
    source_session_id: str
    status: str
    artifact_id: Optional[str] = None


# ===== Endpoints =====

@router.post("/execution-results", response_model=ExecutionResultResponse)
async def submit_execution_result(
    req: ExecutionResultRequest,
    db: AsyncSession = Depends(get_db),
):
    """CLI reports execution results back to the server.

    Processing:
    1. Find PlanningSession by source_session_id
    2. Create/update ExecutionSession
    3. Update Task statuses
    4. Save execution_result.json as Artifact
    5. Publish SSE event
    """
    # 1. Find PlanningSession
    planning_session = await db.get(PlanningSession, req.source_session_id)
    if not planning_session:
        raise HTTPException(status_code=404, detail="Planning session not found")

    # 2. Create or update ExecutionSession
    existing_exec = await db.execute(
        select(ExecutionSession).where(ExecutionSession.id == req.execution_id)
    )
    exec_session = existing_exec.scalars().first()

    if exec_session:
        # Update existing
        exec_status = _map_execution_status(req.status)
        exec_session.status = exec_status
        exec_session.project_path = req.project_path
        exec_session.plan_id = req.plan_id
    else:
        # Create new
        exec_status = _map_execution_status(req.status)
        exec_session = ExecutionSession(
            id=req.execution_id,
            plan_id=req.plan_id,
            user_id=planning_session.user_id,
            status=exec_status,
            project_path=req.project_path,
        )
        db.add(exec_session)

    # 3. Update Task statuses
    for task_item in req.tasks:
        task_result = await db.execute(
            select(Task).where(Task.id == task_item.task_id)
        )
        task = task_result.scalars().first()
        if task:
            task_status = _map_task_status(task_item.status)
            task.status = task_status
            task.result_summary = task_item.result_summary
            task.execution_session_id = req.execution_id

    await db.commit()

    # 4. Save execution_result.json as Artifact
    result_data = {
        "execution_id": req.execution_id,
        "plan_id": req.plan_id,
        "source_session_id": req.source_session_id,
        "status": req.status,
        "project_path": req.project_path,
        "tasks": [t.dict() for t in req.tasks],
    }
    result_json = json.dumps(result_data, ensure_ascii=False, indent=2)

    # Save file
    artifact_dir = settings.artifacts_dir / req.source_session_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = artifact_dir / "execution_result.json"
    artifact_path.write_text(result_json, encoding="utf-8")

    # Create Artifact record
    artifact = Artifact(
        session_type="planning",
        session_id=req.source_session_id,
        artifact_type="execution_result",
        filename="execution_result.json",
        path=str(artifact_path),
        mime_type="application/json",
        size_bytes=len(result_json.encode("utf-8")),
        source="cli",
        created_by="cli_engine",
    )
    db.add(artifact)
    await db.commit()
    await db.refresh(artifact)

    # 5. Publish SSE event
    event_bus.publish(req.source_session_id, Event(
        event="execution_result",
        data={
            "execution_id": req.execution_id,
            "plan_id": req.plan_id,
            "status": req.status,
            "artifact_id": artifact.id,
            "tasks": [t.dict() for t in req.tasks],
        },
    ))

    return ExecutionResultResponse(
        execution_id=req.execution_id,
        plan_id=req.plan_id,
        source_session_id=req.source_session_id,
        status=req.status,
        artifact_id=artifact.id,
    )


@router.get("/execution-results/{plan_id}")
async def get_execution_result(
    plan_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get execution result by plan_id."""
    result = await db.execute(
        select(ExecutionSession).where(ExecutionSession.plan_id == plan_id)
    )
    exec_session = result.scalars().first()
    if not exec_session:
        raise HTTPException(status_code=404, detail="Execution session not found")

    # Get artifact
    artifact_result = await db.execute(
        select(Artifact).where(
            Artifact.artifact_type == "execution_result",
            Artifact.session_id == exec_session.plan_id,
        ).order_by(Artifact.created_at.desc())
    )
    artifact = artifact_result.scalars().first()

    # Get tasks
    task_result = await db.execute(
        select(Task).where(Task.execution_session_id == exec_session.id)
    )
    tasks = task_result.scalars().all()

    return {
        "execution_id": exec_session.id,
        "plan_id": exec_session.plan_id,
        "status": exec_session.status.value if exec_session.status else "pending",
        "project_path": exec_session.project_path,
        "artifact_id": artifact.id if artifact else None,
        "tasks": [
            {
                "task_id": t.id,
                "title": t.title,
                "status": t.status.value if t.status else "pending",
                "result_summary": t.result_summary,
            }
            for t in tasks
        ],
        "created_at": exec_session.created_at.isoformat() if exec_session.created_at else None,
        "updated_at": exec_session.updated_at.isoformat() if exec_session.updated_at else None,
    }


@router.get("/planning-sessions/{session_id}/execution-result")
async def get_session_execution_result(
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get execution result for a planning session."""
    # Find ExecutionSession via plan_id pattern
    plan_id = f"plan_{session_id}"
    result = await db.execute(
        select(ExecutionSession).where(ExecutionSession.plan_id == plan_id)
    )
    exec_session = result.scalars().first()

    if not exec_session:
        # Also try by looking for execution_result artifacts
        artifact_result = await db.execute(
            select(Artifact).where(
                Artifact.session_id == session_id,
                Artifact.artifact_type == "execution_result",
            ).order_by(Artifact.created_at.desc())
        )
        artifact = artifact_result.scalars().first()
        if not artifact:
            raise HTTPException(status_code=404, detail="No execution result found for this session")

        # Read artifact file
        artifact_path = settings.artifacts_dir / session_id / "execution_result.json"
        if artifact_path.exists():
            return json.loads(artifact_path.read_text(encoding="utf-8"))
        raise HTTPException(status_code=404, detail="Execution result file not found")

    # Get tasks
    task_result = await db.execute(
        select(Task).where(Task.execution_session_id == exec_session.id)
    )
    tasks = task_result.scalars().all()

    # Get artifact
    artifact_result = await db.execute(
        select(Artifact).where(
            Artifact.session_id == session_id,
            Artifact.artifact_type == "execution_result",
        ).order_by(Artifact.created_at.desc())
    )
    artifact = artifact_result.scalars().first()

    return {
        "execution_id": exec_session.id,
        "plan_id": exec_session.plan_id,
        "status": exec_session.status.value if exec_session.status else "pending",
        "project_path": exec_session.project_path,
        "artifact_id": artifact.id if artifact else None,
        "tasks": [
            {
                "task_id": t.id,
                "title": t.title,
                "status": t.status.value if t.status else "pending",
                "result_summary": t.result_summary,
            }
            for t in tasks
        ],
        "created_at": exec_session.created_at.isoformat() if exec_session.created_at else None,
        "updated_at": exec_session.updated_at.isoformat() if exec_session.updated_at else None,
    }


# ===== Helpers =====

def _map_execution_status(status: str) -> ExecutionStatus:
    mapping = {
        "completed": ExecutionStatus.COMPLETED,
        "executing": ExecutionStatus.EXECUTING,
        "running": ExecutionStatus.EXECUTING,  # backward compat
        "failed": ExecutionStatus.FAILED,
        "partial": ExecutionStatus.PARTIAL,
        "created": ExecutionStatus.CREATED,
        "pending": ExecutionStatus.READY,  # backward compat
        "ready": ExecutionStatus.READY,
        "paused": ExecutionStatus.PAUSED,
        "cancelled": ExecutionStatus.CANCELLED,
    }
    return mapping.get(status, ExecutionStatus.COMPLETED)


def _map_task_status(status: str) -> TaskStatus:
    mapping = {
        "completed": TaskStatus.COMPLETED,
        "failed": TaskStatus.FAILED,
        "in_progress": TaskStatus.IN_PROGRESS,
        "pending": TaskStatus.PENDING,
        "skipped": TaskStatus.SKIPPED,
        "assigned": TaskStatus.ASSIGNED,
        "paused": TaskStatus.PAUSED,
        "ready": TaskStatus.READY,
        "blocked": TaskStatus.BLOCKED,
        "waiting_approval": TaskStatus.WAITING_APPROVAL,
        "cancelled": TaskStatus.CANCELLED,
    }
    return mapping.get(status, TaskStatus.COMPLETED)
