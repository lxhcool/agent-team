"""Workspace API endpoints."""

import asyncio
import json
import logging
import re
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sse_starlette.sse import EventSourceResponse
from starlette.background import BackgroundTask
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.database import async_session, get_db
from app.api.flow_schemas import (
    CreateWorkspaceRequest,
    GenerateStageRequest,
    SendWorkspaceStageMessageRequest,
    StageFeedbackRequest,
    StageRunSettingsRequest,
    UpdateStageRequest,
    UpdateWorkspaceRequest,
    WorkspaceArtifactResponse,
    WorkspaceResponse,
    WorkspaceStageChatResponse,
    WorkspaceStageMessageResponse,
    WorkspaceStageResponse,
)
from app.api.authz import get_user_from_query_token
from app.api.agents import BUILTIN_AGENTS
from app.models.models import (
    Artifact,
    PlanningSession,
    User,
    Workspace,
    WorkspaceMember,
    WorkspaceMemberRole,
    WorkspaceStage,
    WorkspaceStageKey,
    WorkspaceStageMessage,
    WorkspaceStageStatus,
    WorkspaceStatus,
)
from app.services.flows.conversation import (
    assess_stage_finalization_readiness as _assess_stage_finalization_readiness,
    append_stage_message as _append_stage_message,
    build_finalize_not_ready_instruction as _build_finalize_not_ready_instruction,
    build_model_unavailable_stage_output as _build_model_unavailable_stage_output,
    ensure_stage_seed_messages as _ensure_stage_seed_messages,
    finalize_stage_conclusion as _finalize_stage_conclusion,
    generate_and_append_stage_reply as _generate_and_append_stage_reply,
    generate_requirements_stage_content as _generate_requirements_stage_content,
    get_stage_messages as _get_stage_messages,
    stage_is_ready_to_finalize as _stage_is_ready_to_finalize,
    should_finalize_stage_message as _should_finalize_stage_message,
    stream_stage_reply as _stream_stage_reply,
    stream_requirements_stage_document as _stream_requirements_stage_document,
)
from app.services.flows.artifacts import (
    create_workspace_artifact as _create_workspace_artifact,
    find_latest_workspace_artifact as _find_latest_workspace_artifact,
    generate_workspace_acceptance_stage as _generate_workspace_acceptance_stage,
    generate_workspace_development_stage as _generate_workspace_development_stage,
    load_recommendation as _load_recommendation,
    upsert_artifact_reference as _upsert_artifact_reference,
)
from app.services.flows.contracts import (
    parse_json_object as _parse_json_object,
    sanitize_llm_artifact as _sanitize_llm_artifact,
)
from app.services.flows.memory import (
    latest_conclusion_artifact_id as _latest_conclusion_artifact_id,
    supersede_stage_memories_from_order as _supersede_stage_memories_from_order,
    sync_memories_from_source as _sync_memories_from_source,
)
from app.services.flows.stage_state import (
    has_generated_recommendation as _has_generated_recommendation,
    next_stage_key as _next_stage_key,
    seed_stages as _seed_stages,
)
from app.services.flows.stage_generation import (
    generate_stage_artifact_with_llm as _generate_stage_artifact_with_llm_service,
    resolve_generation_model as _resolve_generation_model,
    workspace_stage_agent_name as _workspace_stage_agent_name,
)

router = APIRouter()
optional_bearer = HTTPBearer(auto_error=False)
logger = logging.getLogger(__name__)


STAGE_AGENT_NAMES: dict[WorkspaceStageKey, str] = {
    WorkspaceStageKey.REQUIREMENTS: "requirements-analyst",
    WorkspaceStageKey.PRODUCT: "product-designer",
    WorkspaceStageKey.UI_DIRECTION: "product-designer",
    WorkspaceStageKey.PROTOTYPE: "ui-ux-designer",
    WorkspaceStageKey.TECHNICAL: "technical-architect",
    WorkspaceStageKey.DEVELOPMENT: "spec-writer",
    WorkspaceStageKey.ACCEPTANCE: "qa-reviewer",
    WorkspaceStageKey.DEPLOYMENT: "orchestrator",
}

BUILTIN_AGENT_MAP = {agent["name"]: agent for agent in BUILTIN_AGENTS}


