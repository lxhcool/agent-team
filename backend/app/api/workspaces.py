"""Workspace API endpoints."""

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.models import (
    User,
    Workspace,
    WorkspaceMember,
    WorkspaceMemberRole,
    WorkspaceStage,
    WorkspaceStageKey,
    WorkspaceStageStatus,
    WorkspaceStatus,
)

router = APIRouter()


STAGE_DEFINITIONS = [
    (
        WorkspaceStageKey.REQUIREMENTS,
        "需求确认",
        "确认产品目标、用户、核心场景和第一版范围。",
        ["确认目标用户", "确认核心问题", "收敛第一版范围"],
    ),
    (
        WorkspaceStageKey.PRODUCT,
        "产品方案",
        "确认功能列表、页面结构、用户流程和优先级。",
        ["页面列表", "用户主流程", "功能优先级"],
    ),
    (
        WorkspaceStageKey.UI_DIRECTION,
        "UI 方向",
        "确认视觉风格、参考案例、颜色倾向和关键页面气质。",
        ["推荐 2-3 个风格方向", "支持上传参考图", "记录用户选择"],
    ),
    (
        WorkspaceStageKey.PROTOTYPE,
        "原型确认",
        "用真实页面预览或截图确认页面长相和交互结构。",
        ["桌面端截图", "移动端截图", "多模态 UI 评审"],
    ),
    (
        WorkspaceStageKey.TECHNICAL,
        "技术方案",
        "确认技术栈、数据模型、第三方服务和部署方式。",
        ["技术栈推荐", "数据隔离方案", "部署测试方案"],
    ),
    (
        WorkspaceStageKey.DEVELOPMENT,
        "开发执行",
        "进入代码生成和修改，所有变更先创建 checkpoint。",
        ["生成任务计划", "执行代码修改", "记录文件变更"],
    ),
    (
        WorkspaceStageKey.ACCEPTANCE,
        "预览验收",
        "用户查看可运行预览，确认结果或继续修改。",
        ["本地预览", "验收反馈", "一键回滚"],
    ),
    (
        WorkspaceStageKey.DEPLOYMENT,
        "部署测试",
        "发布到测试环境并记录访问地址和部署结果。",
        ["测试地址", "部署日志", "发布检查"],
    ),
]


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

    @classmethod
    def from_model(
        cls,
        workspace: Workspace,
        role: Optional[WorkspaceMemberRole] = None,
        stages: Optional[List[WorkspaceStage]] = None,
    ):
        stage_responses = [WorkspaceStageResponse.from_model(s) for s in stages] if stages is not None else None
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


def _default_recommendation(stage_key: WorkspaceStageKey, focus: List[str]) -> Dict[str, Any]:
    return {
        "summary": "等待 AI 团队生成推荐方案。当前先记录该阶段需要用户确认的重点。",
        "recommended_action": "先确认本阶段方向，再进入下一步。",
        "focus": focus,
        "artifacts": [],
    }


def _seed_stages(workspace: Workspace, initial_requirement: Optional[str]) -> List[WorkspaceStage]:
    stages = []
    for order, (stage_key, title, description, focus) in enumerate(STAGE_DEFINITIONS):
        status = WorkspaceStageStatus.DRAFT
        content = None
        if stage_key == WorkspaceStageKey.REQUIREMENTS:
            status = WorkspaceStageStatus.AWAITING_CONFIRMATION
            content = initial_requirement or "请补充你想做的网站/小程序需求。"
        stages.append(
            WorkspaceStage(
                workspace_id=workspace.id,
                stage_key=stage_key,
                title=title,
                description=description,
                status=status,
                order=order,
                recommendation_json=json.dumps(
                    _default_recommendation(stage_key, focus), ensure_ascii=False
                ),
                content=content,
            )
        )
    return stages


async def _get_stages(db: AsyncSession, workspace_id: str) -> List[WorkspaceStage]:
    result = await db.execute(
        select(WorkspaceStage)
        .where(WorkspaceStage.workspace_id == workspace_id)
        .order_by(WorkspaceStage.order)
    )
    return list(result.scalars().all())


def _next_stage_key(stage_key: WorkspaceStageKey) -> Optional[WorkspaceStageKey]:
    keys = [definition[0] for definition in STAGE_DEFINITIONS]
    try:
        index = keys.index(stage_key)
    except ValueError:
        return None
    if index + 1 >= len(keys):
        return None
    return keys[index + 1]


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
    stages = _seed_stages(workspace, req.description)
    db.add_all(stages)
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
    return WorkspaceResponse.from_model(workspace, member.role, stages)


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


@router.get("/workspaces/{workspace_id}/stages", response_model=List[WorkspaceStageResponse])
async def list_workspace_stages(
    workspace_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await _get_accessible_workspace(db, workspace_id, user)
    stages = await _get_stages(db, workspace_id)
    return [WorkspaceStageResponse.from_model(stage) for stage in stages]


@router.patch("/workspaces/{workspace_id}/stages/{stage_key}", response_model=WorkspaceStageResponse)
async def update_workspace_stage(
    workspace_id: str,
    stage_key: WorkspaceStageKey,
    req: UpdateStageRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _, member = await _get_accessible_workspace(db, workspace_id, user)
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
    _, member = await _get_accessible_workspace(db, workspace_id, user)
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
    await db.commit()
    await db.refresh(stage)
    return WorkspaceStageResponse.from_model(stage)
