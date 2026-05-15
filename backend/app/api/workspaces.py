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
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.database import async_session, get_db
from app.api.flow_schemas import (
    CreateWorkspaceRequest,
    SendWorkspaceStageMessageRequest,
    StageFeedbackRequest,
    StageRunSettingsRequest,
    UpdateStageRequest,
    UpdateWorkspaceRequest,
    WorkspaceArtifactResponse,
    WorkspaceResponse,
    WorkspaceStageChatResponse,
    WorkspaceStageMessageResponse,
    WorkspaceStageReviewResponse,
    WorkspaceStageResponse,
)
from app.api.authz import get_user_from_query_token
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
    WorkspaceStageReview,
    WorkspaceStageStatus,
    WorkspaceStatus,
)
from app.services.flows.conversation import (
    append_stage_message as _append_stage_message,
    ensure_stage_seed_messages as _ensure_stage_seed_messages,
    generate_and_append_stage_reply as _generate_and_append_stage_reply,
    get_stage_messages as _get_stage_messages,
    stream_stage_reply as _stream_stage_reply,
)
from app.services.flows.artifacts import (
    create_workspace_artifact as _create_workspace_artifact,
    find_latest_workspace_artifact as _find_latest_workspace_artifact,
    load_recommendation as _load_recommendation,
    upsert_artifact_reference as _upsert_artifact_reference,
)
from app.services.flows.memory import (
    latest_conclusion_artifact_id as _latest_conclusion_artifact_id,
    supersede_stage_memories_from_order as _supersede_stage_memories_from_order,
    sync_memories_from_source as _sync_memories_from_source,
)
from app.services.flows.stage_state import (
    default_recommendation as _default_recommendation,
    has_generated_recommendation as _has_generated_recommendation,
    next_stage_key as _next_stage_key,
    seed_stages as _seed_stages,
)
from app.services.flows.stage_generation import resolve_generation_model as _resolve_generation_model
from app.services.flows.stage_reviews import (
    EXPERT_REVIEW_STAGES,
    StageReviewError,
    build_review_revision_instruction as _build_review_revision_instruction,
    create_queued_stage_review as _create_queued_stage_review,
    run_stage_expert_review as _run_stage_expert_review,
)
from app.services.flows.stage_snapshots import with_stage_snapshot as _with_stage_snapshot

router = APIRouter()
optional_bearer = HTTPBearer(auto_error=False)
logger = logging.getLogger(__name__)


STAGE_DEFINITIONS = [
    (
        WorkspaceStageKey.REQUIREMENTS,
        "需求确认",
        "只确认这是什么产品、给谁用、最基本怎么成立；不提前设计功能、页面或实现方案。",
        ["产品定义", "目标用户", "基本使用方式", "结构性前提"],
    ),
    (
        WorkspaceStageKey.PRODUCT,
        "方案设计",
        "把方案骨架搭起来：功能模块怎么分、模块怎么协作、页面结构怎么组织、主要流程怎么走。",
        ["功能模块", "模块关系", "页面结构与流程"],
    ),
    (
        WorkspaceStageKey.UI_DIRECTION,
        "细节确认",
        "把业务规则说透：角色权限、状态流转、异常处理、数据口径和关键边界怎么定。",
        ["规则边界", "状态流转", "异常与数据口径"],
    ),
    (
        WorkspaceStageKey.TECHNICAL,
        "开发方案",
        "整理开发可直接接手的方案：实现路径、模块拆分、接口数据组织、依赖和风险。",
        ["模块拆分", "接口与数据", "依赖与风险"],
    ),
    (
        WorkspaceStageKey.DEPLOYMENT,
        "最终交付",
        "整理一份最终总结结论，并同步形成最后的交付文档，支持单独下载或整体打包下载。",
        ["最终总结", "交付文档", "整体打包下载"],
    ),
]

STAGE_DEFINITION_MAP = {
    stage_key: {
        "title": title,
        "description": description,
        "focus": focus,
    }
    for stage_key, title, description, focus in STAGE_DEFINITIONS
}


def _normalize_stage_metadata(stage: WorkspaceStage) -> bool:
    canonical = STAGE_DEFINITION_MAP.get(stage.stage_key)
    if not canonical:
        return False
    changed = False
    if stage.title != canonical["title"]:
        stage.title = canonical["title"]
        changed = True
    if (stage.description or "") != canonical["description"]:
        stage.description = canonical["description"]
        changed = True
    return changed