STAGE_DEFINITIONS = [
    (
        WorkspaceStageKey.REQUIREMENTS,
        "需求确认",
        "先对齐这到底是个什么产品，再确认主要用户、核心用途和边界。",
        ["产品定位", "主要用户", "边界前提"],
    ),
    (
        WorkspaceStageKey.PRODUCT,
        "方案设计",
        "先整理功能模块和模块关系，再明确页面结构和主要流程。",
        ["功能模块", "模块关系", "页面结构与流程"],
    ),
    (
        WorkspaceStageKey.UI_DIRECTION,
        "细节确认",
        "确认角色权限、状态流转、异常处理、数据口径和关键边界。",
        ["规则边界", "状态流转", "异常与数据口径"],
    ),
    (
        WorkspaceStageKey.TECHNICAL,
        "开发方案",
        "整理可交给开发接手的实现方案，包括模块拆分、接口数据和依赖风险。",
        ["模块拆分", "接口与数据", "依赖与风险"],
    ),
    (
        WorkspaceStageKey.DEPLOYMENT,
        "交付清单",
        "整理全部已确认文档，支持单独下载或整体打包下载。",
        ["文档清单", "单独下载", "打包下载"],
    ),
]


async def _stream_with_fresh_session(
    *,
    workspace_id: str,
    stage_key: WorkspaceStageKey,
    user: User,
    force_finalize: bool = False,
    runtime_options: Optional[Dict[str, Any]] = None,
):
    async def generator():
        async with async_session() as stream_db:
            workspace, _member = await _get_accessible_workspace(stream_db, workspace_id, user)
            stages = await _get_stages(stream_db, workspace_id)
            stage = next((item for item in stages if item.stage_key == stage_key), None)
            if not stage:
                yield {"event": "error", "data": "Workspace stage not found"}
                return
            messages = await _get_stage_messages(stream_db, stage.id)
            resolve_generation_model = _make_generation_resolver(runtime_options)
            inner = await _stream_stage_reply(
                db=stream_db,
                user=user,
                workspace=workspace,
                stages=stages,
                stage=stage,
                messages=messages,
                resolve_generation_model=resolve_generation_model,
                create_workspace_artifact=_create_workspace_artifact,
                load_recommendation=_load_recommendation,
                upsert_artifact_reference=_upsert_artifact_reference,
                serialize_stage_response=lambda item: WorkspaceStageResponse.from_model(item).model_dump(mode="json"),
                serialize_stage_message_response=lambda item: WorkspaceStageMessageResponse.from_model(item, workspace.id).model_dump(mode="json"),
                parse_json_object=_parse_json_object,
                force_finalize=force_finalize,
                runtime_options=runtime_options,
            )
            async for event in inner:
                yield event

    return generator()


async def _sync_stage_memories_after_approval(
    *,
    workspace_id: str,
    stage_key: WorkspaceStageKey,
    user_id: str,
    source_text: str,
    source_artifact_id: Optional[str],
) -> None:
    if not source_text.strip():
        return

    async with async_session() as session:
        try:
            user = await session.get(User, user_id)
            workspace = await session.get(Workspace, workspace_id)
            if not user or not workspace:
                return

            result = await session.execute(
                select(WorkspaceStage).where(
                    WorkspaceStage.workspace_id == workspace_id,
                    WorkspaceStage.stage_key == stage_key,
                )
            )
            stage = result.scalar_one_or_none()
            if not stage:
                return

            await _sync_memories_from_source(
                db=session,
                user=user,
                workspace=workspace,
                stage=stage,
                source_kind="approved_stage_document",
                source_text=source_text,
                source_artifact_id=source_artifact_id,
                resolve_generation_model=_resolve_generation_model,
            )
            await session.commit()
        except Exception as exc:
            if session.in_transaction():
                await session.rollback()
            logger.warning(
                "post-approval memory sync failed: workspace=%s stage=%s reason=%s",
                workspace_id,
                stage_key.value,
                exc,
            )


async def _get_workspace_member(
    db: AsyncSession,
    workspace_id: str,
    user: User,
) -> WorkspaceMember:
    result = await db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user.id,
        )
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return member


async def _get_accessible_workspace(
    db: AsyncSession,
    workspace_id: str,
    user: User,
) -> tuple[Workspace, WorkspaceMember]:
    member = await _get_workspace_member(db, workspace_id, user)
    workspace = await db.get(Workspace, workspace_id)
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return workspace, member


def _require_editor(member: WorkspaceMember):
    if member.role not in {WorkspaceMemberRole.OWNER, WorkspaceMemberRole.EDITOR}:
        raise HTTPException(status_code=403, detail="Workspace edit permission required")


def _build_runtime_options(settings: Optional[Any]) -> Dict[str, Any]:
    if settings is None:
        return {}
    return {
        "model": str(getattr(settings, "model", "") or "").strip() or None,
        "provider": str(getattr(settings, "provider", "") or "").strip() or None,
        "reasoning_effort": str(getattr(settings, "reasoning_effort", "") or "").strip() or None,
        "enable_web_search": bool(getattr(settings, "enable_web_search", False)),
        "enable_stage_skills": bool(getattr(settings, "enable_stage_skills", True)),
    }


