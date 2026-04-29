"""Artifact API - endpoints for proposal and execution plan export.

Implements:
- F-003: Task failure artifact tracking
- UF-008: File upload artifact storage
- Artifact download and listing
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse, FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.api.authz import get_owned_planning_session
from app.models.models import Artifact, PlanningStatus, Task, TaskStatus, User
from app.services.artifact import artifact_service

router = APIRouter()


# ===== Schemas =====

class ArtifactResponse(BaseModel):
    id: str
    session_id: str
    artifact_type: str
    filename: str
    mime_type: str
    size_bytes: int
    checksum: Optional[str] = None
    source: str
    created_by: Optional[str] = None
    created_at: Optional[str] = None


class ExportResponse(BaseModel):
    proposal_downloaded: bool = False
    execution_plan_downloaded: bool = False
    cli_pull_command: Optional[str] = None


# ===== Endpoints =====

@router.get("/planning-sessions/{session_id}/artifacts", response_model=List[ArtifactResponse])
async def list_artifacts(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List all artifacts for a planning session."""
    await get_owned_planning_session(db, session_id, user)

    result = await db.execute(
        select(Artifact).where(Artifact.session_id == session_id).order_by(Artifact.created_at.asc())
    )
    artifacts = result.scalars().all()

    return [
        ArtifactResponse(
            id=a.id,
            session_id=a.session_id,
            artifact_type=a.artifact_type,
            filename=a.filename,
            mime_type=a.mime_type,
            size_bytes=a.size_bytes,
            checksum=a.checksum,
            source=a.source,
            created_by=a.created_by,
            created_at=a.created_at.isoformat() if a.created_at else None,
        )
        for a in artifacts
    ]


@router.get("/planning-sessions/{session_id}/proposal")
async def get_proposal(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get the proposal.md content for a planning session.

    If the artifact doesn't exist yet, it will be generated on-the-fly.
    """
    session = await get_owned_planning_session(db, session_id, user)

    # Only allow proposal access after proposal is generated
    allowed_statuses = {
        PlanningStatus.AWAITING_APPROVAL,
        PlanningStatus.READY_FOR_EXPORT,
        PlanningStatus.GENERATING_PLAN,
        PlanningStatus.COMPLETED,
    }
    if session.status not in allowed_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Proposal not available in status: {session.status.value}",
        )

    # Try to get existing artifact, or generate on-the-fly
    content = await artifact_service.get_proposal_content(session_id)
    if content is None:
        artifact = await artifact_service.generate_proposal(session_id)
        if not artifact:
            raise HTTPException(status_code=404, detail="No proposal found for this session")
        content = await artifact_service.get_proposal_content(session_id)

    if content is None:
        raise HTTPException(status_code=404, detail="Proposal file not found")

    return PlainTextResponse(content=content, media_type="text/markdown")


@router.get("/planning-sessions/{session_id}/execution-plan")
async def get_execution_plan(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get the execution_plan.json content for a planning session.

    If the artifact doesn't exist yet, it will be generated on-the-fly.
    """
    session = await get_owned_planning_session(db, session_id, user)

    # Only allow plan access after approval
    allowed_statuses = {
        PlanningStatus.READY_FOR_EXPORT,
        PlanningStatus.GENERATING_PLAN,
        PlanningStatus.COMPLETED,
    }
    if session.status not in allowed_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Execution plan not available in status: {session.status.value}",
        )

    # Try to get existing artifact, or generate on-the-fly
    plan_data = await artifact_service.get_execution_plan(session_id)
    if plan_data is None:
        artifact = await artifact_service.generate_execution_plan(session_id)
        if not artifact:
            raise HTTPException(status_code=404, detail="No execution plan found for this session")
        plan_data = await artifact_service.get_execution_plan(session_id)

    if plan_data is None:
        raise HTTPException(status_code=404, detail="Execution plan file not found")

    return plan_data


