"""Shared request/response schemas for flow and workspace APIs."""

import json
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from app.models.models import (
    Artifact,
    PlanningSession,
    PlanningStatus,
    Workspace,
    WorkspaceMemberRole,
    WorkspaceStage,
    WorkspaceStageKey,
    WorkspaceStageMessage,
    WorkspaceStageStatus,
    WorkspaceStatus,
)


class CreateWorkspaceRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    target_platform: str = Field(default="website", max_length=50)


class UpdateWorkspaceRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = None
    target_platform: Optional[str] = Field(default=None, max_length=50)
    status: Optional[WorkspaceStatus] = None


class UpdateStageRequest(BaseModel):
    content: Optional[str] = None
    recommendation: Optional[Dict[str, Any]] = None
    user_feedback: Optional[str] = None
    status: Optional[WorkspaceStageStatus] = None


class StageFeedbackRequest(BaseModel):
    feedback: str = Field(..., min_length=1)


class StageAssistantSettings(BaseModel):
    model: Optional[str] = None
    provider: Optional[str] = None
    reasoning_effort: Optional[Literal["default", "low", "medium", "high"]] = None
    enable_web_search: bool = False
    enable_stage_skills: bool = True


class GenerateStageRequest(BaseModel):
    instruction: Optional[str] = None
    settings: Optional[StageAssistantSettings] = None


class StageRunSettingsRequest(BaseModel):
    settings: Optional[StageAssistantSettings] = None


class WorkspaceArtifactResponse(BaseModel):
    id: str
    workspace_id: str
    artifact_type: str
    filename: str
    mime_type: str
    size_bytes: int
    checksum: Optional[str] = None
    source: str
    created_by: Optional[str] = None
    created_at: Optional[str] = None
    url: str

    @classmethod
    def from_model(cls, artifact: Artifact):
        return cls(
            id=artifact.id,
            workspace_id=artifact.session_id,
            artifact_type=artifact.artifact_type,
            filename=artifact.filename,
            mime_type=artifact.mime_type,
            size_bytes=artifact.size_bytes,
            checksum=artifact.checksum,
            source=artifact.source,
            created_by=artifact.created_by,
            created_at=artifact.created_at.isoformat() if artifact.created_at else None,
            url=f"/api/workspaces/{artifact.session_id}/artifacts/{artifact.id}",
        )


class WorkspaceStageResponse(BaseModel):
    id: str
    workspace_id: str
    stage_key: WorkspaceStageKey
    title: str
    description: Optional[str] = None
    status: WorkspaceStageStatus
    order: int
    recommendation: Optional[Dict[str, Any]] = None
    content: Optional[str] = None
    user_feedback: Optional[str] = None
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    @classmethod
    def from_model(cls, stage: WorkspaceStage):
        recommendation = None
        if stage.recommendation_json:
            try:
                recommendation = json.loads(stage.recommendation_json)
            except json.JSONDecodeError:
                recommendation = None
        return cls(
            id=stage.id,
            workspace_id=stage.workspace_id,
            stage_key=stage.stage_key,
            title=stage.title,
            description=stage.description,
            status=stage.status,
            order=stage.order,
            recommendation=recommendation,
            content=stage.content,
            user_feedback=stage.user_feedback,
            approved_by=stage.approved_by,
            approved_at=stage.approved_at.isoformat() if stage.approved_at else None,
            created_at=stage.created_at.isoformat() if stage.created_at else None,
            updated_at=stage.updated_at.isoformat() if stage.updated_at else None,
        )


class WorkspaceStageMessageResponse(BaseModel):
    id: str
    stage_id: str
    role: str
    kind: str
    content: str
    artifact_id: Optional[str] = None
    artifact_url: Optional[str] = None
    created_at: Optional[str] = None

    @classmethod
    def from_model(cls, message: WorkspaceStageMessage, workspace_id: Optional[str] = None):
        artifact_url = None
        resolved_workspace_id = workspace_id or (message.stage.workspace_id if message.stage else None)
        if message.artifact_id and resolved_workspace_id:
            artifact_url = f"/api/workspaces/{resolved_workspace_id}/artifacts/{message.artifact_id}"
        return cls(
            id=message.id,
            stage_id=message.stage_id,
            role=message.role,
            kind=message.kind,
            content=message.content,
            artifact_id=message.artifact_id,
            artifact_url=artifact_url,
            created_at=message.created_at.isoformat() if message.created_at else None,
        )


class SendWorkspaceStageMessageRequest(BaseModel):
    content: str = Field(..., min_length=1)
    settings: Optional[StageAssistantSettings] = None


class WorkspaceStageChatResponse(BaseModel):
    stage: WorkspaceStageResponse
    messages: List[WorkspaceStageMessageResponse]


class WorkspacePlanningSessionResponse(BaseModel):
    id: str
    workspace_id: str
    title: str
    status: PlanningStatus
    mode: str
    input_text: str
    summary: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    @classmethod
    def from_model(cls, session: PlanningSession):
        return cls(
            id=session.id,
            workspace_id=session.workspace_id or "",
            title=session.title,
            status=session.status,
            mode=session.mode,
            input_text=session.input_text,
            summary=session.summary,
            created_at=session.created_at.isoformat() if session.created_at else None,
            updated_at=session.updated_at.isoformat() if session.updated_at else None,
        )


class WorkspaceResponse(BaseModel):
    id: str
    owner_id: str
    name: str
    description: Optional[str] = None
    target_platform: str
    status: WorkspaceStatus
    current_stage: WorkspaceStageKey
    created_by: str
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    role: Optional[WorkspaceMemberRole] = None
    stage_total: int = 0
    stage_approved: int = 0
    stages: Optional[List[WorkspaceStageResponse]] = None
    planning_sessions: Optional[List[WorkspacePlanningSessionResponse]] = None

    @classmethod
    def from_model(
        cls,
        workspace: Workspace,
        role: Optional[WorkspaceMemberRole] = None,
        stages: Optional[List[WorkspaceStage]] = None,
        planning_sessions: Optional[List[PlanningSession]] = None,
    ):
        stage_responses = [WorkspaceStageResponse.from_model(s) for s in stages] if stages is not None else None
        planning_responses = (
            [WorkspacePlanningSessionResponse.from_model(s) for s in planning_sessions]
            if planning_sessions is not None
            else None
        )
        approved = len([s for s in stages or [] if s.status == WorkspaceStageStatus.APPROVED])
        return cls(
            id=workspace.id,
            owner_id=workspace.owner_id,
            name=workspace.name,
            description=workspace.description,
            target_platform=workspace.target_platform,
            status=workspace.status,
            current_stage=workspace.current_stage,
            created_by=workspace.created_by,
            created_at=workspace.created_at.isoformat() if workspace.created_at else None,
            updated_at=workspace.updated_at.isoformat() if workspace.updated_at else None,
            role=role,
            stage_total=len(stages or []),
            stage_approved=approved,
            stages=stage_responses,
            planning_sessions=planning_responses,
        )