def _make_generation_resolver(runtime_options: Optional[Dict[str, Any]] = None):
    async def _resolver(db: AsyncSession, user: User):
        return await _resolve_generation_model(db, user, overrides=runtime_options)

    return _resolver


async def _get_stages(db: AsyncSession, workspace_id: str) -> List[WorkspaceStage]:
    result = await db.execute(
        select(WorkspaceStage)
        .where(WorkspaceStage.workspace_id == workspace_id)
        .order_by(WorkspaceStage.order)
    )
    return list(result.scalars().all())


def _require_stage_predecessors_approved(stages: List[WorkspaceStage], stage: WorkspaceStage):
    blocking = next(
        (
            item for item in stages
            if item.order < stage.order and item.status not in {WorkspaceStageStatus.APPROVED, WorkspaceStageStatus.SKIPPED}
        ),
        None,
    )
    if blocking:
        raise HTTPException(
            status_code=400,
            detail=f"请先完成前置阶段「{blocking.title}」，再继续当前阶段",
        )


async def _generate_stage_artifact_with_llm(
    db: AsyncSession,
    user: User,
    workspace: Workspace,
    stages: List[WorkspaceStage],
    stage: WorkspaceStage,
    instruction: Optional[str],
    runtime_options: Optional[Dict[str, Any]] = None,
) -> Optional[tuple[Dict[str, Any], str]]:
    return await _generate_stage_artifact_with_llm_service(
        db=db,
        user=user,
        workspace=workspace,
        stages=stages,
        stage=stage,
        instruction=instruction,
        runtime_options=runtime_options,
        stage_agent_names=STAGE_AGENT_NAMES,
        builtin_agent_map=BUILTIN_AGENT_MAP,
        parse_json_object=_parse_json_object,
        sanitize_llm_artifact=_sanitize_llm_artifact,
    )


def _stage_content(stages: List[WorkspaceStage], key: WorkspaceStageKey) -> str:
    for stage in stages:
        if stage.stage_key == key and stage.content:
            return stage.content
    return ""


async def _stage_has_conclusion_artifact(db: AsyncSession, stage: WorkspaceStage) -> bool:
    recommendation = _load_recommendation(stage)
    artifacts = recommendation.get("artifacts")
    if isinstance(artifacts, list) and any(
        isinstance(item, dict)
        and item.get("artifact_id")
        and str(item.get("type") or "").endswith("_conclusion")
        for item in artifacts
    ):
        return True

    messages = await _get_stage_messages(db, stage.id)
    return any(
        message.role == "assistant"
        and message.kind == "conclusion"
        and bool(message.artifact_id)
        for message in messages
    )


@router.post("/workspaces", response_model=WorkspaceResponse)
async def create_workspace(
    req: CreateWorkspaceRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    workspace = Workspace(
        owner_id=user.id,
        name=req.name,
        description=req.description,
        target_platform=req.target_platform,
        storage_mode="server",
        created_by=user.id,
        current_stage=WorkspaceStageKey.REQUIREMENTS,
    )
    db.add(workspace)
    await db.flush()

    member = WorkspaceMember(
        workspace_id=workspace.id,
        user_id=user.id,
        role=WorkspaceMemberRole.OWNER,
    )
    db.add(member)
    stages = _seed_stages(workspace, req.description, STAGE_DEFINITIONS)
    db.add_all(stages)
    await db.flush()

    initial_requirement = (req.description or req.name or "").strip()
    requirements_stage = next(
        (item for item in stages if item.stage_key == WorkspaceStageKey.REQUIREMENTS),
        None,
    )
    if requirements_stage and initial_requirement:
        db.add(
            WorkspaceStageMessage(
                stage_id=requirements_stage.id,
                role="user",
                content=initial_requirement,
                kind="chat",
            )
        )

    await db.commit()
    await db.refresh(workspace)

    return WorkspaceResponse.from_model(workspace, member.role, stages)


@router.get("/workspaces", response_model=List[WorkspaceResponse])
async def list_workspaces(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Workspace, WorkspaceMember)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == user.id)
        .order_by(Workspace.updated_at.desc())
    )
    rows = result.all()
    responses = []
    for workspace, member in rows:
        stages = await _get_stages(db, workspace.id)
        responses.append(WorkspaceResponse.from_model(workspace, member.role, stages))
    return responses