@router.post("/planning-sessions/{session_id}/export", response_model=ExportResponse)
async def export_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Generate and export both proposal.md and execution_plan.json.

    This endpoint triggers artifact generation and returns a status
    indicating what was generated, plus a CLI pull command.
    """
    session = await get_owned_planning_session(db, session_id, user)

    result = ExportResponse()

    # Generate proposal if possible
    if session.status in {PlanningStatus.AWAITING_APPROVAL, PlanningStatus.READY_FOR_EXPORT, PlanningStatus.GENERATING_PLAN, PlanningStatus.COMPLETED}:
        proposal_artifact = await artifact_service.generate_proposal(session_id)
        result.proposal_downloaded = proposal_artifact is not None

    # Generate execution plan if possible
    if session.status in {PlanningStatus.READY_FOR_EXPORT, PlanningStatus.GENERATING_PLAN, PlanningStatus.COMPLETED}:
        plan_artifact = await artifact_service.generate_execution_plan(session_id)
        result.execution_plan_downloaded = plan_artifact is not None

    # Generate CLI pull command
    if result.execution_plan_downloaded:
        plan_id = f"plan_{session_id}"
        result.cli_pull_command = f"agent-team pull-plan --plan-id {plan_id} --server http://localhost:8000"

    return result


# ===== F-003: Task Failure Artifact Tracking =====

@router.get("/planning-sessions/{session_id}/failed-tasks-artifacts")
async def get_failed_task_artifacts(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get artifacts related to failed tasks for a planning session (F-003).

    Per F-003: When tasks fail, the system should retain artifact references
    so users can inspect what was produced before the failure.

    Returns:
        List of failed tasks with their associated artifacts.
    """
    await get_owned_planning_session(db, session_id, user)

    # Get all failed tasks for this session
    task_result = await db.execute(
        select(Task).where(
            Task.session_id == session_id,
            Task.status == TaskStatus.FAILED,
        ).order_by(Task.order.asc())
    )
    failed_tasks = task_result.scalars().all()

    result = []
    for task in failed_tasks:
        # Get artifacts associated with this task
        artifact_result = await db.execute(
            select(Artifact).where(Artifact.task_id == task.id)
        )
        task_artifacts = artifact_result.scalars().all()

        # Also look for artifacts in the session's artifact directory
        # that might be related to this task's target paths
        target_paths = json.loads(task.target_paths_json) if task.target_paths_json else []
        related_files = []
        for tp in target_paths:
            artifact_dir = settings.artifacts_dir / session_id
            if artifact_dir.exists():
                for f in artifact_dir.rglob("*"):
                    if f.is_file() and tp in str(f):
                        related_files.append({
                            "path": str(f),
                            "name": f.name,
                            "size": f.stat().st_size,
                        })

        result.append({
            "task_id": task.id,
            "task_title": task.title,
            "task_status": task.status.value,
            "failure_summary": task.result_summary,
            "assigned_agent": task.assigned_agent,
            "target_paths": target_paths,
            "artifacts": [
                {
                    "id": a.id,
                    "filename": a.filename,
                    "artifact_type": a.artifact_type,
                    "size_bytes": a.size_bytes,
                    "created_by": a.created_by,
                    "created_at": a.created_at.isoformat() if a.created_at else None,
                }
                for a in task_artifacts
            ],
            "related_files": related_files,
            "validation_commands": json.loads(task.validation_commands_json) if task.validation_commands_json else [],
        })

    return result


@router.get("/artifacts/{artifact_id}/download")
async def download_artifact(
    artifact_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Download an artifact file by its ID.

    Supports both generated artifacts and uploaded attachments (UF-008, F-003).
    """
    artifact = await db.get(Artifact, artifact_id)
    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found")

    file_path = Path(artifact.path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Artifact file not found on disk")

    return FileResponse(
        path=str(file_path),
        filename=artifact.filename,
        media_type=artifact.mime_type,
    )
