"""Flow API endpoints."""

from typing import Optional

from fastapi import APIRouter, Depends
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.authz import get_user_from_query_token
from app.api.flow_schemas import (
    CreateWorkspaceRequest,
    SendWorkspaceStageMessageRequest,
    StageFeedbackRequest,
    StageRunSettingsRequest,
    UpdateStageRequest,
    UpdateWorkspaceRequest,
    WorkspaceResponse,
    WorkspaceStageChatResponse,
    WorkspaceStageResponse,
)
from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.models import User, WorkspaceStageKey
from app.api.workspaces import (
    _download_all_workspace_artifacts_impl,
    approve_workspace_stage as approve_workspace_stage_impl,
    bootstrap_workspace_stage_stream as bootstrap_workspace_stage_stream_impl,
    create_workspace as create_workspace_impl,
    delete_workspace as delete_workspace_impl,
    finalize_workspace_stage_stream as finalize_workspace_stage_stream_impl,
    get_workspace as get_workspace_impl,
    get_workspace_stage_messages as get_workspace_stage_messages_impl,
    list_workspace_stages as list_workspace_stages_impl,
    list_workspaces as list_workspaces_impl,
    optional_bearer,
    request_stage_revision as request_stage_revision_impl,
    send_workspace_stage_message as send_workspace_stage_message_impl,
    stream_workspace_stage_message as stream_workspace_stage_message_impl,
    update_workspace as update_workspace_impl,
    update_workspace_stage as update_workspace_stage_impl,
)

router = APIRouter()


@router.post("/flows", response_model=WorkspaceResponse)
async def create_flow(
    req: CreateWorkspaceRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return await create_workspace_impl(req=req, db=db, user=user)


@router.get("/flows", response_model=list[WorkspaceResponse])
async def list_flows(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return await list_workspaces_impl(db=db, user=user)


@router.get("/flows/{workspace_id}", response_model=WorkspaceResponse)
async def get_flow(
    workspace_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return await get_workspace_impl(workspace_id=workspace_id, db=db, user=user)


@router.get("/flows/{workspace_id}/artifacts/download-all")
async def download_all_flow_artifacts(
    workspace_id: str,
    token: Optional[str] = None,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(optional_bearer),
    db: AsyncSession = Depends(get_db),
):
    auth_token = credentials.credentials if credentials else token
    user = await get_user_from_query_token(db, auth_token)
    return await _download_all_workspace_artifacts_impl(
        workspace_id=workspace_id,
        db=db,
        user=user,
    )


@router.patch("/flows/{workspace_id}", response_model=WorkspaceResponse)
async def update_flow(
    workspace_id: str,
    req: UpdateWorkspaceRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return await update_workspace_impl(workspace_id=workspace_id, req=req, db=db, user=user)


@router.delete("/flows/{workspace_id}")
async def delete_flow(
    workspace_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return await delete_workspace_impl(workspace_id=workspace_id, db=db, user=user)


@router.get("/flows/{workspace_id}/stages", response_model=list[WorkspaceStageResponse])
async def list_flow_stages(
    workspace_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return await list_workspace_stages_impl(workspace_id=workspace_id, db=db, user=user)


@router.get("/flows/{workspace_id}/stages/{stage_key}/messages", response_model=WorkspaceStageChatResponse)
async def get_flow_stage_messages(
    workspace_id: str,
    stage_key: WorkspaceStageKey,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return await get_workspace_stage_messages_impl(
        workspace_id=workspace_id,
        stage_key=stage_key,
        db=db,
        user=user,
    )


@router.post("/flows/{workspace_id}/stages/{stage_key}/bootstrap-stream")
async def bootstrap_flow_stage_stream(
    workspace_id: str,
    stage_key: WorkspaceStageKey,
    req: Optional[StageRunSettingsRequest] = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return await bootstrap_workspace_stage_stream_impl(
        workspace_id=workspace_id,
        stage_key=stage_key,
        req=req,
        db=db,
        user=user,
    )


@router.post("/flows/{workspace_id}/stages/{stage_key}/messages", response_model=WorkspaceStageChatResponse)
async def send_flow_stage_message(
    workspace_id: str,
    stage_key: WorkspaceStageKey,
    req: SendWorkspaceStageMessageRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return await send_workspace_stage_message_impl(
        workspace_id=workspace_id,
        stage_key=stage_key,
        req=req,
        db=db,
        user=user,
    )


@router.post("/flows/{workspace_id}/stages/{stage_key}/messages/stream")
async def stream_flow_stage_message(
    workspace_id: str,
    stage_key: WorkspaceStageKey,
    req: SendWorkspaceStageMessageRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return await stream_workspace_stage_message_impl(
        workspace_id=workspace_id,
        stage_key=stage_key,
        req=req,
        db=db,
        user=user,
    )


@router.post("/flows/{workspace_id}/stages/{stage_key}/finalize-stream")
async def finalize_flow_stage_stream(
    workspace_id: str,
    stage_key: WorkspaceStageKey,
    req: Optional[StageRunSettingsRequest] = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return await finalize_workspace_stage_stream_impl(
        workspace_id=workspace_id,
        stage_key=stage_key,
        req=req,
        db=db,
        user=user,
    )


@router.patch("/flows/{workspace_id}/stages/{stage_key}", response_model=WorkspaceStageResponse)
async def update_flow_stage(
    workspace_id: str,
    stage_key: WorkspaceStageKey,
    req: UpdateStageRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return await update_workspace_stage_impl(
        workspace_id=workspace_id,
        stage_key=stage_key,
        req=req,
        db=db,
        user=user,
    )


@router.post("/flows/{workspace_id}/stages/{stage_key}/approve", response_model=WorkspaceResponse)
async def approve_flow_stage(
    workspace_id: str,
    stage_key: WorkspaceStageKey,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return await approve_workspace_stage_impl(
        workspace_id=workspace_id,
        stage_key=stage_key,
        db=db,
        user=user,
    )


@router.post("/flows/{workspace_id}/stages/{stage_key}/request-revision", response_model=WorkspaceResponse)
async def request_flow_stage_revision(
    workspace_id: str,
    stage_key: WorkspaceStageKey,
    req: StageFeedbackRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return await request_stage_revision_impl(
        workspace_id=workspace_id,
        stage_key=stage_key,
        req=req,
        db=db,
        user=user,
    )