@router.get("/workspaces/{workspace_id}", response_model=WorkspaceResponse)
async def get_workspace(
    workspace_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    workspace, member = await _get_accessible_workspace(db, workspace_id, user)
    stages = await _get_stages(db, workspace.id)
    planning_result = await db.execute(
        select(PlanningSession)
        .where(PlanningSession.workspace_id == workspace.id)
        .order_by(PlanningSession.created_at.desc())
    )
    planning_sessions = planning_result.scalars().all()
    return WorkspaceResponse.from_model(workspace, member.role, stages, planning_sessions)


@router.patch("/workspaces/{workspace_id}", response_model=WorkspaceResponse)
async def update_workspace(
    workspace_id: str,
    req: UpdateWorkspaceRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    workspace, member = await _get_accessible_workspace(db, workspace_id, user)
    _require_editor(member)

    if req.name is not None:
        workspace.name = req.name
    if req.description is not None:
        workspace.description = req.description
    if req.target_platform is not None:
        workspace.target_platform = req.target_platform
    if req.status is not None:
        workspace.status = req.status

    await db.commit()
    await db.refresh(workspace)
    stages = await _get_stages(db, workspace.id)
    return WorkspaceResponse.from_model(workspace, member.role, stages)


@router.delete("/workspaces/{workspace_id}")
async def delete_workspace(
    workspace_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    workspace, member = await _get_accessible_workspace(db, workspace_id, user)
    if member.role != WorkspaceMemberRole.OWNER:
        raise HTTPException(status_code=403, detail="Only workspace owner can delete")
    await db.delete(workspace)
    await db.commit()
    return {"status": "deleted", "workspace_id": workspace_id}


@router.get("/workspaces/{workspace_id}/artifacts", response_model=List[WorkspaceArtifactResponse])
async def list_workspace_artifacts(
    workspace_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await _get_accessible_workspace(db, workspace_id, user)
    result = await db.execute(
        select(Artifact)
        .where(
            Artifact.session_type == "workspace",
            Artifact.session_id == workspace_id,
        )
        .order_by(Artifact.created_at.desc())
    )
    return [WorkspaceArtifactResponse.from_model(artifact) for artifact in result.scalars().all()]


@router.get("/workspaces/{workspace_id}/artifacts/{artifact_id}")
async def get_workspace_artifact(
    workspace_id: str,
    artifact_id: str,
    token: Optional[str] = None,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(optional_bearer),
    db: AsyncSession = Depends(get_db),
):
    auth_token = credentials.credentials if credentials else token
    user = await get_user_from_query_token(db, auth_token)
    await _get_accessible_workspace(db, workspace_id, user)
    artifact = await db.get(Artifact, artifact_id)
    if (
        not artifact
        or artifact.session_type != "workspace"
        or artifact.session_id != workspace_id
    ):
        raise HTTPException(status_code=404, detail="Artifact not found")

    path = Path(artifact.path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Artifact file not found")

    return FileResponse(
        path=str(path),
        media_type=artifact.mime_type,
        filename=artifact.filename,
        headers={"Content-Disposition": f'inline; filename="{artifact.filename}"'},
    )


async def _download_all_workspace_artifacts_impl(
    workspace_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = None,
):
    workspace, _member = await _get_accessible_workspace(db, workspace_id, user)
    result = await db.execute(
        select(Artifact)
        .where(
            Artifact.session_type == "workspace",
            Artifact.session_id == workspace_id,
        )
        .order_by(Artifact.created_at.asc())
    )
    artifacts = list(result.scalars().all())
    if not artifacts:
        raise HTTPException(status_code=404, detail="No workspace artifacts found")

    temp_dir = Path(tempfile.mkdtemp(prefix=f"workspace-artifacts-{workspace_id[:8]}-"))
    safe_workspace_name = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "-", workspace.name).strip("-") or "workspace"
    zip_path = temp_dir / f"{safe_workspace_name}-artifacts.zip"
    used_names: set[str] = set()
    added_count = 0

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zip_file:
        for artifact in artifacts:
            file_path = Path(artifact.path)
            if not file_path.exists():
                continue
            base_name = artifact.filename or file_path.name or f"{artifact.artifact_type}.md"
            candidate_name = base_name
            suffix = 2
            while candidate_name in used_names:
                stem = Path(base_name).stem
                ext = Path(base_name).suffix
                candidate_name = f"{stem}-{suffix}{ext}"
                suffix += 1
            used_names.add(candidate_name)
            zip_file.write(file_path, arcname=candidate_name)
            added_count += 1

    if added_count == 0:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise HTTPException(status_code=404, detail="No artifact files found on disk")

    return FileResponse(
        path=str(zip_path),
        media_type="application/zip",
        filename=zip_path.name,
        background=BackgroundTask(shutil.rmtree, temp_dir, True),
    )


@router.get("/workspaces/{workspace_id}/artifacts/download-all")
async def download_all_workspace_artifacts(
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


@router.get("/workspaces/{workspace_id}/stages", response_model=List[WorkspaceStageResponse])
async def list_workspace_stages(
    workspace_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await _get_accessible_workspace(db, workspace_id, user)
    stages = await _get_stages(db, workspace_id)
    return [WorkspaceStageResponse.from_model(stage) for stage in stages]


@router.get("/workspaces/{workspace_id}/stages/{stage_key}/messages", response_model=WorkspaceStageChatResponse)
async def get_workspace_stage_messages(
    workspace_id: str,
    stage_key: WorkspaceStageKey,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    workspace, member = await _get_accessible_workspace(db, workspace_id, user)
    stages = await _get_stages(db, workspace_id)
    stage = next((item for item in stages if item.stage_key == stage_key), None)
    if not stage:
        raise HTTPException(status_code=404, detail="Workspace stage not found")

    messages = await _get_stage_messages(db, stage.id)

    return WorkspaceStageChatResponse(
        stage=WorkspaceStageResponse.from_model(stage),
        messages=[WorkspaceStageMessageResponse.from_model(message, workspace.id) for message in messages],
    )


@router.post("/workspaces/{workspace_id}/stages/{stage_key}/bootstrap-stream")
async def bootstrap_workspace_stage_stream(
    workspace_id: str,
    stage_key: WorkspaceStageKey,
    req: Optional[StageRunSettingsRequest] = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    workspace, member = await _get_accessible_workspace(db, workspace_id, user)
    _require_editor(member)

    stages = await _get_stages(db, workspace_id)
    stage = next((item for item in stages if item.stage_key == stage_key), None)
    if not stage:
        raise HTTPException(status_code=404, detail="Workspace stage not found")

    messages = await _get_stage_messages(db, stage.id)
    has_assistant_message = any(item.role == "assistant" and item.content.strip() for item in messages)
    if has_assistant_message:
        raise HTTPException(status_code=400, detail="当前阶段已经有生成内容")

    messages = await _ensure_stage_seed_messages(
        db=db,
        workspace=workspace,
        stage=stage,
        existing_messages=messages,
    )
    await db.commit()
    messages = await _get_stage_messages(db, stage.id)
    runtime_options = _build_runtime_options(req.settings if req else None)

    generator = await _stream_with_fresh_session(
        workspace_id=workspace_id,
        stage_key=stage_key,
        user=user,
        runtime_options=runtime_options,
    )
    return EventSourceResponse(generator)


@router.post("/workspaces/{workspace_id}/stages/{stage_key}/messages", response_model=WorkspaceStageChatResponse)
async def send_workspace_stage_message(
    workspace_id: str,
    stage_key: WorkspaceStageKey,
    req: SendWorkspaceStageMessageRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    workspace, member = await _get_accessible_workspace(db, workspace_id, user)
    _require_editor(member)

    stages = await _get_stages(db, workspace_id)
    stage = next((item for item in stages if item.stage_key == stage_key), None)
    if not stage:
        raise HTTPException(status_code=404, detail="Workspace stage not found")
    _require_stage_predecessors_approved(stages, stage)
    if stage.status == WorkspaceStageStatus.APPROVED:
        raise HTTPException(status_code=400, detail="当前阶段已确认，请先发起调整，再继续补充")
    runtime_options = _build_runtime_options(req.settings)
    resolve_generation_model = _make_generation_resolver(runtime_options)

    content = req.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="消息不能为空")

    messages = await _get_stage_messages(db, stage.id)
    last_message = messages[-1] if messages else None
    is_retry_after_stream_failure = (
        last_message is not None
        and last_message.role == "user"
        and last_message.content.strip() == content
    )
    if not is_retry_after_stream_failure:
        await _append_stage_message(
            db=db,
            stage=stage,
            role="user",
            content=content,
            kind="chat",
        )
        await db.commit()
        messages = await _get_stage_messages(db, stage.id)

    extra_instruction: Optional[str] = None
    finalize_requested = await _should_finalize_stage_message(
        db=db,
        user=user,
        workspace=workspace,
        stage=stage,
        messages=messages,
        resolve_generation_model=resolve_generation_model,
        parse_json_object=_parse_json_object,
    )
    if finalize_requested:
        if _stage_is_ready_to_finalize(stage, messages):
            can_finalize, blockers = True, []
        else:
            can_finalize, blockers = await _assess_stage_finalization_readiness(
                db=db,
                user=user,
                workspace=workspace,
                stage=stage,
                messages=messages,
                resolve_generation_model=resolve_generation_model,
            )
        if can_finalize:
            await _finalize_stage_conclusion(
                db=db,
                user=user,
                workspace=workspace,
                stages=stages,
                stage=stage,
                messages=messages,
                resolve_generation_model=resolve_generation_model,
                create_workspace_artifact=_create_workspace_artifact,
                load_recommendation=_load_recommendation,
                upsert_artifact_reference=_upsert_artifact_reference,
                runtime_options=runtime_options,
            )
        else:
            extra_instruction = _build_finalize_not_ready_instruction(stage, blockers)

    if extra_instruction is not None or not finalize_requested:
        await _generate_and_append_stage_reply(
            db=db,
            user=user,
            workspace=workspace,
            stages=stages,
            stage=stage,
            messages=messages,
            extra_instruction=extra_instruction,
            resolve_generation_model=resolve_generation_model,
            generate_stage_artifact_with_llm=_generate_stage_artifact_with_llm,
            runtime_options=runtime_options,
        )

    await db.commit()
    await db.refresh(stage)
    messages = await _get_stage_messages(db, stage.id)
    return WorkspaceStageChatResponse(
        stage=WorkspaceStageResponse.from_model(stage),
        messages=[WorkspaceStageMessageResponse.from_model(message, workspace.id) for message in messages],
    )


@router.post("/workspaces/{workspace_id}/stages/{stage_key}/messages/stream")
async def stream_workspace_stage_message(
    workspace_id: str,
    stage_key: WorkspaceStageKey,
    req: SendWorkspaceStageMessageRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    workspace, member = await _get_accessible_workspace(db, workspace_id, user)
    _require_editor(member)

    stages = await _get_stages(db, workspace_id)
    stage = next((item for item in stages if item.stage_key == stage_key), None)
    if not stage:
        raise HTTPException(status_code=404, detail="Workspace stage not found")
    _require_stage_predecessors_approved(stages, stage)
    if stage.status == WorkspaceStageStatus.APPROVED:
        raise HTTPException(status_code=400, detail="当前阶段已确认，请先发起调整，再继续补充")
    runtime_options = _build_runtime_options(req.settings)

    content = req.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="消息不能为空")

    messages = await _get_stage_messages(db, stage.id)
    last_message = messages[-1] if messages else None
    is_retry_after_stream_failure = (
        last_message is not None
        and last_message.role == "user"
        and last_message.content.strip() == content
    )
    appended_user_message: Optional[WorkspaceStageMessage] = None
    if not is_retry_after_stream_failure:
        appended_user_message = await _append_stage_message(
            db=db,
            stage=stage,
            role="user",
            content=content,
            kind="chat",
        )
        await _sync_memories_from_source(
            db=db,
            user=user,
            workspace=workspace,
            stage=stage,
            source_kind="user_message",
            source_text=content,
            source_message_id=appended_user_message.id,
            resolve_generation_model=_resolve_generation_model,
        )
        await db.commit()
        messages = await _get_stage_messages(db, stage.id)

    generator = await _stream_with_fresh_session(
        workspace_id=workspace_id,
        stage_key=stage_key,
        user=user,
        runtime_options=runtime_options,
    )
    return EventSourceResponse(generator)


@router.post("/workspaces/{workspace_id}/stages/{stage_key}/finalize-stream")
async def finalize_workspace_stage_stream(
    workspace_id: str,
    stage_key: WorkspaceStageKey,
    req: Optional[StageRunSettingsRequest] = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    workspace, member = await _get_accessible_workspace(db, workspace_id, user)
    _require_editor(member)

    stages = await _get_stages(db, workspace_id)
    stage = next((item for item in stages if item.stage_key == stage_key), None)
    if not stage:
        raise HTTPException(status_code=404, detail="Workspace stage not found")
    _require_stage_predecessors_approved(stages, stage)

    messages = await _ensure_stage_seed_messages(
        db=db,
        workspace=workspace,
        stage=stage,
        existing_messages=await _get_stage_messages(db, stage.id),
    )
    await db.commit()
    messages = await _get_stage_messages(db, stage.id)
    runtime_options = _build_runtime_options(req.settings if req else None)

    generator = await _stream_with_fresh_session(
        workspace_id=workspace_id,
        stage_key=stage_key,
        user=user,
        force_finalize=True,
        runtime_options=runtime_options,
    )
    return EventSourceResponse(generator)


@router.patch("/workspaces/{workspace_id}/stages/{stage_key}", response_model=WorkspaceStageResponse)
async def update_workspace_stage(
    workspace_id: str,
    stage_key: WorkspaceStageKey,
    req: UpdateStageRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    workspace, member = await _get_accessible_workspace(db, workspace_id, user)
    _require_editor(member)

    result = await db.execute(
        select(WorkspaceStage).where(
            WorkspaceStage.workspace_id == workspace_id,
            WorkspaceStage.stage_key == stage_key,
        )
    )
    stage = result.scalar_one_or_none()
    if not stage:
        raise HTTPException(status_code=404, detail="Workspace stage not found")

    if req.content is not None:
        stage.content = req.content
    if req.recommendation is not None:
        stage.recommendation_json = json.dumps(req.recommendation, ensure_ascii=False)
    if req.user_feedback is not None:
        stage.user_feedback = req.user_feedback
    if req.status is not None:
        stage.status = req.status
    workspace.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(stage)
    return WorkspaceStageResponse.from_model(stage)


@router.post("/workspaces/{workspace_id}/stages/{stage_key}/generate", response_model=WorkspaceStageResponse)
async def generate_workspace_stage(
    workspace_id: str,
    stage_key: WorkspaceStageKey,
    req: GenerateStageRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    workspace, member = await _get_accessible_workspace(db, workspace_id, user)
    _require_editor(member)

    stages = await _get_stages(db, workspace_id)
    stage = next((item for item in stages if item.stage_key == stage_key), None)
    if not stage:
        raise HTTPException(status_code=404, detail="Workspace stage not found")
    _require_stage_predecessors_approved(stages, stage)

    if stage.stage_key == WorkspaceStageKey.DEVELOPMENT:
        stage = await _generate_workspace_development_stage(
            db=db,
            workspace=workspace,
            user=user,
            stages=stages,
            stage=stage,
            workspace_stage_agent_name=_workspace_stage_agent_name,
        )
        await db.commit()
        await db.refresh(stage)
        return WorkspaceStageResponse.from_model(stage)

    if stage.stage_key == WorkspaceStageKey.ACCEPTANCE:
        stage = await _generate_workspace_acceptance_stage(
            db=db,
            workspace=workspace,
            user=user,
            stages=stages,
            stage=stage,
            workspace_stage_agent_name=_workspace_stage_agent_name,
        )
        await db.commit()
        await db.refresh(stage)
        return WorkspaceStageResponse.from_model(stage)

    if stage.stage_key == WorkspaceStageKey.REQUIREMENTS:
        runtime_options = _build_runtime_options(req.settings)
        recommendation, content = await _generate_requirements_stage_content(
            db=db,
            user=user,
            workspace=workspace,
            stages=stages,
            stage=stage,
            instruction=req.instruction,
            resolve_generation_model=_make_generation_resolver(runtime_options),
            runtime_options=runtime_options,
        )
        stage.recommendation_json = json.dumps(recommendation, ensure_ascii=False)
        stage.content = content
        stage.status = WorkspaceStageStatus.AWAITING_CONFIRMATION
        stage.approved_by = None
        stage.approved_at = None
        workspace.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(stage)
        return WorkspaceStageResponse.from_model(stage)

    generated = await _generate_stage_artifact_with_llm(
        db=db,
        user=user,
        workspace=workspace,
        stages=stages,
        stage=stage,
        instruction=req.instruction,
        runtime_options=_build_runtime_options(req.settings),
    )
    if generated:
        recommendation, content = generated
    else:
        recommendation, content = _build_model_unavailable_stage_output(
            stage,
            summary=f"{stage.title}阶段当前没有拿到可用的大模型回复。",
            recommended_action="请先检查模型配置；恢复后再继续当前阶段。",
            focus=["模型可用性", "真实阶段输出", "避免模板兜底"],
        )
    stage.recommendation_json = json.dumps(recommendation, ensure_ascii=False)
    stage.content = content
    stage.status = WorkspaceStageStatus.AWAITING_CONFIRMATION
    stage.approved_by = None
    stage.approved_at = None
    workspace.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(stage)
    return WorkspaceStageResponse.from_model(stage)


@router.post("/workspaces/{workspace_id}/stages/{stage_key}/generate-stream")
async def generate_workspace_stage_stream(
    workspace_id: str,
    stage_key: WorkspaceStageKey,
    req: GenerateStageRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    workspace, member = await _get_accessible_workspace(db, workspace_id, user)
    _require_editor(member)

    stages = await _get_stages(db, workspace_id)
    stage = next((item for item in stages if item.stage_key == stage_key), None)
    if not stage:
        raise HTTPException(status_code=404, detail="Workspace stage not found")

    if stage.stage_key != WorkspaceStageKey.REQUIREMENTS:
        raise HTTPException(status_code=400, detail="当前仅需求确认阶段支持流式生成")

    generator = await _stream_requirements_stage_document(
        db=db,
        user=user,
        workspace=workspace,
        stages=stages,
        stage=stage,
        instruction=req.instruction,
        resolve_generation_model=_make_generation_resolver(_build_runtime_options(req.settings)),
        runtime_options=_build_runtime_options(req.settings),
        build_model_unavailable_stage_output=_build_model_unavailable_stage_output,
        generate_requirements_stage_content=_generate_requirements_stage_content,
        serialize_stage_response=lambda item: WorkspaceStageResponse.from_model(item).model_dump(mode="json"),
    )
    return EventSourceResponse(generator)


@router.post("/workspaces/{workspace_id}/stages/{stage_key}/approve", response_model=WorkspaceResponse)
async def approve_workspace_stage(
    workspace_id: str,
    stage_key: WorkspaceStageKey,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    workspace, member = await _get_accessible_workspace(db, workspace_id, user)
    _require_editor(member)

    stages = await _get_stages(db, workspace_id)
    stage_map = {stage.stage_key: stage for stage in stages}
    stage = stage_map.get(stage_key)
    if not stage:
        raise HTTPException(status_code=404, detail="Workspace stage not found")
    _require_stage_predecessors_approved(stages, stage)
    if not _has_generated_recommendation(_load_recommendation, stage):
        raise HTTPException(status_code=400, detail="请先生成推荐方案，再确认通过当前阶段")
    has_conclusion = await _stage_has_conclusion_artifact(db, stage)
    if not has_conclusion:
        raise HTTPException(status_code=400, detail="请先生成阶段结论，再进入下一阶段")

    conclusion_artifact_id = _latest_conclusion_artifact_id(stage)
    stage_content = stage.content or ""

    stage.status = WorkspaceStageStatus.APPROVED
    stage.approved_by = user.id
    stage.approved_at = datetime.now(timezone.utc)

    next_key = _next_stage_key(stage_key)
    if next_key:
        workspace.current_stage = next_key
        next_stage = stage_map.get(next_key)
        if next_stage and next_stage.status == WorkspaceStageStatus.DRAFT:
            next_stage.status = WorkspaceStageStatus.AWAITING_CONFIRMATION
    else:
        workspace.current_stage = stage_key
    workspace.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(workspace)
    stages = await _get_stages(db, workspace_id)
    asyncio.create_task(
        _sync_stage_memories_after_approval(
            workspace_id=workspace.id,
            stage_key=stage.stage_key,
            user_id=user.id,
            source_text=stage_content,
            source_artifact_id=conclusion_artifact_id,
        )
    )
    return WorkspaceResponse.from_model(workspace, member.role, stages)


@router.post("/workspaces/{workspace_id}/stages/{stage_key}/request-revision", response_model=WorkspaceResponse)
async def request_stage_revision(
    workspace_id: str,
    stage_key: WorkspaceStageKey,
    req: StageFeedbackRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    workspace, member = await _get_accessible_workspace(db, workspace_id, user)
    _require_editor(member)

    result = await db.execute(
        select(WorkspaceStage).where(
            WorkspaceStage.workspace_id == workspace_id,
            WorkspaceStage.stage_key == stage_key,
        )
    )
    stage = result.scalar_one_or_none()
    if not stage:
        raise HTTPException(status_code=404, detail="Workspace stage not found")

    feedback = req.feedback.strip()
    if not feedback:
        raise HTTPException(status_code=400, detail="调整说明不能为空")

    stages = await _get_stages(db, workspace_id)
    target_order = stage.order

    stage.status = WorkspaceStageStatus.REVISION_REQUESTED
    stage.user_feedback = feedback
    stage.approved_by = None
    stage.approved_at = None

    await _append_stage_message(
        db=db,
        stage=stage,
        role="user",
        content=feedback,
        kind="chat",
    )

    for item in stages:
        if item.id == stage.id or item.order <= target_order or item.status == WorkspaceStageStatus.SKIPPED:
            continue
        if item.status != WorkspaceStageStatus.DRAFT:
            item.status = WorkspaceStageStatus.REVISION_REQUESTED
            item.approved_by = None
            item.approved_at = None
            item.user_feedback = f"上游阶段「{stage.title}」已调整，本阶段需要重新确认。"

    workspace.current_stage = stage.stage_key
    await _supersede_stage_memories_from_order(
        db=db,
        workspace_id=workspace_id,
        stages=stages,
        from_order=target_order,
    )
    workspace.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(workspace)
    stages = await _get_stages(db, workspace_id)
    return WorkspaceResponse.from_model(workspace, member.role, stages)
