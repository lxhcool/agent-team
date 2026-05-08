"""Workspace API endpoints."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sse_starlette.sse import EventSourceResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.database import get_db
from app.api.flow_schemas import (
    CreateWorkspaceRequest,
    GenerateStageRequest,
    SendWorkspaceStageMessageRequest,
    StageFeedbackRequest,
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
    append_stage_message as _append_stage_message,
    bootstrap_stage_messages as _bootstrap_stage_messages,
    build_model_unavailable_stage_output as _build_model_unavailable_stage_output,
    ensure_stage_seed_messages as _ensure_stage_seed_messages,
    finalize_stage_conclusion as _finalize_stage_conclusion,
    generate_and_append_stage_reply as _generate_and_append_stage_reply,
    generate_requirements_stage_content as _generate_requirements_stage_content,
    get_stage_messages as _get_stage_messages,
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


STAGE_AGENT_NAMES: dict[WorkspaceStageKey, str] = {
    WorkspaceStageKey.REQUIREMENTS: "requirements-analyst",
    WorkspaceStageKey.PRODUCT: "product-designer",
    WorkspaceStageKey.UI_DIRECTION: "ui-ux-designer",
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
        "需求澄清",
        "明确背景、目标、角色、约束和待澄清问题。",
        ["明确背景与目标", "补齐关键缺口", "沉淀第一版结论"],
    ),
    (
        WorkspaceStageKey.PRODUCT,
        "范围定义",
        "明确这次做什么、不做什么，以及优先级和范围边界。",
        ["功能范围", "优先级", "本期边界"],
    ),
    (
        WorkspaceStageKey.UI_DIRECTION,
        "方案整理",
        "形成可交付的流程、结构、模块清单、页面说明和关键规则。",
        ["流程结构", "模块/页面清单", "关键规则"],
    ),
    (
        WorkspaceStageKey.PROTOTYPE,
        "补充材料（可选）",
        "按需补充页面草图、截图、参考示意或其他辅助材料；不是主流程必经阶段。",
        ["页面草图", "示意截图", "补充说明"],
    ),
    (
        WorkspaceStageKey.TECHNICAL,
        "实现约束",
        "明确实现边界、数据要求、依赖项、风险和不做项。",
        ["实现边界", "数据要求", "风险与不做项"],
    ),
    (
        WorkspaceStageKey.DEVELOPMENT,
        "实现准备",
        "沉淀给设计、开发或协作者接手的实现准备文档。",
        ["开发说明", "模块拆分建议", "验收标准"],
    ),
    (
        WorkspaceStageKey.ACCEPTANCE,
        "验收口径",
        "明确如何判断当前方案是否达标，以及还缺什么。",
        ["验收标准", "风险提示", "待确认项"],
    ),
    (
        WorkspaceStageKey.DEPLOYMENT,
        "交付总览",
        "汇总各阶段产物、当前版本、已确认事项和建议下一步。",
        ["产物索引", "版本说明", "下一步建议"],
    ),
]


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


async def _get_stages(db: AsyncSession, workspace_id: str) -> List[WorkspaceStage]:
    result = await db.execute(
        select(WorkspaceStage)
        .where(WorkspaceStage.workspace_id == workspace_id)
        .order_by(WorkspaceStage.order)
    )
    return list(result.scalars().all())


async def _generate_stage_artifact_with_llm(
    db: AsyncSession,
    user: User,
    workspace: Workspace,
    stages: List[WorkspaceStage],
    stage: WorkspaceStage,
    instruction: Optional[str],
) -> Optional[tuple[Dict[str, Any], str]]:
    return await _generate_stage_artifact_with_llm_service(
        db=db,
        user=user,
        workspace=workspace,
        stages=stages,
        stage=stage,
        instruction=instruction,
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

    generator = await _stream_stage_reply(
        db=db,
        user=user,
        workspace=workspace,
        stages=stages,
        stage=stage,
        messages=messages,
        resolve_generation_model=_resolve_generation_model,
        create_workspace_artifact=_create_workspace_artifact,
        load_recommendation=_load_recommendation,
        upsert_artifact_reference=_upsert_artifact_reference,
        serialize_stage_response=lambda item: WorkspaceStageResponse.from_model(item).model_dump(mode="json"),
        serialize_stage_message_response=lambda item: WorkspaceStageMessageResponse.from_model(item, workspace.id).model_dump(mode="json"),
        parse_json_object=_parse_json_object,
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

    if await _should_finalize_stage_message(
        db=db,
        user=user,
        workspace=workspace,
        stage=stage,
        messages=messages,
        resolve_generation_model=_resolve_generation_model,
        parse_json_object=_parse_json_object,
    ):
        await _finalize_stage_conclusion(
            db=db,
            user=user,
            workspace=workspace,
            stages=stages,
            stage=stage,
            messages=messages,
            resolve_generation_model=_resolve_generation_model,
            create_workspace_artifact=_create_workspace_artifact,
            load_recommendation=_load_recommendation,
            upsert_artifact_reference=_upsert_artifact_reference,
        )
    else:
        await _generate_and_append_stage_reply(
            db=db,
            user=user,
            workspace=workspace,
            stages=stages,
            stage=stage,
            messages=messages,
            resolve_generation_model=_resolve_generation_model,
            generate_stage_artifact_with_llm=_generate_stage_artifact_with_llm,
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

    generator = await _stream_stage_reply(
        db=db,
        user=user,
        workspace=workspace,
        stages=stages,
        stage=stage,
        messages=messages,
        resolve_generation_model=_resolve_generation_model,
        create_workspace_artifact=_create_workspace_artifact,
        load_recommendation=_load_recommendation,
        upsert_artifact_reference=_upsert_artifact_reference,
        serialize_stage_response=lambda item: WorkspaceStageResponse.from_model(item).model_dump(mode="json"),
        serialize_stage_message_response=lambda item: WorkspaceStageMessageResponse.from_model(item, workspace.id).model_dump(mode="json"),
        parse_json_object=_parse_json_object,
    )
    return EventSourceResponse(generator)


@router.post("/workspaces/{workspace_id}/stages/{stage_key}/finalize-stream")
async def finalize_workspace_stage_stream(
    workspace_id: str,
    stage_key: WorkspaceStageKey,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    workspace, member = await _get_accessible_workspace(db, workspace_id, user)
    _require_editor(member)

    stages = await _get_stages(db, workspace_id)
    stage = next((item for item in stages if item.stage_key == stage_key), None)
    if not stage:
        raise HTTPException(status_code=404, detail="Workspace stage not found")

    messages = await _ensure_stage_seed_messages(
        db=db,
        workspace=workspace,
        stage=stage,
        existing_messages=await _get_stage_messages(db, stage.id),
    )
    await db.commit()
    messages = await _get_stage_messages(db, stage.id)

    generator = await _stream_stage_reply(
        db=db,
        user=user,
        workspace=workspace,
        stages=stages,
        stage=stage,
        messages=messages,
        resolve_generation_model=_resolve_generation_model,
        create_workspace_artifact=_create_workspace_artifact,
        load_recommendation=_load_recommendation,
        upsert_artifact_reference=_upsert_artifact_reference,
        serialize_stage_response=lambda item: WorkspaceStageResponse.from_model(item).model_dump(mode="json"),
        serialize_stage_message_response=lambda item: WorkspaceStageMessageResponse.from_model(item, workspace.id).model_dump(mode="json"),
        parse_json_object=_parse_json_object,
        force_finalize=True,
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
        recommendation, content = await _generate_requirements_stage_content(
            db=db,
            user=user,
            workspace=workspace,
            stages=stages,
            stage=stage,
            instruction=req.instruction,
            resolve_generation_model=_resolve_generation_model,
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
        raise HTTPException(status_code=400, detail="当前仅需求澄清阶段支持流式生成")

    generator = await _stream_requirements_stage_document(
        db=db,
        user=user,
        workspace=workspace,
        stages=stages,
        stage=stage,
        instruction=req.instruction,
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
    if not _has_generated_recommendation(_load_recommendation, stage):
        raise HTTPException(status_code=400, detail="请先生成推荐方案，再确认通过当前阶段")
    has_conclusion = await _stage_has_conclusion_artifact(db, stage)
    if not has_conclusion:
        raise HTTPException(status_code=400, detail="请先生成阶段结论，再进入下一阶段")

    stage.status = WorkspaceStageStatus.APPROVED
    stage.approved_by = user.id
    stage.approved_at = datetime.now(timezone.utc)

    next_key = _next_stage_key(stage_key)
    if next_key:
        workspace.current_stage = next_key
        next_stage = stage_map.get(next_key)
        if next_stage and next_stage.status == WorkspaceStageStatus.DRAFT:
            next_stage.status = WorkspaceStageStatus.AWAITING_CONFIRMATION
        if next_stage:
            await _bootstrap_stage_messages(
                db=db,
                user=user,
                workspace=workspace,
                stages=stages,
                stage=next_stage,
                resolve_generation_model=_resolve_generation_model,
                generate_stage_artifact_with_llm=_generate_stage_artifact_with_llm,
            )
    else:
        workspace.current_stage = stage_key
    workspace.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(workspace)
    stages = await _get_stages(db, workspace_id)
    return WorkspaceResponse.from_model(workspace, member.role, stages)


@router.post("/workspaces/{workspace_id}/stages/{stage_key}/request-revision", response_model=WorkspaceStageResponse)
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

    stage.status = WorkspaceStageStatus.REVISION_REQUESTED
    stage.user_feedback = req.feedback
    workspace.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(stage)
    return WorkspaceStageResponse.from_model(stage)