async def _stream_with_fresh_session(
    *,
    workspace_id: str,
    stage_key: WorkspaceStageKey,
    user: User,
    force_finalize: bool = False,
    runtime_options: Optional[Dict[str, Any]] = None,
    extra_instruction: Optional[str] = None,
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
                force_finalize=force_finalize,
                runtime_options=runtime_options,
                extra_instruction=extra_instruction,
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


async def _sync_stage_memories_after_user_message(
    *,
    workspace_id: str,
    stage_key: WorkspaceStageKey,
    user_id: str,
    source_text: str,
    source_message_id: str,
    runtime_options: Optional[Dict[str, Any]] = None,
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
                source_kind="user_message",
                source_text=source_text,
                source_message_id=source_message_id,
                resolve_generation_model=_make_generation_resolver(runtime_options),
            )
            await session.commit()
        except Exception as exc:
            if session.in_transaction():
                await session.rollback()
            logger.warning(
                "async user-message memory sync failed: workspace=%s stage=%s message=%s reason=%s",
                workspace_id,
                stage_key.value,
                source_message_id,
                exc,
            )


async def _get_stages(db: AsyncSession, workspace_id: str) -> List[WorkspaceStage]:
    result = await db.execute(
        select(WorkspaceStage)
        .where(WorkspaceStage.workspace_id == workspace_id)
        .order_by(WorkspaceStage.order)
    )
    stages = list(result.scalars().all())
    changed = False
    for stage in stages:
        changed = _normalize_stage_metadata(stage) or changed
    if changed:
        await db.flush()
    return stages


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


def _stage_content(stages: List[WorkspaceStage], key: WorkspaceStageKey) -> str:
    for stage in stages:
        if stage.stage_key == key and stage.content:
            return stage.content
    return ""


async def _reset_stage_generated_state(
    db: AsyncSession,
    stage: WorkspaceStage,
) -> None:
    canonical = STAGE_DEFINITION_MAP.get(stage.stage_key) or {}
    focus = canonical.get("focus") or []
    stage.content = None
    stage.recommendation_json = json.dumps(
        _default_recommendation(stage.stage_key, focus),
        ensure_ascii=False,
    )
    stage.approved_by = None
    stage.approved_at = None

    await db.execute(
        delete(WorkspaceStageMessage).where(WorkspaceStageMessage.stage_id == stage.id)
    )


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


def _require_expert_review_stage(stage_key: WorkspaceStageKey) -> None:
    if stage_key not in EXPERT_REVIEW_STAGES:
        raise HTTPException(status_code=404, detail="当前阶段没有专业视角检查")


@router.get("/workspaces/{workspace_id}/stages/{stage_key}/reviews", response_model=List[WorkspaceStageReviewResponse])
async def list_workspace_stage_reviews(
    workspace_id: str,
    stage_key: WorkspaceStageKey,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_expert_review_stage(stage_key)
    await _get_accessible_workspace(db, workspace_id, user)
    stages = await _get_stages(db, workspace_id)
    stage = next((item for item in stages if item.stage_key == stage_key), None)
    if not stage:
        raise HTTPException(status_code=404, detail="Workspace stage not found")

    result = await db.execute(
        select(WorkspaceStageReview)
        .where(
            WorkspaceStageReview.workspace_id == workspace_id,
            WorkspaceStageReview.stage_id == stage.id,
        )
        .order_by(WorkspaceStageReview.created_at.desc())
    )
    reviews = list(result.scalars().all())
    return [WorkspaceStageReviewResponse.from_model(review) for review in reviews]


async def _run_workspace_stage_review_task(
    *,
    review_id: str,
    workspace_id: str,
    stage_key: WorkspaceStageKey,
    user_id: str,
    runtime_options: Dict[str, Any],
) -> None:
    async with async_session() as session:
        try:
            user = await session.get(User, user_id)
            workspace = await session.get(Workspace, workspace_id)
            review = await session.get(WorkspaceStageReview, review_id)
            if not user or not workspace or not review:
                return

            stages = await _get_stages(session, workspace_id)
            stage = next((item for item in stages if item.stage_key == stage_key), None)
            if not stage:
                review.status = "failed"
                review.summary = "专业视角检查失败：阶段不存在。"
                await session.commit()
                return

            messages = await _get_stage_messages(session, stage.id)
            await _run_stage_expert_review(
                db=session,
                user=user,
                workspace=workspace,
                stages=stages,
                stage=stage,
                messages=messages,
                review=review,
                runtime_options=runtime_options,
                resolve_generation_model=_make_generation_resolver(runtime_options),
            )
        except Exception as exc:
            logger.exception(
                "workspace stage review task failed: review=%s workspace=%s stage=%s",
                review_id,
                workspace_id,
                stage_key.value,
            )
            try:
                if session.in_transaction():
                    await session.rollback()
                review = await session.get(WorkspaceStageReview, review_id)
                if review and review.status != "superseded":
                    review.status = "failed"
                    review.summary = f"专业视角检查失败：{exc}"
                    review.result_json = json.dumps(
                        {
                            "overall_judgment": "专业检查生成失败",
                            "why": str(exc),
                            "main_risks": [],
                            "expert_conflicts": [],
                            "suggested_supplements": ["请稍后重新生成专业视角检查，或检查模型配置。"],
                            "focus_for_user": ["这次检查不可作为进入下一阶段的依据。"],
                            "can_enter_next_stage": False,
                            "user_confirmation_questions": [],
                        },
                        ensure_ascii=False,
                    )
                    await session.commit()
            except Exception:
                logger.exception("failed to persist review task failure: review=%s", review_id)


async def _mark_stage_reviews_superseded(
    db: AsyncSession,
    *,
    workspace_id: str,
    stage_ids: List[str],
    except_review_id: Optional[str] = None,
) -> None:
    if not stage_ids:
        return
    result = await db.execute(
        select(WorkspaceStageReview).where(
            WorkspaceStageReview.workspace_id == workspace_id,
            WorkspaceStageReview.stage_id.in_(stage_ids),
        )
    )
    for review in result.scalars().all():
        if except_review_id and review.id == except_review_id:
            continue
        if review.status == "superseded":
            continue
        review.status = "superseded"


@router.post("/workspaces/{workspace_id}/stages/{stage_key}/reviews", response_model=WorkspaceStageReviewResponse)
async def create_workspace_stage_review(
    workspace_id: str,
    stage_key: WorkspaceStageKey,
    req: Optional[StageRunSettingsRequest] = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_expert_review_stage(stage_key)
    workspace, member = await _get_accessible_workspace(db, workspace_id, user)
    _require_editor(member)

    stages = await _get_stages(db, workspace_id)
    stage = next((item for item in stages if item.stage_key == stage_key), None)
    if not stage:
        raise HTTPException(status_code=404, detail="Workspace stage not found")
    _require_stage_predecessors_approved(stages, stage)

    runtime_options = _build_runtime_options(req.settings if req else None)
    messages = await _get_stage_messages(db, stage.id)
    try:
        review = _create_queued_stage_review(
            db=db,
            user=user,
            workspace=workspace,
            stage=stage,
            messages=messages,
        )
    except StageReviewError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await db.commit()
    await db.refresh(review)
    asyncio.create_task(
        _run_workspace_stage_review_task(
            review_id=review.id,
            workspace_id=workspace.id,
            stage_key=stage.stage_key,
            user_id=user.id,
            runtime_options=runtime_options,
        )
    )
    return WorkspaceStageReviewResponse.from_model(review)


@router.post("/workspaces/{workspace_id}/stages/{stage_key}/reviews/{review_id}/apply", response_model=WorkspaceStageChatResponse)
async def apply_workspace_stage_review(
    workspace_id: str,
    stage_key: WorkspaceStageKey,
    review_id: str,
    req: Optional[StageRunSettingsRequest] = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_expert_review_stage(stage_key)
    workspace, member = await _get_accessible_workspace(db, workspace_id, user)
    _require_editor(member)

    stages = await _get_stages(db, workspace_id)
    stage = next((item for item in stages if item.stage_key == stage_key), None)
    if not stage:
        raise HTTPException(status_code=404, detail="Workspace stage not found")
    _require_stage_predecessors_approved(stages, stage)
    if stage.status == WorkspaceStageStatus.APPROVED:
        raise HTTPException(status_code=400, detail="当前阶段已确认，请先发起调整，再应用专业检查")

    review = await db.get(WorkspaceStageReview, review_id)
    if not review or review.workspace_id != workspace_id or review.stage_id != stage.id:
        raise HTTPException(status_code=404, detail="专业视角检查不存在")
    if review.status in {"queued", "running"}:
        raise HTTPException(status_code=400, detail="专业视角检查还在生成中，请稍后再应用")
    if review.status == "failed":
        raise HTTPException(status_code=400, detail="专业视角检查失败，不能用于修订方案")
    if review.status == "superseded":
        raise HTTPException(status_code=400, detail="专业视角检查基于旧版本，请重新检查后再应用")

    runtime_options = _build_runtime_options(req.settings if req else None)
    instruction = _build_review_revision_instruction(review)
    await _append_stage_message(
        db=db,
        stage=stage,
        role="user",
        content=instruction,
        kind="chat",
    )
    messages = await _get_stage_messages(db, stage.id)
    await _generate_and_append_stage_reply(
        db=db,
        user=user,
        workspace=workspace,
        stages=stages,
        stage=stage,
        messages=messages,
        extra_instruction="这轮是根据专业视角检查修订当前方案。请直接输出修订后的方案，不要只解释检查意见。",
        resolve_generation_model=_make_generation_resolver(runtime_options),
        runtime_options=runtime_options,
        create_workspace_artifact=_create_workspace_artifact,
        load_recommendation=_load_recommendation,
        upsert_artifact_reference=_upsert_artifact_reference,
    )

    await _mark_stage_reviews_superseded(db, workspace_id=workspace_id, stage_ids=[stage.id], except_review_id=review.id)
    await db.commit()
    await db.refresh(stage)
    messages = await _get_stage_messages(db, stage.id)
    return WorkspaceStageChatResponse(
        stage=WorkspaceStageResponse.from_model(stage),
        messages=[WorkspaceStageMessageResponse.from_model(message, workspace.id) for message in messages],
    )


@router.post("/workspaces/{workspace_id}/stages/{stage_key}/reviews/{review_id}/apply-stream")
async def apply_workspace_stage_review_stream(
    workspace_id: str,
    stage_key: WorkspaceStageKey,
    review_id: str,
    req: Optional[StageRunSettingsRequest] = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_expert_review_stage(stage_key)
    workspace, member = await _get_accessible_workspace(db, workspace_id, user)
    _require_editor(member)

    stages = await _get_stages(db, workspace_id)
    stage = next((item for item in stages if item.stage_key == stage_key), None)
    if not stage:
        raise HTTPException(status_code=404, detail="Workspace stage not found")
    _require_stage_predecessors_approved(stages, stage)
    if stage.status == WorkspaceStageStatus.APPROVED:
        raise HTTPException(status_code=400, detail="当前阶段已确认，请先发起调整，再应用专业检查")

    review = await db.get(WorkspaceStageReview, review_id)
    if not review or review.workspace_id != workspace_id or review.stage_id != stage.id:
        raise HTTPException(status_code=404, detail="专业视角检查不存在")
    if review.status in {"queued", "running"}:
        raise HTTPException(status_code=400, detail="专业视角检查还在生成中，请稍后再应用")
    if review.status == "failed":
        raise HTTPException(status_code=400, detail="专业视角检查失败，不能用于修订方案")
    if review.status == "superseded":
        raise HTTPException(status_code=400, detail="专业视角检查基于旧版本，请重新检查后再应用")

    runtime_options = _build_runtime_options(req.settings if req else None)
    instruction = "\n\n".join(
        [
            _build_review_revision_instruction(review),
            "这轮是根据专业视角检查修订当前方案。请直接输出修订后的方案，不要只解释检查意见。",
        ]
    )
    await _mark_stage_reviews_superseded(db, workspace_id=workspace_id, stage_ids=[stage.id], except_review_id=review.id)
    await db.commit()

    generator = await _stream_with_fresh_session(
        workspace_id=workspace_id,
        stage_key=stage_key,
        user=user,
        runtime_options=runtime_options,
        extra_instruction=instruction,
    )
    return EventSourceResponse(generator)


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
        force_finalize=stage_key == WorkspaceStageKey.DEPLOYMENT,
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
        appended_user_message = await _append_stage_message(
            db=db,
            stage=stage,
            role="user",
            content=content,
            kind="chat",
        )
        await db.commit()
        asyncio.create_task(
            _sync_stage_memories_after_user_message(
                workspace_id=workspace.id,
                stage_key=stage.stage_key,
                user_id=user.id,
                source_text=content,
                source_message_id=appended_user_message.id,
                runtime_options=runtime_options,
            )
        )
        messages = await _get_stage_messages(db, stage.id)

    await _generate_and_append_stage_reply(
        db=db,
        user=user,
        workspace=workspace,
        stages=stages,
        stage=stage,
        messages=messages,
        extra_instruction=None,
        resolve_generation_model=resolve_generation_model,
        runtime_options=runtime_options,
        create_workspace_artifact=_create_workspace_artifact,
        load_recommendation=_load_recommendation,
        upsert_artifact_reference=_upsert_artifact_reference,
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
        await db.commit()
        asyncio.create_task(
            _sync_stage_memories_after_user_message(
                workspace_id=workspace.id,
                stage_key=stage.stage_key,
                user_id=user.id,
                source_text=content,
                source_message_id=appended_user_message.id,
                runtime_options=runtime_options,
            )
        )
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
    recommendation = _load_recommendation(stage)
    stage.recommendation_json = json.dumps(_with_stage_snapshot(recommendation, stage), ensure_ascii=False)

    stage.status = WorkspaceStageStatus.APPROVED
    stage.approved_by = user.id
    stage.approved_at = datetime.now(timezone.utc)

    next_key = _next_stage_key(stage_key)
    if next_key:
        workspace.current_stage = next_key
        next_stage = stage_map.get(next_key)
        if next_stage and next_stage.status in {WorkspaceStageStatus.DRAFT, WorkspaceStageStatus.REVISION_REQUESTED}:
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
            item.user_feedback = f"上游阶段「{stage.title}」已调整，本阶段需要重新确认。"
            await _reset_stage_generated_state(db, item)

    affected_stage_ids = [item.id for item in stages if item.order >= target_order]
    await _mark_stage_reviews_superseded(db, workspace_id=workspace_id, stage_ids=affected_stage_ids)
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
