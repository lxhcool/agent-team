"""Workspace API endpoints."""

import asyncio
import hashlib
import html as html_lib
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.api.authz import get_user_from_query_token
from app.api.agents import BUILTIN_AGENTS
from app.models.models import (
    AgentTemplate,
    Artifact,
    Checkpoint,
    ExecutionSession,
    ExecutionStatus,
    ModelSettings,
    PlanningSession,
    PlanningStatus,
    ProviderConfig,
    Task,
    TaskStatus,
    User,
    Workspace,
    WorkspaceMember,
    WorkspaceMemberRole,
    WorkspaceStage,
    WorkspaceStageKey,
    WorkspaceStageStatus,
    WorkspaceStatus,
)
from app.llm.router import LLMError, LLMMessage, llm_router
from app.services.image_generation import (
    ImageGenerationError,
    generate_openai_image,
    resolve_openai_image_provider,
)

router = APIRouter()
logger = logging.getLogger(__name__)
optional_bearer = HTTPBearer(auto_error=False)


STAGE_AGENT_NAMES: dict[WorkspaceStageKey, str] = {
    WorkspaceStageKey.REQUIREMENTS: "requirements-analyst",
    WorkspaceStageKey.PRODUCT: "product-designer",
    WorkspaceStageKey.UI_DIRECTION: "ui-ux-designer",
    WorkspaceStageKey.PROTOTYPE: "ui-ux-designer",
    WorkspaceStageKey.TECHNICAL: "technical-architect",
    WorkspaceStageKey.DEVELOPMENT: "implementation-engineer",
    WorkspaceStageKey.ACCEPTANCE: "qa-reviewer",
    WorkspaceStageKey.DEPLOYMENT: "release-operator",
}

BUILTIN_AGENT_MAP = {agent["name"]: agent for agent in BUILTIN_AGENTS}


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
    storage_mode: str = Field(default="server", max_length=30)
    root_path: Optional[str] = None


class UpdateWorkspaceRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = None
    target_platform: Optional[str] = Field(default=None, max_length=50)
    storage_mode: Optional[str] = Field(default=None, max_length=30)
    root_path: Optional[str] = None
    status: Optional[WorkspaceStatus] = None


class UpdateStageRequest(BaseModel):
    content: Optional[str] = None
    recommendation: Optional[Dict[str, Any]] = None
    user_feedback: Optional[str] = None
    status: Optional[WorkspaceStageStatus] = None


class StageFeedbackRequest(BaseModel):
    feedback: str = Field(..., min_length=1)


class GenerateStageRequest(BaseModel):
    instruction: Optional[str] = None


class ImportLocalWorkspaceRequest(BaseModel):
    root_path: str = Field(..., min_length=1)
    name: Optional[str] = None
    description: Optional[str] = None


class RebindWorkspaceDirectoryRequest(BaseModel):
    root_path: str = Field(..., min_length=1)


class ImportLocalWorkspaceResponse(BaseModel):
    workspace: Dict[str, Any]
    import_mode: str
    message: str


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
    binding_id: Optional[str] = None
    storage_mode: str
    root_path: Optional[str] = None
    local_directory_exists: Optional[bool] = None
    local_manifest_exists: Optional[bool] = None
    binding_state: Optional[str] = None
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
        local_directory_exists = None
        local_manifest_exists = None
        binding_state = None
        if workspace.storage_mode == "local":
            if workspace.root_path:
                project_dir = Path(workspace.root_path).expanduser()
                local_directory_exists = project_dir.exists() and project_dir.is_dir()
                local_manifest_exists = (project_dir / ".agent-workspace.json").exists() if local_directory_exists else False
                if not local_directory_exists:
                    binding_state = "missing_directory"
                elif not local_manifest_exists:
                    binding_state = "missing_manifest"
                else:
                    binding_state = "healthy"
            else:
                local_directory_exists = False
                local_manifest_exists = False
                binding_state = "missing_directory"
        else:
            binding_state = "server_managed"
        return cls(
            id=workspace.id,
            owner_id=workspace.owner_id,
            name=workspace.name,
            description=workspace.description,
            target_platform=workspace.target_platform,
            binding_id=workspace.binding_id,
            storage_mode=workspace.storage_mode,
            root_path=workspace.root_path,
            local_directory_exists=local_directory_exists,
            local_manifest_exists=local_manifest_exists,
            binding_state=binding_state,
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
        "options": [],
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


def _stage_context(workspace: Workspace, stages: List[WorkspaceStage]) -> Dict[str, str]:
    context = {
        "workspace_name": workspace.name,
        "target_platform": workspace.target_platform,
        "storage_mode": workspace.storage_mode,
        "root_path": workspace.root_path or "",
        "initial_requirement": workspace.description or "",
    }
    for stage in stages:
        if stage.content:
            context[stage.stage_key.value] = stage.content
    return context


def _json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _parse_json_object(text: str) -> Dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end >= start:
        cleaned = cleaned[start:end + 1]
    return json.loads(cleaned)


def _sanitize_llm_artifact(payload: Dict[str, Any]) -> tuple[Dict[str, Any], str]:
    recommendation = payload.get("recommendation") if isinstance(payload.get("recommendation"), dict) else {}
    content = payload.get("content") if isinstance(payload.get("content"), str) else ""
    raw_options = recommendation.get("options") if isinstance(recommendation.get("options"), list) else []
    options = []
    for index, item in enumerate(raw_options):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or f"方案 {index + 1}").strip()
        description = str(item.get("description") or "").strip()
        option_content = str(item.get("content") or "").strip()
        options.append({
            "title": title,
            "description": description,
            "content": option_content,
            "recommended": bool(item.get("recommended")),
        })

    selected_option = str(recommendation.get("selected_option") or "").strip()
    if options and selected_option not in {item["title"] for item in options}:
        selected_option = next((item["title"] for item in options if item["recommended"]), options[0]["title"])

    sanitized = {
        "summary": str(recommendation.get("summary") or "AI 团队已生成本阶段推荐方案。"),
        "recommended_action": str(recommendation.get("recommended_action") or "请确认本阶段方案，或提交反馈让 AI 重新调整。"),
        "focus": recommendation.get("focus") if isinstance(recommendation.get("focus"), list) else [],
        "options": options,
        "selected_option": selected_option or None,
        "artifacts": recommendation.get("artifacts") if isinstance(recommendation.get("artifacts"), list) else [],
    }
    if not content and selected_option:
        selected = next((item for item in options if item["title"] == selected_option), None)
        if selected and selected.get("content"):
            content = selected["content"]
    if not content:
        content = "AI 已生成推荐，但没有返回详细产物。请提交反馈后重新生成。"
    return sanitized, content


def _make_option(title: str, description: str, content: str, recommended: bool = False) -> Dict[str, Any]:
    return {
        "title": title,
        "description": description,
        "content": content,
        "recommended": recommended,
    }


def _finalize_recommendation(recommendation: Dict[str, Any], fallback_content: str) -> tuple[Dict[str, Any], str]:
    raw_options = recommendation.get("options") if isinstance(recommendation.get("options"), list) else []
    options = []
    for index, item in enumerate(raw_options):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or f"方案 {index + 1}").strip()
        description = str(item.get("description") or "").strip()
        option_content = str(item.get("content") or "").strip()
        if not title:
            continue
        options.append({
            "title": title,
            "description": description,
            "content": option_content,
            "recommended": bool(item.get("recommended")),
        })

    selected_option = str(recommendation.get("selected_option") or "").strip()
    titles = {item["title"] for item in options}
    if options and selected_option not in titles:
        selected_option = next((item["title"] for item in options if item["recommended"]), options[0]["title"])

    selected_content = ""
    if selected_option:
        selected = next((item for item in options if item["title"] == selected_option), None)
        if selected:
            selected_content = selected.get("content") or ""
    if not selected_content:
        selected_content = fallback_content

    recommendation["options"] = options
    recommendation["selected_option"] = selected_option or None
    return recommendation, selected_content


def _build_ui_direction_options(brief: Dict[str, Any]) -> List[Dict[str, Any]]:
    identity_label = str(brief.get("identity_label") or brief.get("subject") or "当前产品")
    platform_label = str(brief.get("platform_label") or "当前平台")
    primary_goal = str(brief.get("primary_goal") or "")
    sample_items = brief.get("sample_items") if isinstance(brief.get("sample_items"), list) else []
    sample_label = _compact_join([str(item) for item in sample_items], 3)

    if brief.get("identity") == "blog":
        return [
            _make_option(
                "安静阅读型",
                "强调内容阅读和排版节奏，让用户一进来就知道这是一个在认真更新的博客。",
                "\n".join([
                    f"UI 方向：安静阅读型 {identity_label}",
                    f"1. 首屏重点是文章标题、摘要和阅读入口，弱化花哨装饰。",
                    "2. 版式以大标题、舒展留白、清楚正文层级为主。",
                    "3. 导航优先放首页、文章、归档、关于，让访客快速进入阅读路径。",
                    f"4. 示例内容重点围绕：{sample_label}。",
                ]),
                True,
            ),
            _make_option(
                "作者表达型",
                "更突出作者个性、头像、栏目和长期写作主题。",
                "\n".join([
                    f"UI 方向：作者表达型 {identity_label}",
                    "1. 首屏保留更明确的作者介绍、写作主题和长期栏目入口。",
                    "2. 文章卡片和关于区域更有个人表达感。",
                    "3. 适合把博客做成个人品牌或持续输出阵地。",
                ]),
                False,
            ),
            _make_option(
                "杂志编排型",
                "更像内容站首页，强调栏目、专题和持续更新感。",
                "\n".join([
                    f"UI 方向：杂志编排型 {identity_label}",
                    "1. 首页加强栏目导航、精选区和系列内容组织。",
                    "2. 适合文章数量较多，希望访客先浏览再深入阅读。",
                    "3. 会比极简博客更有运营感，但仍保持博客本体。",
                ]),
                False,
            ),
        ]

    if brief.get("identity") == "ecommerce":
        return [
            _make_option(
                "导购成交型",
                "优先突出商品、价格、活动和加购动作，让用户快速进入购买闭环。",
                "\n".join([
                    f"UI 方向：导购成交型 {identity_label}",
                    f"1. 在 {platform_label} 上强化首页推荐、价格标签、优惠信息和加购按钮。",
                    "2. 商品卡片更强调封面、价格、卖点和促销标签。",
                    f"3. 页面重点围绕 {sample_label} 展开，先让用户想买。",
                ]),
                True,
            ),
            _make_option(
                "内容种草型",
                "加入更多场景感和推荐理由，让商品更像被真实推荐出来。",
                "\n".join([
                    f"UI 方向：内容种草型 {identity_label}",
                    "1. 首页会加强专题推荐、养宠场景或搭配建议。",
                    "2. 更适合消费决策需要灵感和解释的商品。",
                    "3. 依旧保留下单路径，但视觉会更像内容电商。",
                ]),
                False,
            ),
            _make_option(
                "会员复购型",
                "更强调常买商品、回购路径和个人中心。",
                "\n".join([
                    f"UI 方向：会员复购型 {identity_label}",
                    "1. 首页更强调已购回购、常用入口和订单追踪。",
                    "2. 适合长期购买频率高的商品场景。",
                    "3. 首版仍然保留商城本体，不额外扩成复杂会员平台。",
                ]),
                False,
            ),
        ]

    if brief.get("identity") == "game":
        return [
            _make_option(
                "经典可玩型",
                "优先保证玩法区、分数和重开入口清楚，第一眼就像能玩的游戏。",
                "\n".join([
                    f"UI 方向：经典可玩型 {identity_label}",
                    "1. 主界面中心就是玩法区域，状态栏和重开按钮紧贴主玩法。",
                    "2. 减少无关装饰，优先让玩家立刻开始一局。",
                    f"3. 当前核心目标是 {primary_goal or 'play'}，所以交互反馈会最直接。",
                ]),
                True,
            ),
            _make_option(
                "街机氛围型",
                "强化分数冲击、动感色彩和游戏氛围，让它更像一个有记忆点的小游戏。",
                "\n".join([
                    f"UI 方向：街机氛围型 {identity_label}",
                    "1. 保留同样的玩法骨架，但视觉更有节奏感和对比度。",
                    "2. 适合想要更强氛围和视觉记忆点的网页小游戏。",
                    "3. 不会改变主玩法，只调整表现方式。",
                ]),
                False,
            ),
            _make_option(
                "轻松休闲型",
                "降低压迫感，让界面更柔和、轻快、易上手。",
                "\n".join([
                    f"UI 方向：轻松休闲型 {identity_label}",
                    "1. 颜色和组件更柔和，提示更友好。",
                    "2. 适合希望小游戏更轻松、更面向泛用户的方向。",
                    "3. 保持主玩法清楚，弱化强竞技感。",
                ]),
                False,
            ),
        ]

    if brief.get("identity") == "dashboard":
        return [
            _make_option(
                "专业清晰型",
                "强调信息层级、指标可读性和处理效率，适合后台。",
                "\n".join([
                    f"UI 方向：专业清晰型 {identity_label}",
                    "1. 首屏更强调总览卡片、列表结构和操作效率。",
                    "2. 组件密度适中偏高，重点是看得清、点得快。",
                    "3. 适合大多数后台和工作台场景。",
                ]),
                True,
            ),
            _make_option(
                "数据看板型",
                "强化指标卡、趋势和监控感，更偏实时业务总览。",
                "\n".join([
                    f"UI 方向：数据看板型 {identity_label}",
                    "1. 首屏更强调趋势、数字和异常提醒。",
                    "2. 适合希望先看到业务状态再进入列表处理的场景。",
                    "3. 不改变后台本体，只强化总览层。",
                ]),
                False,
            ),
            _make_option(
                "操作台型",
                "更强调列表和详情处理，减少装饰和大图。",
                "\n".join([
                    f"UI 方向：操作台型 {identity_label}",
                    "1. 首页更像任务处理台，少看板、多列表。",
                    "2. 适合处理型后台，而不是展示型后台。",
                    "3. 操作入口会更靠前。",
                ]),
                False,
            ),
        ]

    return [
        _make_option(
            "专业清晰型",
            "强调结构稳定、信息清楚和主操作明确，适合作为默认方向。",
            "\n".join([
                f"UI 方向：专业清晰型 {identity_label}",
                "1. 结构稳定、按钮明确、信息层级清楚。",
                "2. 适合作为默认推荐方向，先把产品本体做对。",
                "3. 后续再根据反馈加强品牌感或轻快感。",
            ]),
            True,
        ),
        _make_option(
            "轻快亲和型",
            "更明亮、引导更直接，适合消费端或轻量产品。",
            "\n".join([
                f"UI 方向：轻快亲和型 {identity_label}",
                "1. 用更轻的色彩和更明显的 CTA 降低使用门槛。",
                "2. 适合希望更友好、更容易上手的方向。",
                "3. 保持产品本体不变，只调整表达方式。",
            ]),
            False,
        ),
        _make_option(
            "高级品牌型",
            "更强调质感、氛围和视觉记忆点。",
            "\n".join([
                f"UI 方向：高级品牌型 {identity_label}",
                "1. 加强首屏气质和视觉冲击。",
                "2. 适合品牌展示或对视觉氛围要求更高的场景。",
                "3. 不牺牲主路径清晰度。",
            ]),
            False,
        ),
    ]


def _llm_artifact_matches_brief(
    workspace: Workspace,
    stage_key: WorkspaceStageKey,
    brief: Dict[str, Any],
    recommendation: Dict[str, Any],
    content: str,
) -> bool:
    identity = str(brief.get("identity") or "")
    text = "\n".join([
        workspace.name,
        workspace.description or "",
        content or "",
        str(recommendation.get("summary") or ""),
        " ".join(str(item.get("title") or "") for item in recommendation.get("options", []) if isinstance(item, dict)),
        " ".join(str(item.get("description") or "") for item in recommendation.get("options", []) if isinstance(item, dict)),
    ]).lower()
    if not text.strip():
        return False

    must_have = {
        "blog": ["博客", "文章", "归档", "阅读"],
        "ecommerce": ["商品", "购物车", "下单", "商城"],
        "game": ["游戏", "分数", "开始", "重开"],
        "dashboard": ["总览", "列表", "详情", "后台"],
        "todo": ["任务", "完成", "待办", "删除"],
        "tool": ["输入", "结果", "生成", "工具"],
        "landing": ["首页", "案例", "联系", "价值"],
    }
    forbidden = {
        "blog": ["购物车", "下单", "待办", "任务列表", "管理后台"],
        "ecommerce": ["文章归档", "博客", "待办", "任务列表"],
        "game": ["购物车", "下单", "文章归档", "任务列表"],
        "dashboard": ["博客", "购物车", "开始游戏"],
    }

    positive_hits = sum(1 for token in must_have.get(identity, []) if token.lower() in text)
    negative_hits = sum(1 for token in forbidden.get(identity, []) if token.lower() in text)

    core_objects = brief.get("core_objects") if isinstance(brief.get("core_objects"), list) else []
    core_actions = brief.get("core_actions") if isinstance(brief.get("core_actions"), list) else []
    object_hits = sum(1 for token in core_objects[:4] if str(token).lower() in text)
    action_hits = sum(1 for token in core_actions[:4] if str(token).lower() in text)

    if negative_hits >= 2:
        return False
    if stage_key in {WorkspaceStageKey.REQUIREMENTS, WorkspaceStageKey.PRODUCT, WorkspaceStageKey.UI_DIRECTION}:
        return positive_hits >= 1 and (object_hits + action_hits) >= 1
    return positive_hits >= 1


def _has_generated_recommendation(stage: WorkspaceStage) -> bool:
    recommendation = _load_recommendation(stage)
    return bool(recommendation.get("source"))


def _safe_text(value: Optional[str], fallback: str = "") -> str:
    return html_lib.escape(value or fallback)


def _normalize_storage_mode(value: Optional[str]) -> str:
    mode = (value or "server").strip().lower()
    if mode not in {"server", "local"}:
        raise HTTPException(status_code=400, detail="storage_mode 只支持 server 或 local")
    return mode


def _normalize_root_path(storage_mode: str, root_path: Optional[str], workspace_id: Optional[str] = None) -> Optional[str]:
    cleaned = (root_path or "").strip()
    if storage_mode == "local":
        if not cleaned:
            raise HTTPException(status_code=400, detail="本地目录模式需要提供 root_path")
        path = Path(cleaned).expanduser()
        if not path.is_absolute():
            raise HTTPException(status_code=400, detail="root_path 必须是绝对路径")
        return str(path)
    return None


def _new_binding_id() -> str:
    return f"wsbind_{uuid.uuid4().hex}"


def _ensure_workspace_binding_id(workspace: Workspace) -> str:
    if workspace.binding_id and workspace.binding_id.strip():
        return workspace.binding_id.strip()
    workspace.binding_id = _new_binding_id()
    return workspace.binding_id


def _stage_content(stages: List[WorkspaceStage], key: WorkspaceStageKey) -> str:
    for stage in stages:
        if stage.stage_key == key and stage.content:
            return stage.content
    return ""


def _stage_selected_option(stages: List[WorkspaceStage], key: WorkspaceStageKey) -> str:
    for stage in stages:
        if stage.stage_key != key or not stage.recommendation_json:
            continue
        try:
            recommendation = json.loads(stage.recommendation_json)
        except json.JSONDecodeError:
            continue
        selected = recommendation.get("selected_option")
        if isinstance(selected, str) and selected.strip():
            return selected.strip()
    return ""


def _paragraphs(value: str) -> str:
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    if not lines:
        return "<p>等待补充。</p>"
    return "\n".join(f"<p>{html_lib.escape(line)}</p>" for line in lines[:12])


def _score_keywords(text: str, keywords: List[str]) -> int:
    lowered = (text or "").lower()
    return sum(1 for keyword in keywords if keyword in lowered)


def _first_sentence(value: str, fallback: str) -> str:
    cleaned = " ".join((value or "").split())
    if not cleaned:
        return fallback
    parts = re.split(r"[。！？!?；;\n]", cleaned, maxsplit=1)
    return (parts[0] or cleaned)[:96]


def _platform_label(target_platform: str) -> str:
    return {
        "website": "网站",
        "miniapp": "小程序",
        "dashboard": "管理后台",
        "app": "应用",
    }.get(target_platform, target_platform)


def _compact_join(items: List[str], limit: int = 4) -> str:
    cleaned = [str(item).strip() for item in items if str(item).strip()]
    if not cleaned:
        return "待补充"
    return "、".join(cleaned[:limit])


def _build_landing_brief_data(request_text: str, subject: str, workspace_name: str) -> Dict[str, Any]:
    title = subject if subject and subject != "这个产品" else (workspace_name or "品牌官网")
    lowered = request_text.lower()

    intro_label = "公司介绍" if any(keyword in request_text for keyword in ["公司介绍", "关于我们", "团队介绍", "品牌介绍"]) else "品牌定位"
    service_label = "服务内容" if any(keyword in request_text for keyword in ["服务", "服务内容", "解决方案", "业务"]) else "核心能力"
    case_label = "案例展示" if any(keyword in request_text for keyword in ["案例", "客户案例", "作品"]) else "代表案例"
    contact_label = "联系咨询" if any(keyword in request_text for keyword in ["联系", "咨询", "预约", "留资"]) else "咨询入口"

    if any(keyword in lowered for keyword in ["科技", "软件", "系统", "saas", "数字化", "开发"]):
        hero_subtitle = "用清楚的公司介绍、服务能力、项目案例和联系入口，让访客快速知道你们是谁、能做什么、为什么值得合作。"
        sample_items = [
            "我们帮助企业把业务流程做成更稳定的数字化系统",
            "定制开发、系统升级与长期技术支持",
            "行业案例、交付结果与合作联系入口",
        ]
        sample_descriptions = [
            "首屏先说明公司定位、服务对象和核心优势，不再只是空泛口号。",
            "把服务拆成清楚模块，分别说明适合什么场景、怎么交付、能解决什么问题。",
            "案例区展示真实项目结果，底部保留表单、电话或微信承接咨询。",
        ]
    else:
        hero_subtitle = "把公司是谁、提供什么服务、做过哪些案例，以及怎样快速联系讲清楚，形成一版像样的品牌官网首页。"
        sample_items = [
            "公司是谁，以及为什么值得进一步了解",
            "核心服务模块、交付方式与适合场景",
            "案例证明、客户信任与联系入口",
        ]
        sample_descriptions = [
            "首屏说明品牌定位、服务对象和核心差异，不再只摆一句抽象口号。",
            "把服务拆成 3 到 4 个清楚模块，说明每项服务解决什么问题、如何交付。",
            "案例与联系区承接转化，让访客看完就知道下一步怎么咨询。",
        ]

    return {
        "title": title,
        "hero_subtitle": hero_subtitle,
        "nav_items": ["首页", intro_label, service_label, case_label, "联系"],
        "blocks": [
            (intro_label, "用一句话说明公司定位、服务对象、核心优势和基础信任背书。"),
            (service_label, "把核心服务拆成清楚模块，说明交付内容、流程和适合场景。"),
            (f"{case_label}与{contact_label}", "用代表案例、客户结果和联系入口承接下一步咨询。"),
        ],
        "cards": [
            (intro_label, "定位、优势、团队概况和可信度说明"),
            (service_label, "服务模块、交付方式、流程和适配场景"),
            (f"{case_label}与{contact_label}", "案例亮点、客户反馈、表单和联系方式"),
        ],
        "sample_items": sample_items,
        "sample_descriptions": sample_descriptions,
        "contact_label": contact_label,
        "case_label": case_label,
        "service_label": service_label,
        "intro_label": intro_label,
    }


def _extract_product_subject(*values: str) -> str:
    patterns = [
        r"^(帮我|请|想要|我要|我想|希望|需要|麻烦)?",
        r"(开发|做|制作|创建|搭建|实现|设计|生成|写|做个|搞一个)",
        r"(一个|一款|一套|一个能|一版|一个用于)?",
    ]
    suffix_pattern = r"(的)?(网页版本|网页版|web版|网站版|网站|网页|小程序|app|APP|应用|平台|系统)$"
    for raw in values:
        cleaned = " ".join((raw or "").split())
        if not cleaned:
            continue
        cleaned = re.split(r"[。！？!?；;\n]", cleaned, maxsplit=1)[0].strip()
        cleaned = re.split(r"[，,]", cleaned, maxsplit=1)[0].strip()
        cleaned = re.split(r"(需要|包括|包含|并且|并需|用于|用来|支持|展示|介绍)", cleaned, maxsplit=1)[0].strip()
        for pattern in patterns:
            cleaned = re.sub(pattern, "", cleaned, count=1).strip()
        cleaned = re.sub(suffix_pattern, "", cleaned).strip("：:，,。 ")
        if len(cleaned) >= 2:
            return cleaned[:24]
    return "这个产品"


def _infer_product_identity(text: str, target_platform: str) -> str:
    signals = {
        "blog": [
            "博客", "blog", "文章", "写作", "随笔", "专栏", "归档", "分类", "标签",
            "教程", "文档", "知识库", "作品集", "案例集", "内容站",
        ],
        "ecommerce": [
            "商城", "商店", "商品", "购物", "购物车", "下单", "订单", "sku", "支付",
            "导购", "电商", "店铺", "促销", "热卖", "宠物粮", "宠物用品",
        ],
        "game": [
            "游戏", "小游戏", "2048", "闯关", "棋盘", "合成", "分数", "玩家", "关卡",
            "计时", "排行榜", "角色", "对战", "玩法",
        ],
        "dashboard": [
            "后台", "管理后台", "dashboard", "管理台", "工作台", "数据看板", "报表",
            "审批", "用户管理", "订单管理", "库存", "权限", "运营后台",
        ],
        "todo": [
            "待办", "todo", "to-do", "任务", "清单", "计划", "日程", "看板",
            "提醒", "进度", "事项",
        ],
        "tool": [
            "工具", "生成器", "查询", "计算", "分析", "转换", "上传", "编辑", "表单",
            "提交", "搜索", "评估", "诊断", "助手", "配置",
        ],
        "landing": [
            "官网", "落地页", "品牌", "服务介绍", "预约", "咨询", "联系", "销售",
            "获客", "介绍页", "机构", "公司官网",
        ],
    }
    lowered = (text or "").lower()
    scores = {identity: _score_keywords(lowered, keywords) for identity, keywords in signals.items()}
    if target_platform == "dashboard":
        scores["dashboard"] += 3
    if "2048" in lowered:
        scores["game"] += 4
    if "商城" in text and target_platform == "miniapp":
        scores["ecommerce"] += 2
    priority = ["game", "ecommerce", "dashboard", "blog", "todo", "tool", "landing"]
    best = max(priority, key=lambda item: (scores[item], -priority.index(item)))
    return best if scores[best] > 0 else "landing"


def _build_design_brief(workspace: Workspace, stages: List[WorkspaceStage]) -> Dict[str, Any]:
    requirement = _stage_content(stages, WorkspaceStageKey.REQUIREMENTS) or workspace.description or ""
    product = _stage_content(stages, WorkspaceStageKey.PRODUCT) or ""
    ui_mode = _stage_selected_option(stages, WorkspaceStageKey.UI_DIRECTION) or "专业清晰型"
    request_text = "\n".join(filter(None, [workspace.name, workspace.description or "", requirement, product]))
    identity = _infer_product_identity(request_text, workspace.target_platform)
    subject = _extract_product_subject(workspace.name, requirement, workspace.description or "")
    platform_label = _platform_label(workspace.target_platform)
    app_name = subject if subject != "这个产品" else (workspace.name or "AI Product")
    requirement_summary = _first_sentence(requirement or workspace.description or workspace.name, workspace.name)

    base = {
        "brief_version": "v2",
        "identity": identity,
        "raw_request": workspace.description or workspace.name,
        "subject": subject,
        "app_name": app_name,
        "platform": workspace.target_platform,
        "platform_label": platform_label,
        "ui_mode": ui_mode,
        "requirement_summary": requirement_summary,
        "target_users": [],
        "core_objects": [],
        "core_actions": [],
        "interaction_modes": [],
        "core_pages": [],
        "key_states": [],
        "constraints": [],
        "primary_goal": "",
        "core_loop_text": "",
        "feature_label": "需求驱动界面",
        "status_label": "待确认",
        "panel_title": "核心界面",
        "primary_surface": "landing",
        "hero_title": app_name,
        "hero_subtitle": requirement_summary,
        "primary_action": "开始体验",
        "secondary_action": "查看详情",
        "nav_items": ["首页", "内容", "功能", "关于"],
        "blocks": [],
        "cards": [],
        "sample_items": [],
        "sample_descriptions": [],
        "confirmation_points": ["首屏是否像真实产品", "核心操作是否清楚", "是否符合当前需求"],
        "type_hint": [identity],
        "subtype": "",
        "game_variant": "",
    }

    lowered = request_text.lower()
    if identity == "blog":
        is_portfolio = any(keyword in request_text for keyword in ["作品集", "案例集", "作品展示"])
        topic = subject if subject and subject != "这个产品" else "个人博客"
        type_label = "作品博客" if is_portfolio else "个人博客"
        return {
            **base,
            "identity_label": type_label,
            "target_users": ["想快速了解作者内容与观点的访客"],
            "core_objects": ["文章", "分类", "标签", "作者介绍"],
            "core_actions": ["浏览最新文章", "打开详情阅读", "按分类归档查找", "了解作者"],
            "interaction_modes": ["feed", "detail", "archive"],
            "core_pages": [
                {"key": "home", "name": "首页", "purpose": "展示置顶文章和最新更新"},
                {"key": "post_detail", "name": "文章详情页", "purpose": "沉浸阅读正文内容"},
                {"key": "archive", "name": "归档页", "purpose": "按时间或主题回看文章"},
                {"key": "about", "name": "关于页", "purpose": "介绍作者和站点定位"},
            ],
            "key_states": ["文章列表加载中", "暂无文章空状态", "文章阅读完成后的继续浏览提示"],
            "constraints": ["第一版先不做复杂评论审核", "先不做会员体系", "先不做多作者后台"],
            "primary_goal": "read",
            "core_loop_text": "首页浏览最新文章 -> 打开文章详情 -> 查看归档/分类 -> 继续阅读或了解作者",
            "feature_label": "博客内容界面",
            "status_label": "文章流",
            "panel_title": "博客主界面",
            "primary_surface": "feed",
            "hero_title": topic,
            "hero_subtitle": "记录产品、技术和生活中的长期观察，把零散想法写成值得反复回看的文章与笔记。",
            "primary_action": "阅读最新文章",
            "secondary_action": "浏览归档",
            "nav_items": ["首页", "文章", "归档", "关于"],
            "blocks": [
                ("置顶文章", "首屏直接看到最新主推文章、摘要、发布时间和阅读入口。"),
                ("分类归档", "按主题、标签或时间整理文章，方便回看内容。"),
                ("作者介绍", "交代作者是谁、写什么、为什么值得继续关注。"),
            ],
            "cards": [
                ("最新文章", "文章标题、摘要、发布日期和阅读按钮"),
                ("分类归档", "标签、时间线和搜索入口"),
                ("关于作者", "简介、社交链接和订阅入口"),
            ],
            "sample_items": [
                "把零散想法写成长期博客之后，我改变了什么",
                "最近一周的写作、产品和站点迭代记录",
                "关于我：产品、代码与长期写作",
            ],
            "sample_descriptions": [
                "上周我把 9 篇零散草稿合并成了 3 个长期栏目，写作节奏第一次稳定下来，也终于知道这个博客该坚持记录什么。",
                "这周上线了归档页、重做了文章卡片间距，还把接下来三篇准备写的主题先排进了发布节奏里。",
                "独立开发者 / 产品从业者，长期记录做产品时踩过的坑、写代码时的小实验，以及那些值得慢慢写下来的生活片段。",
            ],
            "confirmation_points": ["一眼看上去是否就是博客", "阅读路径是否自然", "个人表达是否足够明显"],
            "type_hint": ["blog", "content"],
        }

    if identity == "ecommerce":
        is_pet = "宠物" in request_text or "猫" in request_text or "狗" in request_text
        type_label = "宠物商城" if is_pet else "商品商城"
        lead_subject = subject if subject and subject != "这个产品" else type_label
        sample_items = ["冻干主粮组合", "新手养宠必备清单", "本周热卖推荐"] if is_pet else ["主推商品", "限时优惠专区", "今日热卖清单"]
        sample_descriptions = [
            "首屏直接看到主推商品、活动标签和可点击的分类入口，像一个真的在卖东西的首页。",
            "列表区能同时看见价格、适合对象、规格标签和加购入口，用户不用反复点进点出。",
            "详情页承接卖点说明、发货承诺、优惠信息和加入购物车动作，购买闭环是连起来的。",
        ]
        return {
            **base,
            "identity_label": type_label,
            "target_users": ["准备浏览并购买相关商品的消费者"],
            "core_objects": ["商品", "分类", "购物车", "订单"],
            "core_actions": ["浏览推荐", "按分类筛选", "查看商品详情", "加入购物车", "提交订单"],
            "interaction_modes": ["catalog", "detail", "transaction"],
            "core_pages": [
                {"key": "home", "name": "首页", "purpose": "展示推荐商品和活动入口"},
                {"key": "category", "name": "分类页", "purpose": "按品类浏览商品"},
                {"key": "product_detail", "name": "商品详情页", "purpose": "查看规格、价格和加购入口"},
                {"key": "cart", "name": "购物车", "purpose": "确认商品并准备下单"},
                {"key": "orders", "name": "订单页", "purpose": "查看订单状态和售后入口"},
            ],
            "key_states": ["商品加载中", "购物车为空", "加购成功反馈", "下单失败提示"],
            "constraints": ["第一版先不做分销体系", "先不做复杂会员等级", "先不做直播/拼团能力"],
            "primary_goal": "purchase",
            "core_loop_text": "首页推荐 -> 分类/搜索 -> 商品详情 -> 加入购物车 -> 下单",
            "feature_label": "商品目录界面",
            "status_label": "可下单",
            "panel_title": "商城主界面",
            "primary_surface": "catalog",
            "hero_title": lead_subject,
            "hero_subtitle": f"让用户在 {platform_label} 里快速看到推荐商品、进入分类、查看详情并完成加购下单，第一眼就是一个能买东西的商城。",
            "primary_action": "立即逛逛",
            "secondary_action": "查看购物车",
            "nav_items": ["首页", "分类", "购物车", "我的"],
            "blocks": [
                ("首页推荐", "主推商品、活动 Banner 和分类入口放在最前面。"),
                ("商品列表", "商品卡片直接展示封面、价格、标签和快速加购。"),
                ("购物闭环", "商品详情、购物车、订单状态构成完整购买路径。"),
            ],
            "cards": [
                ("推荐商品", "主图、价格、卖点和活动标签"),
                ("分类浏览", "分类入口、筛选条件和搜索结果"),
                ("购买闭环", "加购、下单、订单状态和售后入口"),
            ],
            "sample_items": sample_items,
            "sample_descriptions": sample_descriptions,
            "confirmation_points": ["一眼看上去是否就是商城", "商品和价格是否够明显", "加购下单路径是否成立"],
            "type_hint": ["ecommerce", "catalog"] + (["pet"] if is_pet else []),
        }

    if identity == "game":
        is_2048 = "2048" in lowered
        title = subject if subject and subject != "这个产品" else ("2048 小游戏" if is_2048 else "网页小游戏")
        return {
            **base,
            "identity_label": "2048 小游戏" if is_2048 else "网页小游戏",
            "target_users": ["想快速开始一局并获得即时反馈的玩家"],
            "core_objects": ["棋盘", "数字块", "分数", "最高分", "重开按钮"] if is_2048 else ["主画布", "分数", "规则", "重开按钮"],
            "core_actions": ["滑动合并", "刷新分数", "重新开始"] if is_2048 else ["开始游戏", "操作角色/对象", "查看分数", "重新开始"],
            "interaction_modes": ["canvas", "realtime_status", "result_overlay"],
            "core_pages": [
                {"key": "game", "name": "游戏主界面", "purpose": "直接进入主玩法"},
                {"key": "rules", "name": "规则弹层", "purpose": "说明玩法和目标"},
                {"key": "result", "name": "结算弹层", "purpose": "展示结果并支持重开"},
            ],
            "key_states": ["准备开始", "进行中", "游戏结束", "重新开始后重置"],
            "constraints": ["第一版先不做联网排行", "先不做多人对战", "先不做复杂关卡系统"],
            "primary_goal": "play",
            "core_loop_text": "进入游戏 -> 进行操作 -> 分数/状态实时变化 -> 失败或胜利后重开",
            "feature_label": "可玩游戏界面",
            "status_label": "可试玩",
            "panel_title": "游戏主画面",
            "primary_surface": "canvas",
            "hero_title": title,
            "hero_subtitle": "打开页面就能看到主玩法、分数反馈和重新开始入口，而不是先看到一页解释文字。",
            "primary_action": "开始游戏",
            "secondary_action": "查看规则",
            "nav_items": ["开始", "规则", "排行", "设置"],
            "blocks": [
                ("主玩法区域", "中央区域直接承载棋盘或游戏画布，用户一眼就知道怎么玩。"),
                ("即时状态", "分数、最高分、剩余步数或时间等信息实时反馈。"),
                ("结束与重开", "失败/达成目标后立刻给出结果和重开入口。"),
            ],
            "cards": [
                ("主界面", "棋盘/画布、主要交互对象和目标提示"),
                ("状态栏", "分数、最高分、时间或关卡进度"),
                ("规则与重开", "玩法说明、结束状态和重新开始"),
            ],
            "sample_items": ["当前得分 1280", "最高分 4096", "继续冲击 2048"] if is_2048 else ["第 1 关", "得分 1280", "剩余 45 秒"],
            "sample_descriptions": [
                "棋盘打开就是当前局面，玩家不用先读长说明，第一眼就知道这是可以马上开始的一局。" if is_2048 else "主玩法区域直接告诉玩家目标、危险区和当前可操作对象，不需要再靠解释页理解玩法。",
                "每一步滑动后分数、最高分和棋盘状态都要立即变化，让操作结果有连续反馈。" if is_2048 else "分数或计时变化要立即可见，让玩家知道刚才那一步到底有没有产生结果。",
                "失败或达成目标后，结算和重新开始入口要在当前视线里，不打断一局游戏的节奏。" if is_2048 else "规则和重开入口必须简单直接，第一次进入也能很快玩起来。",
            ],
            "confirmation_points": ["一眼看上去是否就是游戏", "主玩法是否明确", "状态和重开反馈是否成立"],
            "type_hint": ["game"] + (["2048", "puzzle"] if is_2048 else []),
            "subtype": "2048" if is_2048 else "",
            "game_variant": "2048" if is_2048 else "",
        }

    if identity == "dashboard":
        return {
            **base,
            "identity_label": "管理后台",
            "target_users": ["需要处理业务数据和对象列表的运营或管理员"],
            "core_objects": ["指标", "业务列表", "详情面板", "操作记录"],
            "core_actions": ["查看总览", "筛选列表", "处理详情", "记录操作"],
            "interaction_modes": ["overview", "list", "detail"],
            "core_pages": [
                {"key": "overview", "name": "总览页", "purpose": "展示关键指标与异常提醒"},
                {"key": "list", "name": "业务列表页", "purpose": "筛选和处理对象"},
                {"key": "detail", "name": "详情页", "purpose": "查看明细并执行操作"},
                {"key": "settings", "name": "设置页", "purpose": "管理通知、权限和基础配置"},
            ],
            "key_states": ["列表为空", "加载中", "处理成功", "处理失败"],
            "constraints": ["第一版先不做复杂自动化", "先不做超细权限模型", "先不做高级 BI 分析"],
            "primary_goal": "manage",
            "core_loop_text": "总览看指标 -> 进入列表筛选 -> 打开详情处理 -> 返回继续处理",
            "feature_label": "管理后台界面",
            "status_label": "多模块",
            "panel_title": "后台信息架构",
            "primary_surface": "dashboard",
            "hero_title": subject if subject and subject != "这个产品" else "管理工作台",
            "hero_subtitle": "首屏看到关键指标、待处理事项和业务列表入口，整体像一个真的后台而不是宣传页。",
            "primary_action": "进入总览",
            "secondary_action": "查看列表",
            "nav_items": ["总览", "列表", "详情", "设置"],
            "blocks": [
                ("总览指标", "关键数字、趋势变化和异常提醒放在首屏。"),
                ("业务列表", "对象列表支持筛选、排序、批量处理。"),
                ("详情处理", "详情页承接审核、编辑、备注和状态变更。"),
            ],
            "cards": [
                ("指标总览", "关键数字、趋势和异常提醒"),
                ("业务列表", "筛选、状态、批量动作和分页"),
                ("详情操作", "编辑、审核、回溯和备注"),
            ],
            "sample_items": ["今日新增 128", "待处理 24", "转化率 18.6%"],
            "sample_descriptions": ["核心指标卡片帮助快速判断当前业务状态。", "列表区承接筛选和批量处理。", "详情区支持继续操作和记录历史。"],
            "confirmation_points": ["是否一眼像后台", "操作路径是否高效", "信息密度是否合理"],
            "type_hint": ["dashboard", "admin"],
        }

    if identity == "todo":
        return {
            **base,
            "identity_label": "待办工具",
            "target_users": ["想快速记录、完成和删除任务的人"],
            "core_objects": ["任务", "状态", "截止时间", "归档"],
            "core_actions": ["新增任务", "切换完成状态", "删除任务", "查看归档"],
            "interaction_modes": ["list", "quick_add", "status_toggle"],
            "core_pages": [
                {"key": "today", "name": "今日列表", "purpose": "承接当天任务"},
                {"key": "upcoming", "name": "即将事项", "purpose": "查看后续安排"},
                {"key": "archive", "name": "完成归档", "purpose": "回看已完成任务"},
            ],
            "key_states": ["任务为空", "进行中", "已完成", "删除确认"],
            "constraints": ["第一版先不做复杂协作", "先不做高级统计", "先不做自动化规则"],
            "primary_goal": "organize",
            "core_loop_text": "新增任务 -> 切换状态 -> 完成归档 -> 继续新增",
            "feature_label": "任务清单界面",
            "status_label": "可操作",
            "panel_title": "今日任务",
            "primary_surface": "board",
            "hero_title": "今天要做什么？",
            "hero_subtitle": "把临时想法快速收进任务箱，按优先级处理，完成后自动归档。",
            "primary_action": "添加任务",
            "secondary_action": "查看已完成",
            "nav_items": ["今天", "即将", "已完成", "统计"],
            "blocks": [
                ("任务输入", "输入框、优先级和添加按钮放在同一操作区。"),
                ("今日列表", "展示待办、进行中、已完成，并支持勾选切换。"),
                ("归档管理", "完成项自动进入归档，也可以删除不需要的任务。"),
            ],
            "cards": [
                ("收集箱", "快速新增、优先级和截止时间"),
                ("今日列表", "勾选完成、筛选状态和继续编辑"),
                ("完成归档", "删除、归档、历史记录和统计"),
            ],
            "sample_items": ["写完首页文案", "确认移动端按钮", "整理周五发布清单"],
            "sample_descriptions": ["新任务会进入今天的待办列表，并默认标记为普通优先级。", "点击状态位即可切换完成状态。", "已完成任务保留归档入口，也可以直接删除。"],
            "confirmation_points": ["新增入口是否明显", "状态变化是否清楚", "删除/归档是否符合预期"],
            "type_hint": ["todo", "productivity"],
        }

    if identity == "tool":
        return {
            **base,
            "identity_label": "结果型工具",
            "target_users": ["想快速输入信息并立即得到结果的人"],
            "core_objects": ["输入项", "结果区", "历史记录", "参数配置"],
            "core_actions": ["输入内容", "触发执行", "查看结果", "复制或导出"],
            "interaction_modes": ["form", "result", "history"],
            "core_pages": [
                {"key": "workspace", "name": "输入工作区", "purpose": "填写信息与参数"},
                {"key": "result", "name": "结果区", "purpose": "展示输出结果和状态"},
                {"key": "history", "name": "历史记录", "purpose": "回看最近操作"},
            ],
            "key_states": ["等待输入", "执行中", "执行成功", "执行失败"],
            "constraints": ["第一版先不做复杂权限", "先不做团队协作", "先不做高级工作流自动化"],
            "primary_goal": "create",
            "core_loop_text": "输入需求 -> 点击执行 -> 查看结果 -> 复制/导出或继续修改",
            "feature_label": "工具表单界面",
            "status_label": "有结果区",
            "panel_title": "输入到结果流程",
            "primary_surface": "form",
            "hero_title": subject if subject and subject != "这个产品" else "结果型工具",
            "hero_subtitle": "先收集必要输入，再立即给出结果预览、状态反馈和继续编辑入口。",
            "primary_action": "立即生成",
            "secondary_action": "查看示例",
            "nav_items": ["输入", "结果", "历史", "设置"],
            "blocks": [
                ("输入区域", "收集用户文本、文件、参数或选择项。"),
                ("执行按钮", "明确触发生成、查询、计算或分析动作。"),
                ("结果反馈", "展示结果、状态、错误提示和再次编辑入口。"),
            ],
            "cards": [
                ("输入表单", "字段、上传、参数和校验"),
                ("结果预览", "生成结果、复制、下载或继续编辑"),
                ("历史记录", "最近操作、复用和重新生成"),
            ],
            "sample_items": ["输入需求", "生成结果", "复制/导出"],
            "sample_descriptions": ["左侧收集文本、文件或参数。", "右侧展示即时结果和状态。", "底部保留历史记录和导出入口。"],
            "confirmation_points": ["输入项是否够少且必要", "结果区是否像真实产物", "错误/空状态是否清楚"],
            "type_hint": ["tool", "workflow"],
        }

    landing_data = _build_landing_brief_data(request_text, subject, workspace.name)

    return {
        **base,
        "identity_label": "官网/介绍页",
        "target_users": ["第一次了解这个产品或服务的访客"],
        "core_objects": ["价值主张", "能力说明", "案例证明", "联系入口"],
        "core_actions": ["了解产品", "查看案例", "联系咨询"],
        "interaction_modes": ["hero", "sections", "conversion"],
        "core_pages": [
            {"key": "home", "name": "首页", "purpose": "说明产品价值和核心能力"},
            {"key": "cases", "name": "案例页", "purpose": "展示成果与可信度"},
            {"key": "contact", "name": "联系页", "purpose": "承接咨询和转化"},
        ],
        "key_states": ["首屏加载中", "咨询提交成功", "咨询提交失败"],
        "constraints": ["第一版先不做复杂后台", "先不做会员体系", "先不做高级营销自动化"],
        "primary_goal": "convert",
        "core_loop_text": "进入首页 -> 了解价值与案例 -> 发起咨询或联系",
        "feature_label": "转化落地页",
        "status_label": "转化优先",
        "panel_title": "转化路径",
        "primary_surface": "landing",
        "hero_title": landing_data["title"],
        "hero_subtitle": landing_data["hero_subtitle"],
        "primary_action": "立即了解",
        "secondary_action": "查看案例",
        "nav_items": landing_data["nav_items"],
        "blocks": landing_data["blocks"],
        "cards": landing_data["cards"],
        "sample_items": landing_data["sample_items"],
        "sample_descriptions": landing_data["sample_descriptions"],
        "confirmation_points": ["价值表达是否直接", "转化入口是否明显", "案例和服务是否支撑信任"],
        "type_hint": ["landing", "website"],
        "contact_label": landing_data["contact_label"],
        "case_label": landing_data["case_label"],
        "service_label": landing_data["service_label"],
        "intro_label": landing_data["intro_label"],
    }


def _display_name_for_surface(surface: str, workspace_name: str) -> str:
    cleaned = re.sub(
        r"(开发|制作|做|创建|搭建|实现)?一个?(简单的|轻量的|基础的)?",
        "",
        workspace_name or "",
    )
    cleaned = re.sub(r"(网页|网站|小程序|应用|app|APP)$", "", cleaned.strip())
    lowered = cleaned.lower()
    if surface == "board":
        if any(keyword in lowered for keyword in ("todo", "待办", "任务")):
            return "TaskFlow"
        return cleaned[:16] or "TaskFlow"
    if surface == "feed":
        return cleaned[:16] or workspace_name or "个人博客"
    return cleaned[:16] or workspace_name or "AI App"


def _infer_surface(text: str, target_platform: str) -> str:
    """Map free-form requirements to finite UI surfaces, not business templates."""
    surface_signals = {
        "feed": [
            "博客", "blog", "文章", "随笔", "写作", "内容", "阅读", "归档", "标签", "分类",
            "作品集", "案例集", "教程", "文档", "知识库", "资讯", "动态", "发布",
        ],
        "canvas": [
            "游戏", "小游戏", "game", "play", "玩家", "关卡", "闯关", "分数", "积分",
            "计时", "排行榜", "角色", "地图", "画布", "棋盘", "抽卡", "互动故事",
        ],
        "dashboard": [
            "后台", "管理后台", "dashboard", "管理台", "工作台", "数据看板", "报表",
            "审批", "用户管理", "订单", "库存", "权限", "统计", "运营",
        ],
        "board": [
            "待办", "todo", "to-do", "任务", "任务清单", "任务列表", "看板", "日程",
            "计划", "进度", "项目管理", "协作", "状态流转",
        ],
        "form": [
            "工具", "生成", "查询", "计算", "分析", "转换", "上传", "编辑", "表单",
            "提交", "搜索", "评估", "诊断", "助手", "配置",
        ],
        "landing": [
            "官网", "落地页", "品牌", "服务介绍", "预约", "咨询", "联系", "销售",
            "转化", "获客", "介绍页", "公司", "机构",
        ],
    }

    scores = {surface: _score_keywords(text, keywords) for surface, keywords in surface_signals.items()}
    if target_platform == "dashboard":
        scores["dashboard"] += 2

    # Tie-break by strongest structural signal. This keeps "个人博客网站" as feed
    # instead of landing, and "小游戏官网" as canvas instead of landing.
    priority = ["canvas", "dashboard", "feed", "board", "form", "landing"]
    return max(priority, key=lambda surface: (scores[surface], -priority.index(surface))) if max(scores.values()) > 0 else "landing"


def _surface_copy(surface: str, workspace: Workspace, requirement: str, product: str, ui_mode: str) -> Dict[str, Any]:
    product_hint = _first_sentence(product, "")
    requirement_hint = _first_sentence(requirement, workspace.name)
    app_name = _display_name_for_surface(surface, workspace.name)
    base = {
        "surface": surface,
        "ui_mode": ui_mode,
        "app_name": app_name,
        "hero_title": workspace.name,
        "hero_subtitle": requirement_hint,
        "confirmation_summary": product_hint or requirement_hint,
        "primary_action": "开始体验",
        "secondary_action": "查看详情",
        "nav_items": ["首页", "内容", "功能", "关于"],
        "feature_label": "需求驱动界面",
        "status_label": "待确认",
        "panel_title": "核心界面",
        "blocks": [],
        "cards": [],
        "confirmation_points": ["首屏是否像真实产品", "核心操作是否清楚", "是否符合当前需求"],
        "sample_items": [],
    }

    if surface == "feed":
        return {
            **base,
            "experience_mode": "content",
            "primary_goal": "read",
            "primary_surface": "feed",
            "hero_subtitle": "记录想法、项目和生活片段，按文章、分类与归档持续沉淀个人表达。",
            "primary_action": "阅读最新文章",
            "secondary_action": "浏览归档",
            "nav_items": ["首页", "文章", "分类", "关于"],
            "feature_label": "内容流界面",
            "status_label": "文章流",
            "panel_title": "内容浏览骨架",
            "blocks": [
                ("精选文章", "首屏展示主推文章、摘要、发布时间和阅读入口。"),
                ("分类与标签", "按主题组织内容，帮助读者快速找到感兴趣的方向。"),
                ("作者信息", "展示个人简介、联系方式和订阅入口，强化个人表达。"),
            ],
            "cards": [
                ("最新文章", "文章标题、摘要、发布日期和阅读按钮"),
                ("分类归档", "标签、分类、时间线和搜索入口"),
                ("作者资料", "头像、简介、社交链接和订阅入口"),
            ],
            "confirmation_points": ["是否已经像内容站/博客", "文章浏览路径是否清楚", "个人表达是否足够明显"],
            "sample_items": ["我为什么开始记录这个项目", "最近的产品思考与实践", "关于我和这个站点"],
            "sample_descriptions": ["一篇置顶文章摘要，介绍博客主题、写作动机和近期更新方向。", "记录产品、技术、设计和个人成长中的具体观察。", "个人介绍、联系方式和长期写作计划。"],
        }

    if surface == "canvas":
        return {
            **base,
            "experience_mode": "immersive",
            "primary_goal": "play",
            "primary_surface": "canvas",
            "hero_subtitle": "进入一局轻量小游戏，看到目标、规则、分数和重新开始入口。",
            "primary_action": "开始游戏",
            "secondary_action": "查看规则",
            "nav_items": ["开始", "规则", "排行", "设置"],
            "feature_label": "沉浸式画布",
            "status_label": "可试玩",
            "panel_title": "游戏主画面",
            "blocks": [
                ("游戏画布", "中央区域承载角色、目标、障碍或互动元素。"),
                ("HUD 状态", "展示分数、生命、计时、关卡等即时反馈。"),
                ("开始与重玩", "保留开始、暂停、重新开始和规则说明入口。"),
            ],
            "cards": [
                ("主画布", "游戏场景、互动区域和视觉目标"),
                ("状态栏", "分数、时间、生命值或关卡进度"),
                ("规则与操作", "键盘/触控提示、开始和重玩按钮"),
            ],
            "confirmation_points": ["是否像一个可玩的界面", "目标和规则是否一眼可懂", "状态反馈是否足够明显"],
            "sample_items": ["第 1 关", "得分 1280", "剩余 45 秒"],
            "sample_descriptions": ["移动角色避开障碍并收集奖励。", "分数随操作实时变化。", "倒计时结束后展示结算和重玩入口。"],
        }

    if surface == "dashboard":
        return {
            **base,
            "experience_mode": "management",
            "primary_goal": "manage",
            "primary_surface": "dashboard",
            "hero_title": "管理工作台",
            "hero_subtitle": "集中呈现关键指标、待处理事项、对象列表和快速操作入口。",
            "primary_action": "进入总览",
            "secondary_action": "查看列表",
            "nav_items": ["总览", "列表", "详情", "设置"],
            "feature_label": "管理后台界面",
            "status_label": "多模块",
            "panel_title": "后台信息架构",
            "blocks": [
                ("总览指标", "把关键数据、异常提醒和趋势变化放在首屏。"),
                ("对象列表", "围绕用户、订单、内容或任务做筛选、排序和批量操作。"),
                ("详情处理", "承接审核、编辑、状态变更和操作记录。"),
            ],
            "cards": [
                ("指标总览", "关键数字、趋势和异常提醒"),
                ("业务列表", "筛选、状态、批量动作和分页"),
                ("详情操作", "编辑、审核、回溯和备注"),
            ],
            "confirmation_points": ["信息密度是否合理", "操作路径是否高效", "总览和列表是否像后台"],
            "sample_items": ["今日新增 128", "待处理 24", "转化率 18.6%"],
            "sample_descriptions": ["核心指标卡片用于判断业务状态。", "待处理列表承接审核和分配。", "趋势变化帮助发现异常。"],
        }

    if surface == "board":
        return {
            **base,
            "experience_mode": "action",
            "primary_goal": "organize",
            "primary_surface": "board",
            "hero_title": "今天要做什么？",
            "hero_subtitle": "把临时想法快速收进任务箱，按优先级处理，完成后自动归档。",
            "primary_action": "添加任务",
            "secondary_action": "查看已完成",
            "nav_items": ["今天", "即将", "已完成", "统计"],
            "feature_label": "任务清单界面",
            "status_label": "可操作",
            "panel_title": "今日任务",
            "blocks": [
                ("任务输入", "输入框、优先级和添加按钮放在同一操作区。"),
                ("今日列表", "展示待办、进行中、已完成，并支持勾选切换。"),
                ("归档管理", "完成项自动进入归档，也可以删除不需要的任务。"),
            ],
            "cards": [
                ("收集箱", "快速新增、优先级和截止时间"),
                ("今日列表", "勾选完成、筛选状态和继续编辑"),
                ("完成归档", "删除、归档、历史记录和统计"),
            ],
            "confirmation_points": ["新增入口是否明显", "状态变化是否清楚", "删除/归档是否符合预期"],
            "sample_items": ["写完首页文案", "确认移动端按钮", "整理周五发布清单"],
            "sample_descriptions": ["新任务会进入今天的待办列表，并默认标记为普通优先级。", "点击圆形状态位即可切换完成状态。", "已完成任务保留归档入口，也可以直接删除。"],
        }

    if surface == "form":
        return {
            **base,
            "experience_mode": "action",
            "primary_goal": "create",
            "primary_surface": "form",
            "hero_subtitle": "输入必要信息，快速生成、查询或分析结果，并支持继续编辑。",
            "primary_action": "立即生成",
            "secondary_action": "查看示例",
            "nav_items": ["输入", "结果", "历史", "设置"],
            "feature_label": "工具表单界面",
            "status_label": "有结果区",
            "panel_title": "输入到结果流程",
            "blocks": [
                ("输入区域", "收集用户文本、文件、参数或选择项。"),
                ("执行按钮", "明确触发生成、查询、计算或分析动作。"),
                ("结果反馈", "展示结果、状态、错误提示和再次编辑入口。"),
            ],
            "cards": [
                ("输入表单", "字段、上传、参数和校验"),
                ("结果预览", "生成结果、复制、下载或继续编辑"),
                ("历史记录", "最近操作、复用和重新生成"),
            ],
            "confirmation_points": ["输入项是否够少且必要", "结果区是否像真实产物", "错误/空状态是否清楚"],
            "sample_items": ["输入需求", "生成结果", "复制/导出"],
            "sample_descriptions": ["左侧收集文本、文件或参数。", "右侧展示即时结果和状态。", "底部保留历史记录和导出入口。"],
        }

    return {
        **base,
        "experience_mode": "conversion",
        "primary_goal": "convert",
        "primary_surface": "landing",
        "hero_subtitle": "用价值主张、服务说明、案例证明和联系入口完成第一版转化路径。",
        "primary_action": "立即了解",
        "secondary_action": "查看案例",
        "nav_items": ["首页", "服务", "案例", "联系"],
        "feature_label": "转化落地页",
        "status_label": "转化优先",
        "panel_title": "转化路径",
        "blocks": [
            ("价值主张", "首屏回答你是谁、能解决什么问题、为什么可信。"),
            ("服务与案例", "用能力说明、流程和案例支撑用户判断。"),
            ("联系入口", "把咨询、预约或留资入口放在明显位置。"),
        ],
        "cards": [
            ("首屏转化区", "标题、副标题、CTA 和信任背书"),
            ("服务说明", "能力、流程、适合人群和价格线索"),
            ("案例与联系", "案例、评价、表单和联系方式"),
        ],
        "confirmation_points": ["价值表达是否直接", "转化入口是否明显", "案例和服务是否支撑信任"],
        "sample_items": ["核心服务", "代表案例", "预约咨询"],
        "sample_descriptions": ["首屏说明解决什么问题。", "案例区支撑可信度。", "表单或联系方式承接转化。"],
    }


def _infer_product_shape(workspace: Workspace, stages: List[WorkspaceStage]) -> Dict[str, Any]:
    brief = _build_design_brief(workspace, stages)
    surface = str(brief.get("primary_surface") or "landing")
    surface_map = {
        "feed": "feed",
        "canvas": "canvas",
        "dashboard": "dashboard",
        "board": "board",
        "form": "form",
        "catalog": "landing",
        "landing": "landing",
    }
    shape = surface_map.get(surface, "landing")
    brief["shape"] = shape
    return brief


def _html_surface_preview(shape: Dict[str, Any]) -> str:
    surface = shape["primary_surface"]
    samples = shape.get("sample_items") or ["示例一", "示例二", "示例三"]
    descriptions = shape.get("sample_descriptions") or [block[1] for block in shape["blocks"]]
    blocks = shape["blocks"]

    def sample(index: int) -> str:
        return _safe_text(str(samples[index] if index < len(samples) else samples[0]))

    def description(index: int) -> str:
        return _safe_text(str(descriptions[index] if index < len(descriptions) else descriptions[0]))

    if surface == "feed":
        return f"""
            <article class="preview-tile preview-tile-large">
              <span class="meta">置顶文章 · 6 min read</span>
              <strong>{sample(0)}</strong>
              <p>{description(0)}</p>
            </article>
            <div class="preview-stack">
              <article class="preview-tile"><span class="meta">最新</span><strong>{sample(1)}</strong><p>{description(1)}</p></article>
              <article class="preview-tile"><span class="meta">关于</span><strong>{sample(2)}</strong><p>{description(2)}</p></article>
            </div>
        """
    if surface == "canvas":
        return f"""
            <div class="game-hud"><span>{sample(0)}</span><span>{sample(1)}</span><span>{sample(2)}</span></div>
            <div class="game-stage"><span class="player"></span><span class="target"></span><span class="bonus"></span></div>
            <button class="mini-cta">{_safe_text(shape["primary_action"])}</button>
        """
    if surface == "dashboard":
        return "".join(
            f'<div class="preview-tile"><span class="meta">{_safe_text(blocks[index][0])}</span><strong>{sample(index)}</strong><p>{description(index)}</p></div>'
            for index in range(3)
        )
    if surface == "catalog":
        return f"""
            <div class="catalog-strip">
              <article class="preview-tile preview-tile-large"><span class="meta">主推商品</span><strong>{sample(0)}</strong><p>{description(0)}</p></article>
              <article class="preview-tile"><span class="meta">分类浏览</span><strong>{sample(1)}</strong><p>{description(1)}</p></article>
              <article class="preview-tile"><span class="meta">购买闭环</span><strong>{sample(2)}</strong><p>{description(2)}</p></article>
            </div>
        """
    if surface == "board":
        return f"""
            <div class="task-app">
              <div class="task-input">
                <span>今天要完成什么？</span>
                <button>添加</button>
              </div>
              <div class="task-tabs"><span class="active">全部 6</span><span>进行中 3</span><span>已完成 2</span></div>
              <div class="task-card high"><i></i><div><strong>{sample(0)}</strong><p>今天 18:00 · 高优先级</p></div><button>删除</button></div>
              <div class="task-card"><i></i><div><strong>{sample(1)}</strong><p>明天上午 · 进行中</p></div><button>删除</button></div>
              <div class="task-card done"><i></i><div><strong>{sample(2)}</strong><p>已完成 · 自动归档</p></div><button>删除</button></div>
            </div>
        """
    if surface == "form":
        return f"""
            <div class="preview-tile input-tile"><span class="meta">{sample(0)}</span><strong>输入区</strong><p>{description(0)}</p></div>
            <div class="preview-tile result-tile"><span class="meta">{sample(1)}</span><strong>结果预览</strong><p>{description(1)}</p></div>
            <div class="preview-tile"><span class="meta">{sample(2)}</span><strong>历史与导出</strong><p>{description(2)}</p></div>
        """
    return f"""
            <div class="preview-tile preview-tile-large"><span class="meta">{_safe_text(blocks[0][0])}</span><strong>{sample(0)}</strong><p>{description(0)}</p></div>
            <div class="preview-stack">
              <article class="preview-tile"><span class="meta">{_safe_text(blocks[1][0])}</span><strong>{sample(1)}</strong><p>{description(1)}</p></article>
              <article class="preview-tile"><span class="meta">{_safe_text(blocks[2][0])}</span><strong>{sample(2)}</strong><p>{description(2)}</p></article>
            </div>
        """


def _html_detail_cards(shape: Dict[str, Any]) -> str:
    samples = shape.get("sample_items") or ["示例一", "示例二", "示例三"]
    descriptions = shape.get("sample_descriptions") or [block[1] for block in shape["blocks"]]
    cards = shape["cards"]

    def sample(index: int) -> str:
        return _safe_text(str(samples[index] if index < len(samples) else samples[0]))

    def description(index: int) -> str:
        return _safe_text(str(descriptions[index] if index < len(descriptions) else descriptions[0]))

    return "".join(
        f"""
        <article class="card">
          <div class="card-meta">{_safe_text(cards[index][0])}</div>
          <h2>{sample(index)}</h2>
          <p>{description(index)}</p>
          <div class="card-foot">{_safe_text(cards[index][1])}</div>
        </article>
        """
        for index in range(3)
    )


def _html_interaction_playground(shape: Dict[str, Any]) -> str:
    identity = str(shape.get("identity") or "")
    samples = shape.get("sample_items") or ["示例一", "示例二", "示例三"]
    descriptions = shape.get("sample_descriptions") or ["示例描述", "示例描述", "示例描述"]

    if identity == "blog":
        posts = [
            ("产品", str(samples[0]), str(descriptions[0])),
            ("写作", str(samples[1]), str(descriptions[1])),
            ("关于", str(samples[2]), str(descriptions[2])),
        ]
        post_html = "".join(
            f'<button class="mock-item{" active" if index == 0 else ""}" data-post-card data-category="{html_lib.escape(category)}" data-title="{html_lib.escape(title)}" data-body="{html_lib.escape(body)}"><span class="mock-kicker">{html_lib.escape(category)}</span><strong>{html_lib.escape(title)}</strong><p>{html_lib.escape(body)}</p></button>'
            for index, (category, title, body) in enumerate(posts)
        )
        return f"""
      <section class="experience" id="interaction-playground">
        <div class="experience-head">
          <div>
            <div class="eyebrow">可操作原型</div>
            <h2>文章列表与阅读区</h2>
          </div>
          <div class="sample-row">
            <button class="filter active" type="button" data-post-filter="全部">全部</button>
            <button class="filter" type="button" data-post-filter="产品">产品</button>
            <button class="filter" type="button" data-post-filter="写作">写作</button>
            <button class="filter" type="button" data-post-filter="关于">关于</button>
          </div>
        </div>
        <div class="experience-grid">
          <div class="mock-list">
            {post_html}
          </div>
          <article class="mock-reader">
            <span class="mock-kicker" id="readerCategory">产品</span>
            <h3 id="readerTitle">{_safe_text(str(samples[0]))}</h3>
            <p id="readerBody">{_safe_text(str(descriptions[0]))}</p>
            <div class="reader-meta">
              <span id="readerDate">2026-04-30</span>
              <span id="readerReadTime">6 分钟阅读</span>
              <span id="readerTag"># 长文</span>
            </div>
            <div class="reader-actions">
              <button class="primary" type="button" id="readerLike">继续阅读</button>
              <button class="secondary" type="button" id="readerArchive">加入归档</button>
            </div>
            <div class="reader-note" id="readerNote">你可以切换左侧文章，确认列表、阅读区和个人表达方式是否对路。</div>
            <div class="reader-outline">
              <strong>文章目录</strong>
              <div class="outline-list">
                <span>01. 为什么开始写</span>
                <span>02. 这周具体更新了什么</span>
                <span>03. 下一篇准备写什么</span>
              </div>
            </div>
          </article>
        </div>
      </section>
        """

    if identity == "ecommerce":
        products = [
            (str(samples[0]), "¥129", "主粮 / 高蛋白"),
            (str(samples[1]), "¥79", "新手入门 / 套装"),
            (str(samples[2]), "¥39", "零食 / 补充装"),
        ]
        cards = "".join(
            f'<article class="product-card" data-product-card><div class="product-media"></div><span class="product-chip">{html_lib.escape(tag)}</span><strong>{html_lib.escape(title)}</strong><p>{price}</p><button class="primary" type="button" data-add-cart>加入购物车</button></article>'
            for title, price, tag in products
        )
        return f"""
      <section class="experience" id="interaction-playground">
        <div class="experience-head">
          <div>
            <div class="eyebrow">可操作原型</div>
            <h2>商品浏览与加购</h2>
          </div>
          <div class="cart-badge">购物车 <span id="cartCount">0</span></div>
        </div>
        <div class="product-grid">{cards}</div>
        <div class="reader-note" id="cartNote">点击商品卡按钮，检查加购反馈、购物车数量和商品焦点是否明确。</div>
      </section>
        """

    if identity == "game":
        return """
      <section class="experience" id="interaction-playground">
        <div class="experience-head">
          <div>
            <div class="eyebrow">可操作原型</div>
            <h2>单局试玩反馈</h2>
          </div>
          <div class="cart-badge">得分 <span id="gameScore">0</span></div>
        </div>
        <div class="game-shell">
          <div class="mini-board" id="miniBoard">
            <span>2</span><span>4</span><span>8</span><span>16</span>
            <span>0</span><span>2</span><span>4</span><span>8</span>
            <span>0</span><span>0</span><span>2</span><span>4</span>
            <span>0</span><span>0</span><span>0</span><span>2</span>
          </div>
          <div class="game-status">
            <div><strong>目标</strong><span>合成到 2048</span></div>
            <div><strong>最佳连击</strong><span id="comboValue">0</span></div>
            <div><strong>状态</strong><span id="gameStateText">准备开始</span></div>
          </div>
          <div class="reader-actions">
            <button class="primary" type="button" id="gameMove">模拟一步</button>
            <button class="secondary" type="button" id="gameReset">重新开始</button>
          </div>
          <div class="reader-note" id="gameNote">先看每一步是否有即时反馈，再决定是否继续丰富动效和规则表现。</div>
        </div>
      </section>
        """

    if identity == "landing":
        cards = [
            (str(shape.get("intro_label") or "公司介绍"), str(samples[0]), str(descriptions[0])),
            (str(shape.get("service_label") or "服务内容"), str(samples[1]), str(descriptions[1])),
            (str(shape.get("case_label") or "案例展示"), str(samples[2]), str(descriptions[2])),
        ]
        card_html = "".join(
            f'<button class="mock-item{" active" if index == 0 else ""}" data-landing-card data-section="{html_lib.escape(section)}" data-title="{html_lib.escape(title)}" data-body="{html_lib.escape(body)}"><span class="mock-kicker">{html_lib.escape(section)}</span><strong>{html_lib.escape(title)}</strong><p>{html_lib.escape(body)}</p></button>'
            for index, (section, title, body) in enumerate(cards)
        )
        return f"""
      <section class="experience" id="interaction-playground">
        <div class="experience-head">
          <div>
            <div class="eyebrow">可操作原型</div>
            <h2>官网首页内容结构</h2>
          </div>
          <div class="sample-row">
            <button class="filter active" type="button" data-landing-filter="全部">全部</button>
            <button class="filter" type="button" data-landing-filter="{_safe_text(str(shape.get("intro_label") or "公司介绍"))}">{_safe_text(str(shape.get("intro_label") or "公司介绍"))}</button>
            <button class="filter" type="button" data-landing-filter="{_safe_text(str(shape.get("service_label") or "服务内容"))}">{_safe_text(str(shape.get("service_label") or "服务内容"))}</button>
            <button class="filter" type="button" data-landing-filter="{_safe_text(str(shape.get("case_label") or "案例展示"))}">{_safe_text(str(shape.get("case_label") or "案例展示"))}</button>
          </div>
        </div>
        <div class="experience-grid">
          <div class="mock-list">
            {card_html}
          </div>
          <article class="mock-reader">
            <span class="mock-kicker" id="landingSection">{_safe_text(str(shape.get("intro_label") or "公司介绍"))}</span>
            <h3 id="landingTitle">{_safe_text(str(samples[0]))}</h3>
            <p id="landingBody">{_safe_text(str(descriptions[0]))}</p>
            <div class="reader-meta">
              <span id="landingTrust">10+ 行业项目</span>
              <span id="landingSpeed">24 小时内响应</span>
              <span id="landingType">官网首页原型</span>
            </div>
            <div class="reader-actions">
              <button class="primary" type="button" id="landingConsult">立即咨询</button>
              <button class="secondary" type="button" id="landingCases">查看案例</button>
            </div>
            <div class="reader-note" id="landingNote">切换左侧模块，确认这个官网是不是已经具备“公司介绍、服务、案例、联系”这条完整转化路径。</div>
            <div class="reader-outline">
              <strong>首页模块顺序</strong>
              <div class="outline-list">
                <span>01. 首屏价值与公司定位</span>
                <span>02. 服务模块与交付方式</span>
                <span>03. 案例证明与联系入口</span>
              </div>
            </div>
          </article>
        </div>
      </section>
        """

    return ""


def _html_interaction_script(shape: Dict[str, Any]) -> str:
    identity = str(shape.get("identity") or "")
    if identity == "blog":
        return """
  <script>
    const primary = document.getElementById("primaryAction");
    const secondary = document.getElementById("secondaryAction");
    const playground = document.getElementById("interaction-playground");
    primary?.addEventListener("click", () => playground?.scrollIntoView({ behavior: "smooth", block: "start" }));
    secondary?.addEventListener("click", () => {
      document.querySelector('[data-post-filter="写作"]')?.click();
      playground?.scrollIntoView({ behavior: "smooth", block: "start" });
    });

    const cards = Array.from(document.querySelectorAll("[data-post-card]"));
    const filters = Array.from(document.querySelectorAll("[data-post-filter]"));
    const titleEl = document.getElementById("readerTitle");
    const bodyEl = document.getElementById("readerBody");
    const categoryEl = document.getElementById("readerCategory");
    const noteEl = document.getElementById("readerNote");
    const dateEl = document.getElementById("readerDate");
    const readTimeEl = document.getElementById("readerReadTime");
    const tagEl = document.getElementById("readerTag");
    const metaByCategory = {
      "产品": ["2026-04-30", "6 分钟阅读", "# 产品观察"],
      "写作": ["2026-04-27", "4 分钟阅读", "# 周记"],
      "关于": ["2026-04-18", "3 分钟阅读", "# 作者"],
    };

    const activateCard = (card) => {
      cards.forEach((item) => item.classList.remove("active"));
      card.classList.add("active");
      titleEl.textContent = card.dataset.title || "";
      bodyEl.textContent = card.dataset.body || "";
      categoryEl.textContent = card.dataset.category || "";
      const [dateText, readText, tagText] = metaByCategory[card.dataset.category] || ["2026-04-30", "5 分钟阅读", "# 文章"];
      dateEl.textContent = dateText;
      readTimeEl.textContent = readText;
      tagEl.textContent = tagText;
      noteEl.textContent = "当前正在预览这篇文章的阅读区状态。继续切换不同文章，确认首页列表和详情阅读感是不是你想要的博客气质。";
    };

    cards.forEach((card) => {
      card.addEventListener("click", () => activateCard(card));
    });

    filters.forEach((filter) => {
      filter.addEventListener("click", () => {
        filters.forEach((item) => item.classList.remove("active"));
        filter.classList.add("active");
        const target = filter.dataset.postFilter;
        cards.forEach((card) => {
          card.style.display = target === "全部" || card.dataset.category === target ? "grid" : "none";
        });
        const visible = cards.find((card) => card.style.display !== "none");
        if (visible) activateCard(visible);
      });
    });
  </script>
        """
    if identity == "ecommerce":
        return """
  <script>
    let cart = 0;
    const cartCount = document.getElementById("cartCount");
    const primary = document.getElementById("primaryAction");
    const secondary = document.getElementById("secondaryAction");
    const playground = document.getElementById("interaction-playground");
    const cards = Array.from(document.querySelectorAll("[data-product-card]"));
    const buttons = Array.from(document.querySelectorAll("[data-add-cart]"));
    const cartNote = document.getElementById("cartNote");

    primary?.addEventListener("click", () => playground?.scrollIntoView({ behavior: "smooth", block: "start" }));
    secondary?.addEventListener("click", () => buttons[0]?.click());

    cards.forEach((card, index) => {
      card.addEventListener("click", () => {
        cards.forEach((item) => item.classList.remove("active"));
        card.classList.add("active");
        cartNote.textContent = `正在查看第 ${index + 1} 个商品卡。这里主要确认商品封面、价格、标签和加购按钮是否已经形成购买氛围。`;
      });
    });

    buttons.forEach((button, index) => {
      button.addEventListener("click", () => {
        cart += 1;
        cartCount.textContent = String(cart);
        button.textContent = "已加入";
        cards[index]?.classList.add("active");
        cartNote.textContent = `已加入 1 件商品，购物车数量变为 ${cart}。确认这种加购反馈是否足够直接。`;
      });
    });
  </script>
        """
    if identity == "game":
        return """
  <script>
    let score = 0;
    let combo = 0;
    const scoreEl = document.getElementById("gameScore");
    const comboEl = document.getElementById("comboValue");
    const stateEl = document.getElementById("gameStateText");
    const noteEl = document.getElementById("gameNote");
    const primary = document.getElementById("primaryAction");
    const secondary = document.getElementById("secondaryAction");
    const playground = document.getElementById("interaction-playground");
    const cells = Array.from(document.querySelectorAll("#miniBoard span"));
    primary?.addEventListener("click", () => playground?.scrollIntoView({ behavior: "smooth", block: "start" }));
    secondary?.addEventListener("click", () => {
      stateEl.textContent = "查看规则中";
      noteEl.textContent = "当前重点是确认你一进来就知道目标是什么、如何开始、如何重开。";
      playground?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
    document.getElementById("gameMove")?.addEventListener("click", () => {
      score += 16;
      combo += 1;
      scoreEl.textContent = String(score);
      comboEl.textContent = String(combo);
      stateEl.textContent = "进行中";
      noteEl.textContent = "这一步模拟了分数刷新和棋盘变化。下一步如果继续做，会补动画、失败态和更完整的规则反馈。";
      const last = cells[cells.length - 1];
      if (last) last.textContent = String(Number(last.textContent || "0") + 2);
    });
    document.getElementById("gameReset")?.addEventListener("click", () => {
      score = 0;
      combo = 0;
      scoreEl.textContent = "0";
      comboEl.textContent = "0";
      stateEl.textContent = "准备开始";
      noteEl.textContent = "棋盘和分数已重置。这里主要确认重新开始入口是否足够直接。";
      const seed = ["2","4","8","16","0","2","4","8","0","0","2","4","0","0","0","2"];
      cells.forEach((cell, index) => { cell.textContent = seed[index] || "0"; });
    });
  </script>
        """
    if identity == "landing":
        return """
  <script>
    const primary = document.getElementById("primaryAction");
    const secondary = document.getElementById("secondaryAction");
    const playground = document.getElementById("interaction-playground");
    const cards = Array.from(document.querySelectorAll("[data-landing-card]"));
    const filters = Array.from(document.querySelectorAll("[data-landing-filter]"));
    const sectionEl = document.getElementById("landingSection");
    const titleEl = document.getElementById("landingTitle");
    const bodyEl = document.getElementById("landingBody");
    const noteEl = document.getElementById("landingNote");
    const trustEl = document.getElementById("landingTrust");
    const speedEl = document.getElementById("landingSpeed");

    const metaBySection = {
      "公司介绍": ["10+ 行业项目", "24 小时内响应"],
      "品牌定位": ["品牌官网首页", "1 屏讲清定位"],
      "服务内容": ["模块化服务", "可继续展开详情页"],
      "核心能力": ["能力拆分清楚", "支持后续扩页"],
      "案例展示": ["真实项目结果", "支持继续展开案例页"],
      "代表案例": ["案例承接信任", "适合放客户结果"],
    };

    const activateCard = (card) => {
      cards.forEach((item) => item.classList.remove("active"));
      card.classList.add("active");
      sectionEl.textContent = card.dataset.section || "";
      titleEl.textContent = card.dataset.title || "";
      bodyEl.textContent = card.dataset.body || "";
      const [trustText, speedText] = metaBySection[card.dataset.section] || ["官网模块预览", "支持继续细化"];
      trustEl.textContent = trustText;
      speedEl.textContent = speedText;
      noteEl.textContent = "当前正在预览官网首页的这个模块。继续切换模块，确认页面信息顺序、服务表达和案例承接是不是对路。";
    };

    primary?.addEventListener("click", () => playground?.scrollIntoView({ behavior: "smooth", block: "start" }));
    secondary?.addEventListener("click", () => {
      document.querySelector('[data-landing-filter="案例展示"], [data-landing-filter="代表案例"]')?.click();
      playground?.scrollIntoView({ behavior: "smooth", block: "start" });
    });

    cards.forEach((card) => {
      card.addEventListener("click", () => activateCard(card));
    });

    filters.forEach((filter) => {
      filter.addEventListener("click", () => {
        filters.forEach((item) => item.classList.remove("active"));
        filter.classList.add("active");
        const target = filter.dataset.landingFilter;
        cards.forEach((card) => {
          card.style.display = target === "全部" || card.dataset.section === target ? "grid" : "none";
        });
        const visible = cards.find((card) => card.style.display !== "none");
        if (visible) activateCard(visible);
      });
    });

    document.getElementById("landingConsult")?.addEventListener("click", () => {
      noteEl.textContent = "这里后续可以接联系表单、电话、微信或预约弹层。当前原型重点是确认官网首页是否已经形成转化路径。";
    });

    document.getElementById("landingCases")?.addEventListener("click", () => {
      document.querySelector('[data-landing-filter="案例展示"], [data-landing-filter="代表案例"]')?.click();
    });
  </script>
        """
    return ""


def _build_todo_prototype_html(workspace: Workspace, requirement: str) -> str:
    safe_name = _safe_text(workspace.name)
    safe_requirement = _safe_text(requirement, "轻量待办应用，用来快速记录、完成和删除任务。")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{safe_name} - Todo Prototype</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f4f7fb;
      --surface: rgba(255,255,255,.82);
      --surface-strong: #ffffff;
      --line: #dbe4f0;
      --ink: #162033;
      --muted: #667085;
      --accent: #2563eb;
      --accent-soft: #dbeafe;
      --success: #16a34a;
      --danger: #dc2626;
      --shadow: 0 22px 60px rgba(37, 99, 235, .12);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background:
        radial-gradient(circle at top left, rgba(37,99,235,.14), transparent 28%),
        radial-gradient(circle at bottom right, rgba(14,165,233,.12), transparent 24%),
        var(--bg);
      color: var(--ink);
    }}
    .shell {{ max-width: 1180px; margin: 0 auto; padding: 28px 24px 56px; }}
    .topbar {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 16px;
      margin-bottom: 28px;
      padding: 14px 18px;
      border: 1px solid rgba(255,255,255,.6);
      background: rgba(255,255,255,.72);
      backdrop-filter: blur(18px);
      border-radius: 18px;
      box-shadow: 0 12px 30px rgba(15, 23, 42, .06);
    }}
    .brand {{ display: flex; align-items: center; gap: 12px; font-weight: 760; }}
    .logo {{
      width: 40px;
      height: 40px;
      border-radius: 14px;
      background: linear-gradient(135deg, #2563eb, #38bdf8);
      color: #fff;
      display: grid;
      place-items: center;
      box-shadow: 0 10px 24px rgba(37, 99, 235, .28);
    }}
    .badge {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 8px 12px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 700;
      color: var(--accent);
      background: var(--accent-soft);
    }}
    .hero {{
      display: grid;
      grid-template-columns: minmax(0, 1.05fr) minmax(380px, .95fr);
      gap: 24px;
      align-items: start;
      margin-bottom: 24px;
    }}
    .hero-card, .board, .aside-card {{
      border: 1px solid rgba(255,255,255,.72);
      border-radius: 28px;
      background: var(--surface);
      backdrop-filter: blur(16px);
      box-shadow: var(--shadow);
    }}
    .hero-card {{ padding: 34px; }}
    h1 {{
      margin: 16px 0 14px;
      font-size: clamp(40px, 5vw, 64px);
      line-height: .96;
      letter-spacing: -0.04em;
    }}
    .lead {{
      max-width: 640px;
      color: var(--muted);
      font-size: 17px;
      line-height: 1.75;
      margin: 0 0 24px;
    }}
    .hero-actions {{ display: flex; gap: 12px; flex-wrap: wrap; }}
    .hero-actions button {{
      border: 0;
      border-radius: 14px;
      padding: 13px 18px;
      font-weight: 760;
      font-size: 14px;
    }}
    .primary {{ background: var(--accent); color: #fff; }}
    .secondary {{ background: var(--surface-strong); color: var(--ink); border: 1px solid var(--line); }}
    .board {{
      padding: 24px;
      display: grid;
      gap: 18px;
    }}
    .board-head {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
    }}
    .board-head strong {{ font-size: 18px; }}
    .stats {{ display: flex; gap: 10px; flex-wrap: wrap; }}
    .pill {{
      padding: 7px 12px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 700;
      background: #eff6ff;
      color: var(--accent);
    }}
    .composer {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) 140px;
      gap: 12px;
    }}
    .composer input {{
      height: 52px;
      border-radius: 16px;
      border: 1px solid var(--line);
      padding: 0 16px;
      background: var(--surface-strong);
      color: var(--ink);
      font-size: 15px;
    }}
    .composer button {{
      height: 52px;
      border: 0;
      border-radius: 16px;
      background: linear-gradient(135deg, #2563eb, #1d4ed8);
      color: #fff;
      font-weight: 760;
      font-size: 14px;
    }}
    .task-list {{ display: grid; gap: 12px; }}
    .task {{
      display: grid;
      grid-template-columns: auto minmax(0, 1fr) auto;
      align-items: center;
      gap: 14px;
      padding: 16px;
      border-radius: 18px;
      border: 1px solid var(--line);
      background: var(--surface-strong);
    }}
    .check {{
      width: 22px;
      height: 22px;
      border-radius: 999px;
      border: 2px solid #bfdbfe;
      background: #fff;
    }}
    .task.done .check {{ background: var(--success); border-color: var(--success); }}
    .task-title {{ font-weight: 700; margin-bottom: 4px; }}
    .task.done .task-title {{ text-decoration: line-through; color: #94a3b8; }}
    .task-meta {{ font-size: 12px; color: var(--muted); }}
    .delete {{
      border: 0;
      background: #fee2e2;
      color: var(--danger);
      border-radius: 12px;
      padding: 9px 12px;
      font-weight: 700;
      font-size: 12px;
    }}
    .bottom {{
      display: grid;
      grid-template-columns: 1.1fr .9fr;
      gap: 24px;
    }}
    .aside-card {{ padding: 24px; }}
    .aside-card h2 {{ margin: 0 0 14px; font-size: 18px; }}
    .tips {{ display: grid; gap: 12px; }}
    .tip {{
      padding: 14px 16px;
      border-radius: 16px;
      background: var(--surface-strong);
      border: 1px solid var(--line);
    }}
    .tip strong {{ display: block; margin-bottom: 6px; font-size: 14px; }}
    .tip span {{ font-size: 13px; color: var(--muted); line-height: 1.6; }}
    .filters {{ display: flex; gap: 10px; flex-wrap: wrap; margin-top: 18px; }}
    .filter {{
      padding: 10px 14px;
      border-radius: 999px;
      font-size: 13px;
      font-weight: 700;
      border: 1px solid var(--line);
      background: var(--surface-strong);
      color: var(--muted);
    }}
    .filter.active {{ color: var(--accent); background: var(--accent-soft); border-color: transparent; }}
    @media (max-width: 900px) {{
      .hero, .bottom {{ grid-template-columns: 1fr; }}
      .composer {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <div class="topbar">
      <div class="brand">
        <div class="logo">AI</div>
        <div>
          <div style="font-size:14px;color:#667085;font-weight:600;">待办应用原型</div>
          <div>{safe_name}</div>
        </div>
      </div>
      <div class="badge">MVP 原型 · 添加 / 完成 / 删除</div>
    </div>

    <section class="hero">
      <div class="hero-card">
        <div class="badge">真实待办界面，而不是方案说明页</div>
        <h1>我的待办，<br/>今天就清掉。</h1>
        <p class="lead">{safe_requirement}</p>
        <div class="hero-actions">
          <button class="primary">开始添加任务</button>
          <button class="secondary">查看今日进度</button>
        </div>
      </div>

      <div class="board">
        <div class="board-head">
          <strong>任务列表</strong>
          <div class="stats">
            <span class="pill">共 3 项</span>
            <span class="pill">已完成 1 项</span>
          </div>
        </div>
        <div class="composer">
          <input value="明天会议前整理本周待办" />
          <button>添加任务</button>
        </div>
        <div class="task-list">
          <div class="task">
            <div class="check"></div>
            <div>
              <div class="task-title">补充首页欢迎文案</div>
              <div class="task-meta">未完成 · 今天 18:00 前</div>
            </div>
            <button class="delete">删除</button>
          </div>
          <div class="task done">
            <div class="check"></div>
            <div>
              <div class="task-title">完成待办应用线框图</div>
              <div class="task-meta">已完成 · 今天 11:20</div>
            </div>
            <button class="delete">删除</button>
          </div>
          <div class="task">
            <div class="check"></div>
            <div>
              <div class="task-title">确认移动端按钮是否足够明显</div>
              <div class="task-meta">未完成 · 待确认</div>
            </div>
            <button class="delete">删除</button>
          </div>
        </div>
      </div>
    </section>

    <section class="bottom">
      <div class="aside-card">
        <h2>这个原型已经覆盖的核心功能</h2>
        <div class="tips">
          <div class="tip"><strong>添加任务</strong><span>顶部输入框 + 主按钮，模拟新增任务的主要路径。</span></div>
          <div class="tip"><strong>完成状态切换</strong><span>每条任务前有圆形状态位，表示点击后切换完成/未完成。</span></div>
          <div class="tip"><strong>删除任务</strong><span>每条任务右侧保留删除按钮，明确你的“可删除”需求已经进入界面。</span></div>
        </div>
      </div>

      <div class="aside-card">
        <h2>下一步可确认的点</h2>
        <div class="filters">
          <span class="filter active">输入框位置</span>
          <span class="filter">列表样式</span>
          <span class="filter">完成状态</span>
          <span class="filter">删除交互</span>
        </div>
        <div class="tips" style="margin-top:18px;">
          <div class="tip"><strong>如果界面方向对</strong><span>继续补筛选、统计、历史记录等功能。</span></div>
          <div class="tip"><strong>如果还不够像产品</strong><span>直接在工作区反馈里说你想要更简洁、更多卡片感，或更偏移动端。</span></div>
        </div>
      </div>
    </section>
  </div>
</body>
</html>
"""


def _schema_module_library() -> List[Dict[str, Any]]:
    return [
        {"key": "about", "label": "关于", "component": "intro", "keywords": ["关于", "关于我们", "公司介绍", "团队", "品牌", "作者"]},
        {"key": "services", "label": "服务", "component": "feature-grid", "keywords": ["服务", "解决方案", "能力", "业务", "功能"]},
        {"key": "cases", "label": "案例", "component": "showcase", "keywords": ["案例", "项目", "客户", "作品", "成果"]},
        {"key": "contact", "label": "联系", "component": "contact", "keywords": ["联系", "咨询", "预约", "留资", "电话", "微信"]},
        {"key": "articles", "label": "内容", "component": "article-list", "keywords": ["文章", "博客", "写作", "归档", "专栏", "内容"]},
        {"key": "metrics", "label": "总览", "component": "stats", "keywords": ["总览", "指标", "数据", "报表", "概览", "统计"]},
        {"key": "table", "label": "列表", "component": "table", "keywords": ["列表", "表格", "订单", "用户", "库存", "审批", "管理"]},
        {"key": "detail", "label": "详情", "component": "detail", "keywords": ["详情", "审核", "编辑", "备注", "记录", "设置"]},
        {"key": "catalog", "label": "商品", "component": "catalog", "keywords": ["商品", "分类", "购物车", "下单", "电商", "商城"]},
        {"key": "form", "label": "输入", "component": "form", "keywords": ["表单", "输入", "生成", "结果", "上传", "查询", "工具"]},
        {"key": "board", "label": "任务", "component": "board", "keywords": ["待办", "任务", "清单", "看板", "计划", "日程"]},
        {"key": "play", "label": "玩法", "component": "playground", "keywords": ["游戏", "玩法", "棋盘", "关卡", "分数", "角色", "2048"]},
        {"key": "faq", "label": "问答", "component": "faq", "keywords": ["faq", "问题", "常见问题", "答疑"]},
        {"key": "pricing", "label": "报价", "component": "pricing", "keywords": ["价格", "报价", "套餐", "定价"]},
    ]


def _schema_title_for_module(component: str, app_name: str, module_label: str, brief: Dict[str, Any]) -> str:
    title_map = {
        "intro": f"{app_name} 是什么，以及为什么值得继续往下看",
        "feature-grid": "把核心能力拆成几块，让用户快速判断是否匹配",
        "showcase": "用真实案例、结果和交付内容建立信任",
        "contact": "把需求发过来，尽快进入下一步沟通",
        "article-list": "最近更新的内容、专题和长期栏目",
        "stats": "先看关键指标和待处理事项，再进入具体页面",
        "table": "主要业务列表、筛选状态和常用操作入口",
        "detail": "详情、备注、状态流转和继续处理入口",
        "catalog": "主推内容、分类入口和继续深入浏览的路径",
        "form": "先填必要输入，再立即看到结果和下一步动作",
        "board": "把今天要做的事放进同一个操作面板里",
        "playground": "打开页面就能开始，而不是先看一堆解释",
        "faq": "提前回答用户最常问的问题，减少理解成本",
        "pricing": "把价格方式和交付边界讲清楚，降低犹豫成本",
    }
    return title_map.get(component, f"{module_label} 作为当前页面的重要模块")


def _schema_summary_for_module(component: str, module_label: str, brief: Dict[str, Any]) -> str:
    core_objects = _compact_join([str(item) for item in brief.get("core_objects", [])], 3)
    core_actions = _compact_join([str(item) for item in brief.get("core_actions", [])], 3)
    summary_map = {
        "intro": "这里不是抽象口号，而是用一句清楚的话说明你是谁、服务谁、凭什么值得继续了解。",
        "feature-grid": f"围绕 {core_objects} 或关键能力展开，把每块内容讲成用户能直接理解的具体价值。",
        "showcase": "不要只写“有案例”，而是展示做过什么、结果如何、为什么可信。",
        "contact": "让用户看完页面后知道该怎么继续联系、咨询或开始下一步。",
        "article-list": "内容标题、摘要和栏目要像真的站点更新，而不是说明这个区块是做什么的。",
        "stats": "总览区需要先回答“现在状态怎么样”，再引导用户进入具体处理页面。",
        "table": f"列表区围绕 {core_actions} 展开，强调筛选、状态、批量动作和继续处理。",
        "detail": "详情区承接对象说明、操作记录、状态修改和备注，让用户知道下一步能做什么。",
        "catalog": "用主推卡片、分类入口和深入浏览路径组成一个可继续探索的界面。",
        "form": "输入区、执行按钮和结果区要形成闭环，而不是只摆一堆字段。",
        "board": "新增、状态切换、删除和归档这些动作应该在同一屏里自然成立。",
        "playground": "玩法区要把目标、反馈和重来入口放在当前视线里，减少解释文字。",
        "faq": "把最容易卡住用户的问题提前讲清楚，帮助他们继续往下走。",
        "pricing": "明确交付方式、价格逻辑和适合场景，避免空泛的“联系我们获取报价”。",
    }
    return summary_map.get(component, f"{module_label} 需要有可确认的真实内容，而不是说明性占位。")


def _schema_bullets_for_module(component: str, module_label: str, brief: Dict[str, Any]) -> List[str]:
    core_pages = brief.get("core_pages") if isinstance(brief.get("core_pages"), list) else []
    page_names = [str(item.get("name") or "") for item in core_pages if isinstance(item, dict)]
    defaults = [
        f"{module_label} 要能单独成立，不依赖大量解释文字。",
        f"这里后续可以继续扩成独立页面或更细的模块层级。",
        f"当前重点是确认 {module_label} 是否真的属于这个产品。",
    ]
    if component == "feature-grid":
        return [
            "每个能力块都应该说明解决什么问题，而不是只写一个标签。",
            "如果用户继续展开，这里可以接详情页、流程页或更细的功能页。",
            f"当前关联页面：{_compact_join(page_names, 3)}。",
        ]
    if component == "playground":
        return [
            "先让用户一眼看懂当前目标，再给即时反馈。",
            "主操作和重来入口要靠近主区域，不要藏太深。",
            "如果继续补强，可以在这里加动画、失败态或更多状态提示。",
        ]
    if component == "table":
        return [
            "列表页要同时承担浏览、筛选和继续处理的职责。",
            "状态标签、批量动作和分页位置需要清楚。",
            "后续可以继续扩展列定义、筛选条件和详情跳转。",
        ]
    return defaults


def _build_interface_schema(workspace: Workspace, stages: List[WorkspaceStage]) -> Dict[str, Any]:
    requirement = _stage_content(stages, WorkspaceStageKey.REQUIREMENTS) or workspace.description or workspace.name
    product = _stage_content(stages, WorkspaceStageKey.PRODUCT) or ""
    ui_direction = _stage_selected_option(stages, WorkspaceStageKey.UI_DIRECTION) or "专业清晰型"
    brief = _build_design_brief(workspace, stages)
    request_text = "\n".join(filter(None, [workspace.name, workspace.description or "", requirement, product]))
    lowered = request_text.lower()
    app_name = brief.get("app_name") or _extract_product_subject(workspace.name, requirement, workspace.description or "") or workspace.name

    matched: List[Dict[str, Any]] = []
    seen = set()
    for item in _schema_module_library():
        if any(keyword.lower() in lowered for keyword in item["keywords"]):
            if item["key"] not in seen:
                matched.append(item)
                seen.add(item["key"])

    if not matched:
        for page in brief.get("core_pages", [])[:4]:
            if not isinstance(page, dict):
                continue
            name = str(page.get("name") or "")
            purpose = str(page.get("purpose") or "")
            combined = f"{name} {purpose}".lower()
            for item in _schema_module_library():
                if item["key"] in seen:
                    continue
                if any(keyword.lower() in combined for keyword in item["keywords"]):
                    matched.append(item)
                    seen.add(item["key"])
                    break

    if not matched:
        matched = [
            {"key": "intro", "label": "介绍", "component": "intro"},
            {"key": "services", "label": "能力", "component": "feature-grid"},
            {"key": "contact", "label": "联系", "component": "contact"},
        ]

    if "官网" in request_text and "contact" not in seen:
        matched.append({"key": "contact", "label": "联系", "component": "contact"})
    if ("后台" in request_text or "管理" in request_text) and "table" not in seen:
        matched.insert(0, {"key": "metrics", "label": "总览", "component": "stats"})
        matched.insert(1, {"key": "table", "label": "列表", "component": "table"})
        matched.append({"key": "detail", "label": "详情", "component": "detail"})
    if ("游戏" in request_text or "2048" in lowered) and "play" not in seen:
        matched.insert(0, {"key": "play", "label": "玩法", "component": "playground"})

    modules = []
    for index, item in enumerate(matched[:4]):
        component = str(item.get("component") or "feature-grid")
        label = str(item.get("label") or f"模块 {index + 1}")
        title = _schema_title_for_module(component, str(app_name), label, brief)
        summary = _schema_summary_for_module(component, label, brief)
        modules.append({
            "id": f"module-{index + 1}",
            "key": str(item.get("key") or f"module-{index + 1}"),
            "label": label,
            "component": component,
            "title": title,
            "summary": summary,
            "bullets": _schema_bullets_for_module(component, label, brief),
        })

    components = {module["component"] for module in modules}
    if "playground" in components or "board" in components:
        layout_mode = "immersive"
    elif "stats" in components or "table" in components or "detail" in components:
        layout_mode = "console"
    elif "article-list" in components:
        layout_mode = "editorial"
    else:
        layout_mode = "marketing"

    nav_items = ["首页"] + [module["label"] for module in modules[:4]]
    primary_action = "立即咨询" if "contact" in {module["key"] for module in modules} else (
        "开始体验" if layout_mode == "immersive" else (
            "查看总览" if layout_mode == "console" else "查看核心内容"
        )
    )
    secondary_action = (
        modules[1]["label"] if len(modules) > 1 else "查看更多"
    )
    hero_title = str(app_name)
    hero_subtitle = (
        _first_sentence(product, "")
        or _first_sentence(requirement, "")
        or _first_sentence(workspace.description or "", "")
        or "根据当前需求动态组织页面结构、模块顺序和主要操作。"
    )
    hero_subtitle = hero_subtitle if len(hero_subtitle) > 18 else f"{hero_subtitle}，并让首页、主要模块和下一步动作都直接可确认。"

    return {
        "app_name": app_name,
        "platform_label": _platform_label(workspace.target_platform),
        "layout_mode": layout_mode,
        "ui_mode": ui_direction,
        "hero_title": hero_title,
        "hero_subtitle": hero_subtitle,
        "primary_action": primary_action,
        "secondary_action": secondary_action,
        "nav_items": nav_items,
        "modules": modules,
        "focus_points": brief.get("confirmation_points") or ["首屏是否对路", "模块是否像真实产品", "主操作是否明确"],
        "summary": _first_sentence(product or requirement, hero_subtitle),
        "feature_label": f"{workspace.target_platform} · schema-first prototype",
        "status_label": "可确认",
    }


def _schema_preview_markup(schema: Dict[str, Any]) -> str:
    modules = schema["modules"]
    mode = str(schema.get("layout_mode") or "marketing")
    if mode == "console":
        cards = "".join(
            f'<article class="preview-stat"><span>{_safe_text(module["label"])}</span><strong>{_safe_text(module["title"])}</strong><p>{_safe_text(module["summary"])}</p></article>'
            for module in modules[:3]
        )
        return f'<div class="preview-console">{cards}<div class="preview-table"><div class="preview-row header"><span>对象</span><span>状态</span><span>动作</span></div><div class="preview-row"><span>最新项 A</span><span>进行中</span><span>查看</span></div><div class="preview-row"><span>最新项 B</span><span>待处理</span><span>处理</span></div><div class="preview-row"><span>最新项 C</span><span>已完成</span><span>回看</span></div></div></div>'
    if mode == "immersive":
        chips = "".join(f'<span>{_safe_text(module["label"])}</span>' for module in modules[:3])
        return f'<div class="preview-immersive"><div class="preview-hud">{chips}</div><div class="preview-stage"><div class="stage-orb"></div><div class="stage-target"></div><div class="stage-path"></div></div><div class="preview-caption">{_safe_text(modules[0]["summary"])}</div></div>'
    if mode == "editorial":
        return "".join(
            f'<article class="preview-article{" featured" if index == 0 else ""}"><span class="preview-kicker">{_safe_text(module["label"])}</span><strong>{_safe_text(module["title"])}</strong><p>{_safe_text(module["summary"])}</p></article>'
            for index, module in enumerate(modules[:3])
        )
    return "".join(
        f'<article class="preview-panel{" featured" if index == 0 else ""}"><span class="preview-kicker">{_safe_text(module["label"])}</span><strong>{_safe_text(module["title"])}</strong><p>{_safe_text(module["summary"])}</p></article>'
        for index, module in enumerate(modules[:3])
    )


def _schema_modules_markup(schema: Dict[str, Any]) -> str:
    return "".join(
        f"""
        <article class="module-card">
          <div class="module-meta">{_safe_text(module["label"])}</div>
          <h2>{_safe_text(module["title"])}</h2>
          <p>{_safe_text(module["summary"])}</p>
          <ul>{"".join(f"<li>{_safe_text(str(item))}</li>" for item in module["bullets"][:3])}</ul>
        </article>
        """
        for module in schema["modules"]
    )


def _schema_playground_markup(schema: Dict[str, Any]) -> str:
    modules = schema["modules"]
    if not modules:
        return ""
    cards_html = "".join(
        f'<button class="schema-item{" active" if index == 0 else ""}" type="button" data-schema-card data-title="{html_lib.escape(module["title"])}" data-body="{html_lib.escape(module["summary"])}" data-label="{html_lib.escape(module["label"])}" data-bullets="{html_lib.escape("｜".join(module["bullets"][:3]))}"><span class="schema-chip">{html_lib.escape(module["label"])}</span><strong>{html_lib.escape(module["title"])}</strong><p>{html_lib.escape(module["summary"])}</p></button>'
        for index, module in enumerate(modules)
    )
    active = modules[0]
    bullets = "".join(f"<span>{_safe_text(str(item))}</span>" for item in active["bullets"][:3])
    return f"""
      <section class="experience" id="interaction-playground">
        <div class="experience-head">
          <div>
            <div class="eyebrow">可操作原型</div>
            <h2>模块切换与页面确认</h2>
          </div>
          <div class="sample-row">{"".join(f'<span class="sample">{_safe_text(module["label"])}</span>' for module in modules[:4])}</div>
        </div>
        <div class="experience-grid">
          <div class="schema-list">{cards_html}</div>
          <article class="schema-reader">
            <span class="schema-chip" id="schemaReaderLabel">{_safe_text(active["label"])}</span>
            <h3 id="schemaReaderTitle">{_safe_text(active["title"])}</h3>
            <p id="schemaReaderBody">{_safe_text(active["summary"])}</p>
            <div class="reader-actions">
              <button class="primary" type="button" id="schemaPrimaryAction">{_safe_text(schema["primary_action"])}</button>
              <button class="secondary" type="button" id="schemaSecondaryAction">{_safe_text(schema["secondary_action"])}</button>
            </div>
            <div class="reader-note" id="schemaReaderNote">点击左侧不同模块，确认这个产品真正需要的页面组成，而不是被固定类型模板带偏。</div>
            <div class="reader-outline">
              <strong>当前模块要点</strong>
              <div class="outline-list" id="schemaReaderBullets">{bullets}</div>
            </div>
          </article>
        </div>
      </section>
    """


def _schema_playground_script() -> str:
    return """
  <script>
    const primary = document.getElementById("primaryAction");
    const secondary = document.getElementById("secondaryAction");
    const playground = document.getElementById("interaction-playground");
    primary?.addEventListener("click", () => playground?.scrollIntoView({ behavior: "smooth", block: "start" }));
    secondary?.addEventListener("click", () => {
      document.querySelectorAll("[data-schema-card]")[1]?.click();
      playground?.scrollIntoView({ behavior: "smooth", block: "start" });
    });

    const cards = Array.from(document.querySelectorAll("[data-schema-card]"));
    const titleEl = document.getElementById("schemaReaderTitle");
    const bodyEl = document.getElementById("schemaReaderBody");
    const labelEl = document.getElementById("schemaReaderLabel");
    const bulletsEl = document.getElementById("schemaReaderBullets");
    const noteEl = document.getElementById("schemaReaderNote");

    const activateCard = (card) => {
      cards.forEach((item) => item.classList.remove("active"));
      card.classList.add("active");
      titleEl.textContent = card.dataset.title || "";
      bodyEl.textContent = card.dataset.body || "";
      labelEl.textContent = card.dataset.label || "";
      const bullets = (card.dataset.bullets || "").split("｜").filter(Boolean);
      bulletsEl.innerHTML = bullets.map((item) => `<span>${item}</span>`).join("");
      noteEl.textContent = "当前模块已切换。继续看这个区块是不是你真正需要的内容，而不是因为系统先入为主地把你归进了某个固定类型。";
    };

    cards.forEach((card) => {
      card.addEventListener("click", () => activateCard(card));
    });
  </script>
    """


def _build_design_image_prompt(workspace: Workspace, stages: List[WorkspaceStage], viewport: str) -> str:
    brief = _build_design_brief(workspace, stages)
    requirement = _stage_content(stages, WorkspaceStageKey.REQUIREMENTS) or workspace.description or workspace.name
    product = _stage_content(stages, WorkspaceStageKey.PRODUCT) or ""
    ui_direction = _stage_selected_option(stages, WorkspaceStageKey.UI_DIRECTION) or "专业清晰型"
    core_pages = brief.get("core_pages") if isinstance(brief.get("core_pages"), list) else []
    page_lines = "\n".join(
        f"- {item.get('name')}: {item.get('purpose')}"
        for item in core_pages[:5]
        if isinstance(item, dict)
    )
    sample_items = brief.get("sample_items") if isinstance(brief.get("sample_items"), list) else []
    sample_descriptions = brief.get("sample_descriptions") if isinstance(brief.get("sample_descriptions"), list) else []
    sample_lines = "\n".join(
        f"- {item}: {sample_descriptions[index] if index < len(sample_descriptions) else ''}"
        for index, item in enumerate(sample_items[:5])
    )
    focus_lines = "\n".join(
        f"- {item}" for item in (brief.get("confirmation_points") or [])[:4]
    )
    core_objects = _compact_join([str(item) for item in brief.get("core_objects", [])], 6)
    core_actions = _compact_join([str(item) for item in brief.get("core_actions", [])], 6)
    platform_label = str(brief.get("platform_label") or workspace.target_platform)
    identity = str(brief.get("identity") or "product")
    visual_scope = "单张移动端产品界面截图" if viewport == "mobile" else "单张桌面端产品界面截图"
    size_hint = "1536x1024 横版" if viewport == "desktop" else "1024x1536 竖版"

    identity_instruction_map = {
        "marketing": "画面应像真实官网/落地页，有完整首屏、内容区、CTA，而不是概念海报。",
        "landing": "画面应像真实官网/落地页，有完整首屏、内容区、CTA，而不是概念海报。",
        "editorial": "画面应像真实内容型站点，有文章列表、摘要、栏目或阅读入口，而不是介绍这个网站是做什么的。",
        "blog": "画面应像真实内容型站点，有文章列表、摘要、栏目或阅读入口，而不是介绍这个网站是做什么的。",
        "console": "画面应像真实后台/控制台，有导航、指标卡、数据列表、状态标签和操作入口。",
        "dashboard": "画面应像真实后台/控制台，有导航、指标卡、数据列表、状态标签和操作入口。",
        "immersive": "画面应像真实可玩的产品界面，有主视区、即时反馈、状态条和开始/继续入口。",
        "game": "画面应像真实可玩的产品界面，有主视区、即时反馈、状态条和开始/继续入口。",
        "ecommerce": "画面应像真实商城界面，有商品、价格、分类、购物车和购买入口，而不是泛化宣传页。",
        "todo": "画面应像真实任务工具界面，有输入、任务列表、状态切换和归档入口。",
        "tool": "画面应像真实工具界面，有输入区、参数区、结果区、状态反馈和继续操作入口。",
    }
    copy_requirements = [
        "必须直接表现这个需求本身，不要把页面做成“解释这个产品是什么”的说明海报。",
        "所有标题、副标题、按钮、卡片、列表项都要像真实产品文案，允许中文真实内容示例。",
        "严禁出现占位词或抽象词：核心价值、服务说明、模块一、示例标题、这里是描述、Lorem ipsum。",
        "优先展示页面结构、主要交互和真实内容感，不要做成品牌海报、PPT 封面或信息图。",
        "信息层级要清楚，留白合理，按钮和可操作区域明显。",
    ]
    copy_block = "\n".join(f"- {item}" for item in copy_requirements)

    return "\n".join([
        f"请生成一张高保真 UI 设计稿，目标是 {visual_scope}。",
        f"产品名称：{workspace.name}",
        f"目标平台：{platform_label}",
        f"设计视口：{size_hint}",
        f"需求原文：{requirement}",
        f"产品方案补充：{product or '按需求原文直接展开'}",
        f"视觉方向：{ui_direction}",
        f"产品类型：{brief.get('identity_label') or identity}",
        "",
        "这张图必须看起来像用户真正想做的那个产品：",
        f"- 页面主体：{brief.get('hero_title') or brief.get('app_name') or workspace.name}",
        f"- 核心对象：{core_objects or '从需求中自行提炼'}",
        f"- 核心动作：{core_actions or '从需求中自行提炼'}",
        f"- 主按钮：{brief.get('primary_action') or '按真实主流程命名'}",
        f"- 次按钮：{brief.get('secondary_action') or '按真实辅助流程命名'}",
        f"- 导航：{_compact_join([str(item) for item in brief.get('nav_items', [])], 5) or '按真实页面结构命名'}",
        "",
        "优先体现这些真实页面或区域：",
        page_lines or "- 按需求和产品方案自行组织，不要套用“关于/服务/案例/联系”的默认骨架。",
        "",
        "可以使用这些真实示例内容，但不要逐字机械堆砌：",
        sample_lines or "- 从需求中生成具体业务内容，不要使用占位内容。",
        "",
        "评审重点：",
        focus_lines,
        "",
        "内容与视觉要求：",
        copy_block,
        f"- {identity_instruction_map.get(identity, '画面必须像真实产品界面，而不是通用模板。')}",
        "",
        "如果需求里提到官网、博客、商城、后台、工具或游戏，请直接把对应的界面内容画出来；",
        "如果需求是更细分的新类型，也不要套固定模板，而是根据需求自行组织页面内容、信息模块和交互入口。",
        "",
        "补充上下文：",
        f"- 需求摘要：{brief.get('requirement_summary') or requirement}",
        f"- 关键对象：{core_objects or '按当前需求自行提炼'}",
        f"- 关键动作：{core_actions or '按当前需求自行提炼'}",
    ])


def _build_llm_prototype_prompt(workspace: Workspace, stages: List[WorkspaceStage]) -> str:
    brief = _build_design_brief(workspace, stages)
    payload = {
        "workspace": {
            "name": workspace.name,
            "description": workspace.description,
            "target_platform": workspace.target_platform,
        },
        "product_type": brief.get("identity_label") or brief.get("identity"),
        "requirement": (_stage_content(stages, WorkspaceStageKey.REQUIREMENTS) or workspace.description or "")[:1000],
        "product_plan": (_stage_content(stages, WorkspaceStageKey.PRODUCT) or "")[:1000],
        "ui_direction": (_stage_content(stages, WorkspaceStageKey.UI_DIRECTION) or _stage_selected_option(stages, WorkspaceStageKey.UI_DIRECTION))[:700],
        "core_pages": brief.get("core_pages", [])[:5],
        "core_actions": brief.get("core_actions", [])[:6],
        "sample_items": brief.get("sample_items", [])[:5],
        "sample_descriptions": brief.get("sample_descriptions", [])[:5],
    }
    return _json_dumps(payload)


def _generation_error_message(label: str, exc: Exception) -> str:
    if isinstance(exc, asyncio.TimeoutError):
        return f"{label}超时：模型在限定时间内没有返回结果。建议使用更快的文本模型，例如 gpt-5.4-mini，或稍后重试。"
    text = str(exc).strip()
    if not text:
        text = f"{type(exc).__name__}"
    return f"{label}失败：{text}"


def _extract_complete_html_document(raw: str) -> str:
    cleaned = (raw or "").strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()

    lowered = cleaned.lower()
    start_candidates = [
        lowered.find("<!doctype"),
        lowered.find("<html"),
    ]
    starts = [index for index in start_candidates if index >= 0]
    if starts:
        cleaned = cleaned[min(starts):].strip()
        lowered = cleaned.lower()

    end = lowered.rfind("</html>")
    if end >= 0:
        cleaned = cleaned[:end + len("</html>")].strip()
        lowered = cleaned.lower()

    if "<html" not in lowered or "</html>" not in lowered:
        raise ValueError("模型没有返回完整 HTML 文档")
    if "<style" not in lowered or "</style>" not in lowered:
        raise ValueError("模型返回的 HTML 缺少内联样式，无法作为可确认原型")

    forbidden_fragments = [
        "lorem ipsum",
        "模块一",
        "示例标题",
        "这里是描述",
        "核心价值",
        "generated by team agent workspace prototype flow",
    ]
    matched = next((item for item in forbidden_fragments if item in lowered), None)
    if matched:
        raise ValueError(f"模型返回了骨架或占位内容：{matched}")

    return cleaned


async def _generate_llm_prototype_html(
    db: AsyncSession,
    user: User,
    workspace: Workspace,
    stages: List[WorkspaceStage],
) -> tuple[str, str, str]:
    model, provider, fallback_chain = await _resolve_generation_model(db, user)
    if not model or not provider:
        raise HTTPException(status_code=400, detail="请先在模型配置里设置可用的文本模型，再生成 HTML 原型")
    if "image" in model.lower():
        raise HTTPException(status_code=400, detail="当前默认模型是图片模型，请在模型配置里选择文本/代码模型后再生成 HTML 原型")
    model = await _prefer_low_latency_generation_model(db, user, provider, model)

    await llm_router.load_providers(db, user_id=user.id)
    system_prompt = """
你是资深产品设计师和前端原型工程师。你只输出一个完整可运行的 HTML 文档，不要 Markdown，不要代码块，不要解释。

目标：根据用户已经确认的需求、产品方案和 UI 方向，生成一个真正像目标产品的高保真 HTML 原型。

硬性要求：
- 必须输出完整单文件 HTML：包含 <!doctype html>、<html>、<head>、<style>、<body>，可直接在浏览器预览。
- 不允许使用默认骨架、占位模块或说明性页面；不要出现“模块一、示例标题、这里是描述、核心价值、Lorem ipsum”。
- 页面内容必须是这个具体产品本身：真实标题、真实导航、真实列表/卡片/表单/数据/状态/按钮文案。
- 如果是官网、博客、商城、后台、工具、游戏或其他类型，直接做出对应产品界面，不要做“介绍这个产品是什么”的模板页。
- 首屏必须让用户一眼看到产品本体和核心操作，不要把需求说明铺在页面上。
- CSS 必须内联在 <style> 中；可以写少量内联 JS 支持切换、筛选、勾选、标签页等原型交互。
- 不依赖外部图片、字体、CDN 或网络资源；视觉资产可用 CSS、渐变、符号、布局和真实文案表达。
- 桌面和移动端都必须响应式可读，不要出现文字重叠。
- 不要加入“Generated by...”之类生成器水印。
- 控制输出长度，优先完成一个首屏加 2-3 个关键区域的可确认原型。
""".strip()

    try:
        result = await asyncio.wait_for(
            llm_router.call(
                messages=[
                    LLMMessage(role="system", content=system_prompt),
                    LLMMessage(role="user", content=_build_llm_prototype_prompt(workspace, stages)),
                ],
                model=model,
                provider_name=provider,
                fallback_chain=fallback_chain,
                max_tokens=2600,
                temperature=0.45,
                session_id=workspace.id,
                session_type="workspace",
                agent_name="ui-ux-designer",
            ),
            timeout=180,
        )
        return _extract_complete_html_document(result.content), result.model, result.provider
    except HTTPException:
        raise
    except (LLMError, asyncio.TimeoutError, ValueError, Exception) as exc:
        raise HTTPException(status_code=400, detail=_generation_error_message("生成 HTML 原型", exc)) from exc


async def _generate_llm_design_html(
    db: AsyncSession,
    user: User,
    workspace: Workspace,
    stages: List[WorkspaceStage],
    viewport: str,
) -> tuple[str, str, str]:
    model, provider, fallback_chain = await _resolve_generation_model(db, user)
    if not model or not provider:
        raise HTTPException(status_code=400, detail="请先在模型配置里设置可用的文本模型，再生成 HTML 设计稿")
    if "image" in model.lower():
        raise HTTPException(status_code=400, detail="当前默认模型是图片模型，请在模型配置里选择文本/代码模型后再生成 HTML 设计稿")
    model = await _prefer_low_latency_generation_model(db, user, provider, model)

    await llm_router.load_providers(db, user_id=user.id)
    is_mobile = viewport == "mobile"
    viewport_label = "移动端 390x844 设计稿" if is_mobile else "桌面端 1440x1024 设计稿"
    layout_instruction = (
        "主体画布宽度按 390px 手机界面设计，居中展示，页面背景可以留出深浅对比。"
        if is_mobile
        else "主体画布宽度按 1440px 桌面网页设计，首屏高度接近 1024px，内容必须有真实产品界面密度。"
    )
    system_prompt = f"""
你是资深 UI 设计师。你只输出一个完整可运行的 HTML 文档，不要 Markdown，不要代码块，不要解释。

目标：根据用户需求生成一张可在浏览器里预览的 {viewport_label}。这是一份设计稿，不是说明文档。

硬性要求：
- 必须输出完整单文件 HTML：包含 <!doctype html>、<html>、<head>、<style>、<body>。
- {layout_instruction}
- 不允许使用默认骨架、占位模块或说明性页面；不要出现“模块一、示例标题、这里是描述、核心价值、服务说明、Lorem ipsum”。
- 页面内容必须是这个具体产品本身：真实标题、真实导航、真实列表/卡片/表单/数据/状态/按钮文案。
- 如果是官网、博客、商城、后台、工具、游戏或其他类型，直接做出对应产品界面。
- 首屏必须让用户一眼看到产品本体和核心操作，不要把需求说明铺在页面上。
- CSS 必须内联在 <style> 中，不依赖外部图片、字体、CDN 或网络资源。
- 视觉风格要完整，有颜色、层级、间距、组件状态和真实内容；不要只输出线框。
- 不要加入“Generated by...”之类生成器水印。
""".strip()

    brief = _build_design_brief(workspace, stages)
    prompt_payload = {
        "viewport": viewport,
        "workspace": {
            "name": workspace.name,
            "description": workspace.description,
            "target_platform": workspace.target_platform,
        },
        "product_type": brief.get("identity_label") or brief.get("identity"),
        "requirement": (_stage_content(stages, WorkspaceStageKey.REQUIREMENTS) or workspace.description or "")[:1200],
        "product_plan": (_stage_content(stages, WorkspaceStageKey.PRODUCT) or "")[:1200],
        "ui_direction": (_stage_content(stages, WorkspaceStageKey.UI_DIRECTION) or _stage_selected_option(stages, WorkspaceStageKey.UI_DIRECTION))[:800],
        "core_pages": brief.get("core_pages", [])[:5],
        "core_actions": brief.get("core_actions", [])[:6],
        "sample_items": brief.get("sample_items", [])[:5],
        "sample_descriptions": brief.get("sample_descriptions", [])[:5],
    }

    try:
        result = await asyncio.wait_for(
            llm_router.call(
                messages=[
                    LLMMessage(role="system", content=system_prompt),
                    LLMMessage(role="user", content=_json_dumps(prompt_payload)),
                ],
                model=model,
                provider_name=provider,
                fallback_chain=fallback_chain,
                max_tokens=2400,
                temperature=0.45,
                session_id=workspace.id,
                session_type="workspace",
                agent_name="ui-ux-designer",
            ),
            timeout=180,
        )
        return _extract_complete_html_document(result.content), result.model, result.provider
    except HTTPException:
        raise
    except (LLMError, asyncio.TimeoutError, ValueError, Exception) as exc:
        raise HTTPException(status_code=400, detail=_generation_error_message("生成 HTML 设计稿", exc)) from exc


def _wrap_text(value: str, max_chars: int, max_lines: int) -> List[str]:
    normalized = " ".join((value or "").split())
    if not normalized:
        return ["等待补充"]

    lines = []
    current = ""
    for char in normalized:
        current += char
        if len(current) >= max_chars:
            lines.append(current)
            current = ""
            if len(lines) >= max_lines:
                break
    if current and len(lines) < max_lines:
        lines.append(current)
    if len(lines) == max_lines and len(normalized) > sum(len(line) for line in lines):
        lines[-1] = lines[-1].rstrip("，。,. ") + "..."
    return lines


def _svg_text(lines: List[str], x: int, y: int, size: int, color: str, line_height: int) -> str:
    tspans = []
    for index, line in enumerate(lines):
        dy = 0 if index == 0 else line_height
        tspans.append(
            f'<tspan x="{x}" dy="{dy}">{html_lib.escape(line)}</tspan>'
        )
    return f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" font-family="Inter, Arial, sans-serif">{"".join(tspans)}</text>'


def _svg_surface_preview(shape: Dict[str, Any], x: int, y: int, width: int, height: int, accent: str, accent_soft: str, surface: str) -> str:
    blocks = shape["blocks"]
    samples = shape.get("sample_items") or ["示例一", "示例二", "示例三"]
    title = html_lib.escape(blocks[0][0])
    second = html_lib.escape(blocks[1][0])
    third = html_lib.escape(blocks[2][0])
    sample_a = html_lib.escape(str(samples[0]))
    sample_b = html_lib.escape(str(samples[1] if len(samples) > 1 else samples[0]))
    sample_c = html_lib.escape(str(samples[2] if len(samples) > 2 else samples[0]))

    if surface == "canvas":
        return f'''
  <rect x="{x}" y="{y}" width="{width}" height="{height}" rx="28" fill="#111827"/>
  <rect x="{x + 28}" y="{y + 28}" width="{width - 56}" height="{height - 92}" rx="22" fill="#1F2937" stroke="#374151" stroke-dasharray="10 10"/>
  <text x="{x + 50}" y="{y + 66}" font-size="18" font-weight="760" fill="#FFFFFF" font-family="Inter, Arial, sans-serif">{title}</text>
  <text x="{x + width - 162}" y="{y + 66}" font-size="15" font-weight="700" fill="#A7F3D0" font-family="Inter, Arial, sans-serif">{sample_b}</text>
  <circle cx="{x + 120}" cy="{y + 174}" r="28" fill="{accent}"/>
  <rect x="{x + width - 160}" y="{y + 142}" width="72" height="72" rx="18" fill="#F59E0B"/>
  <circle cx="{x + width - 220}" cy="{y + 250}" r="18" fill="#38BDF8"/>
  <rect x="{x + 38}" y="{y + height - 50}" width="{width - 76}" height="30" rx="15" fill="rgba(255,255,255,.12)"/>
  <text x="{x + 58}" y="{y + height - 30}" font-size="13" fill="#D1D5DB" font-family="Inter, Arial, sans-serif">{sample_a} · {sample_c}</text>'''

    if surface == "dashboard":
        card_w = (width - 56) // 3
        return f'''
  <rect x="{x}" y="{y}" width="{width}" height="{height}" rx="28" fill="#FFFFFF" stroke="#E5E7EB"/>
  <rect x="{x + 28}" y="{y + 28}" width="{card_w}" height="86" rx="18" fill="{accent_soft}"/>
  <rect x="{x + 28 + card_w + 14}" y="{y + 28}" width="{card_w}" height="86" rx="18" fill="#F8FAFC" stroke="#E5E7EB"/>
  <rect x="{x + 28 + (card_w + 14) * 2}" y="{y + 28}" width="{card_w}" height="86" rx="18" fill="#F8FAFC" stroke="#E5E7EB"/>
  <text x="{x + 52}" y="{y + 72}" font-size="15" font-weight="760" fill="{accent}" font-family="Inter, Arial, sans-serif">{sample_a}</text>
  <text x="{x + 52 + card_w + 14}" y="{y + 72}" font-size="15" font-weight="760" fill="#172033" font-family="Inter, Arial, sans-serif">{sample_b}</text>
  <text x="{x + 52 + (card_w + 14) * 2}" y="{y + 72}" font-size="15" font-weight="760" fill="#172033" font-family="Inter, Arial, sans-serif">{sample_c}</text>
  <rect x="{x + 28}" y="{y + 144}" width="{width - 56}" height="{height - 172}" rx="20" fill="#F8FAFC" stroke="#E5E7EB"/>
  <text x="{x + 56}" y="{y + 190}" font-size="18" font-weight="760" fill="#172033" font-family="Inter, Arial, sans-serif">{second}</text>
  <line x1="{x + 56}" y1="{y + 220}" x2="{x + width - 56}" y2="{y + 220}" stroke="#E5E7EB"/>
  <line x1="{x + 56}" y1="{y + 266}" x2="{x + width - 56}" y2="{y + 266}" stroke="#E5E7EB"/>
  <line x1="{x + 56}" y1="{y + 312}" x2="{x + width - 56}" y2="{y + 312}" stroke="#E5E7EB"/>'''

    if surface == "board":
        return f'''
  <rect x="{x}" y="{y}" width="{width}" height="{height}" rx="28" fill="#FFFFFF" stroke="#E5E7EB"/>
  <rect x="{x + 28}" y="{y + 30}" width="{width - 56}" height="62" rx="18" fill="#F8FAFC" stroke="#E5E7EB"/>
  <text x="{x + 54}" y="{y + 68}" font-size="15" fill="#667085" font-family="Inter, Arial, sans-serif">今天要完成什么？</text>
  <rect x="{x + width - 126}" y="{y + 42}" width="74" height="38" rx="12" fill="{accent}"/>
  <text x="{x + width - 104}" y="{y + 66}" font-size="14" font-weight="760" fill="#FFFFFF" font-family="Inter, Arial, sans-serif">添加</text>
  <rect x="{x + 28}" y="{y + 116}" width="74" height="32" rx="16" fill="{accent_soft}"/>
  <text x="{x + 50}" y="{y + 137}" font-size="12" font-weight="760" fill="{accent}" font-family="Inter, Arial, sans-serif">全部 6</text>
  <rect x="{x + 112}" y="{y + 116}" width="86" height="32" rx="16" fill="#FFFFFF" stroke="#E5E7EB"/>
  <text x="{x + 132}" y="{y + 137}" font-size="12" fill="#667085" font-family="Inter, Arial, sans-serif">进行中</text>
  <rect x="{x + 208}" y="{y + 116}" width="86" height="32" rx="16" fill="#FFFFFF" stroke="#E5E7EB"/>
  <text x="{x + 228}" y="{y + 137}" font-size="12" fill="#667085" font-family="Inter, Arial, sans-serif">已完成</text>
  <rect x="{x + 28}" y="{y + 172}" width="{width - 56}" height="68" rx="18" fill="#FFFFFF" stroke="#FED7AA"/>
  <circle cx="{x + 58}" cy="{y + 206}" r="10" fill="#FFFFFF" stroke="#F97316" stroke-width="3"/>
  <text x="{x + 82}" y="{y + 198}" font-size="15" font-weight="760" fill="#172033" font-family="Inter, Arial, sans-serif">{sample_a}</text>
  <text x="{x + 82}" y="{y + 220}" font-size="12" fill="#667085" font-family="Inter, Arial, sans-serif">今天 18:00 · 高优先级</text>
  <rect x="{x + 28}" y="{y + 256}" width="{width - 56}" height="68" rx="18" fill="#FFFFFF" stroke="#E5E7EB"/>
  <circle cx="{x + 58}" cy="{y + 290}" r="10" fill="#FFFFFF" stroke="{accent}" stroke-width="3"/>
  <text x="{x + 82}" y="{y + 282}" font-size="15" font-weight="760" fill="#172033" font-family="Inter, Arial, sans-serif">{sample_b}</text>
  <text x="{x + 82}" y="{y + 304}" font-size="12" fill="#667085" font-family="Inter, Arial, sans-serif">明天上午 · 进行中</text>
  <rect x="{x + 28}" y="{y + 340}" width="{width - 56}" height="54" rx="18" fill="#F8FAFC" stroke="#E5E7EB"/>
  <circle cx="{x + 58}" cy="{y + 367}" r="10" fill="#10B981"/>
  <text x="{x + 82}" y="{y + 372}" font-size="14" font-weight="760" fill="#94A3B8" text-decoration="line-through" font-family="Inter, Arial, sans-serif">{sample_c}</text>'''

    if surface == "catalog":
        card_w = (width - 68) // 2
        return f'''
  <rect x="{x}" y="{y}" width="{width}" height="{height}" rx="28" fill="#FFFFFF" stroke="#E5E7EB"/>
  <rect x="{x + 28}" y="{y + 28}" width="{width - 56}" height="92" rx="22" fill="{accent_soft}"/>
  <text x="{x + 52}" y="{y + 62}" font-size="14" font-weight="760" fill="{accent}" font-family="Inter, Arial, sans-serif">首页推荐</text>
  <text x="{x + 52}" y="{y + 92}" font-size="18" font-weight="760" fill="#172033" font-family="Inter, Arial, sans-serif">{sample_a}</text>
  <text x="{x + 52}" y="{y + 114}" font-size="12" fill="#667085" font-family="Inter, Arial, sans-serif">{title}</text>
  <rect x="{x + 28}" y="{y + 148}" width="{card_w}" height="176" rx="20" fill="#F8FAFC" stroke="#E5E7EB"/>
  <rect x="{x + 40 + card_w}" y="{y + 148}" width="{card_w}" height="176" rx="20" fill="#F8FAFC" stroke="#E5E7EB"/>
  <text x="{x + 50}" y="{y + 186}" font-size="15" font-weight="760" fill="#172033" font-family="Inter, Arial, sans-serif">{sample_b}</text>
  <text x="{x + 50}" y="{y + 210}" font-size="12" fill="#667085" font-family="Inter, Arial, sans-serif">分类 / 筛选 / 搜索</text>
  <text x="{x + 62 + card_w}" y="{y + 186}" font-size="15" font-weight="760" fill="#172033" font-family="Inter, Arial, sans-serif">{sample_c}</text>
  <text x="{x + 62 + card_w}" y="{y + 210}" font-size="12" fill="#667085" font-family="Inter, Arial, sans-serif">详情 / 加购 / 下单</text>
  <rect x="{x + 28}" y="{y + 348}" width="{width - 56}" height="42" rx="16" fill="#FFFFFF" stroke="#E5E7EB"/>
  <text x="{x + 52}" y="{y + 374}" font-size="12" fill="#667085" font-family="Inter, Arial, sans-serif">{second} · {third}</text>'''

    if surface == "form":
        return f'''
  <rect x="{x}" y="{y}" width="{width}" height="{height}" rx="28" fill="#FFFFFF" stroke="#E5E7EB"/>
  <rect x="{x + 32}" y="{y + 34}" width="{width - 64}" height="104" rx="20" fill="#F8FAFC" stroke="#E5E7EB"/>
  <text x="{x + 60}" y="{y + 76}" font-size="17" font-weight="760" fill="#172033" font-family="Inter, Arial, sans-serif">{title}</text>
  <text x="{x + 60}" y="{y + 112}" font-size="14" fill="#667085" font-family="Inter, Arial, sans-serif">{sample_a}</text>
  <rect x="{x + 32}" y="{y + 160}" width="158" height="52" rx="16" fill="{accent}"/>
  <text x="{x + 70}" y="{y + 193}" font-size="16" font-weight="760" fill="#FFFFFF" font-family="Inter, Arial, sans-serif">{html_lib.escape(shape["primary_action"])}</text>
  <rect x="{x + 32}" y="{y + 240}" width="{width - 64}" height="{height - 272}" rx="20" fill="{accent_soft}"/>
  <text x="{x + 60}" y="{y + 286}" font-size="17" font-weight="760" fill="{accent}" font-family="Inter, Arial, sans-serif">{second}</text>
  <text x="{x + 60}" y="{y + 324}" font-size="14" fill="#667085" font-family="Inter, Arial, sans-serif">{sample_b} · {sample_c}</text>'''

    if surface == "feed":
        return f'''
  <rect x="{x}" y="{y}" width="{width}" height="{height}" rx="28" fill="#FFFFFF" stroke="#E5E7EB"/>
  <rect x="{x + 32}" y="{y + 34}" width="{width - 64}" height="136" rx="24" fill="{accent_soft}"/>
  <text x="{x + 62}" y="{y + 82}" font-size="22" font-weight="780" fill="#172033" font-family="Inter, Arial, sans-serif">{sample_a}</text>
  <text x="{x + 62}" y="{y + 122}" font-size="14" fill="#667085" font-family="Inter, Arial, sans-serif">{title} · 6 min read</text>
  <rect x="{x + 32}" y="{y + 198}" width="{width - 64}" height="70" rx="18" fill="#F8FAFC" stroke="#E5E7EB"/>
  <text x="{x + 58}" y="{y + 241}" font-size="17" font-weight="740" fill="#172033" font-family="Inter, Arial, sans-serif">{sample_b}</text>
  <rect x="{x + 32}" y="{y + 288}" width="{width - 64}" height="70" rx="18" fill="#F8FAFC" stroke="#E5E7EB"/>
  <text x="{x + 58}" y="{y + 331}" font-size="17" font-weight="740" fill="#172033" font-family="Inter, Arial, sans-serif">{sample_c}</text>'''

    return f'''
  <rect x="{x}" y="{y}" width="{width}" height="{height}" rx="28" fill="#FFFFFF" stroke="#E5E7EB"/>
  <rect x="{x + 30}" y="{y + 34}" width="{width // 2 - 48}" height="{height - 68}" rx="24" fill="{accent_soft}"/>
  <rect x="{x + width // 2 + 12}" y="{y + 34}" width="{width // 2 - 42}" height="{height - 68}" rx="24" fill="#F8FAFC" stroke="#E5E7EB"/>
  <text x="{x + 60}" y="{y + 92}" font-size="22" font-weight="780" fill="{accent}" font-family="Inter, Arial, sans-serif">{title}</text>
  <text x="{x + width // 2 + 42}" y="{y + 92}" font-size="20" font-weight="760" fill="#172033" font-family="Inter, Arial, sans-serif">{second}</text>
  <text x="{x + 60}" y="{y + 142}" font-size="15" fill="#667085" font-family="Inter, Arial, sans-serif">{sample_a}</text>
  <text x="{x + width // 2 + 42}" y="{y + 142}" font-size="15" fill="#667085" font-family="Inter, Arial, sans-serif">{sample_b}</text>'''


def _build_design_svg(workspace: Workspace, stages: List[WorkspaceStage], viewport: str) -> str:
    schema = _build_interface_schema(workspace, stages)
    ui_mode = str(schema.get("ui_mode") or "")
    is_mobile = viewport == "mobile"
    accent = "#4F46E5"
    accent_soft = "#EEF2FF"
    surface = "#F8FAFC"
    if "轻快亲和" in ui_mode:
        accent = "#0F766E"
        accent_soft = "#DCFCE7"
        surface = "#F0FDFA"
    elif "高级品牌" in ui_mode:
        accent = "#B45309"
        accent_soft = "#FEF3C7"
        surface = "#FFFBEB"

    hero_title = str(schema["hero_title"])
    hero_subtitle = str(schema["hero_subtitle"])
    primary_cta = str(schema["primary_action"])
    secondary_cta = str(schema["secondary_action"])
    nav_text = "　".join(schema["nav_items"])
    modules = schema["modules"]
    module_a = modules[0] if modules else {"label": "模块 A", "title": "核心模块", "summary": "待补充"}
    module_b = modules[1] if len(modules) > 1 else module_a
    module_c = modules[2] if len(modules) > 2 else module_b
    app_name = str(schema["app_name"])

    if is_mobile:
        width, height = 390, 844
        title_size = 32
        margin = 22
        card_width = 346
        hero_lines = _wrap_text(hero_subtitle, 19, 3)
        module_b_lines = _wrap_text(str(module_b["summary"]), 22, 3)
        module_c_lines = _wrap_text(str(module_c["summary"]), 22, 3)
        return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <rect width="{width}" height="{height}" rx="34" fill="#F7F8FC"/>
  <rect x="18" y="18" width="354" height="808" rx="28" fill="#FFFFFF" stroke="#E5E7EB"/>
  <rect x="{margin}" y="34" width="{card_width}" height="54" rx="18" fill="{accent_soft}"/>
  <circle cx="55" cy="61" r="16" fill="{accent}"/>
  <text x="82" y="67" font-size="16" font-weight="700" fill="#172033" font-family="Inter, Arial, sans-serif">{_safe_text(app_name)}</text>
  <text x="{margin}" y="130" font-size="12" font-weight="700" fill="{accent}" font-family="Inter, Arial, sans-serif">移动端设计稿 · {_safe_text(str(schema["feature_label"]))}</text>
  <text x="{margin}" y="178" font-size="{title_size}" font-weight="800" fill="#172033" font-family="Inter, Arial, sans-serif">{_safe_text(hero_title)}</text>
  {_svg_text(hero_lines, margin, 218, 14, "#667085", 22)}
  <rect x="{margin}" y="286" width="{card_width}" height="148" rx="24" fill="{accent}"/>
  <text x="46" y="330" font-size="20" font-weight="760" fill="#FFFFFF" font-family="Inter, Arial, sans-serif">{_safe_text(primary_cta)}</text>
  <text x="46" y="360" font-size="13" fill="#E0E7FF" font-family="Inter, Arial, sans-serif">{html_lib.escape(str(module_a["summary"]))}</text>
  <rect x="238" y="314" width="84" height="92" rx="22" fill="#FFFFFF" opacity=".16"/>
  <rect x="{margin}" y="458" width="{card_width}" height="150" rx="22" fill="{surface}" stroke="#E5E7EB"/>
  <text x="46" y="498" font-size="16" font-weight="760" fill="#172033" font-family="Inter, Arial, sans-serif">{html_lib.escape(str(module_b["label"]))}</text>
  {_svg_text(module_b_lines, 46, 530, 12, "#667085", 18)}
  <rect x="46" y="556" width="118" height="30" rx="15" fill="#FFFFFF"/>
  <text x="66" y="575" font-size="12" font-weight="700" fill="{accent}" font-family="Inter, Arial, sans-serif">{_safe_text(secondary_cta)}</text>
  <rect x="{margin}" y="632" width="{card_width}" height="150" rx="22" fill="#FFFFFF" stroke="#E5E7EB"/>
  <text x="46" y="672" font-size="16" font-weight="760" fill="#172033" font-family="Inter, Arial, sans-serif">{html_lib.escape(str(module_c["label"]))}</text>
  {_svg_text(module_c_lines, 46, 706, 12, "#667085", 18)}
  <circle cx="56" cy="748" r="8" fill="{accent_soft}" stroke="{accent}"/>
  <circle cx="56" cy="772" r="8" fill="{accent_soft}" stroke="{accent}"/>
  <text x="74" y="752" font-size="12" fill="#172033" font-family="Inter, Arial, sans-serif">{html_lib.escape(str(module_b["title"]))}</text>
  <text x="74" y="776" font-size="12" fill="#172033" font-family="Inter, Arial, sans-serif">{html_lib.escape(str(module_c["title"]))}</text>
</svg>'''

    width, height = 1440, 1040
    detail_a_lines = _wrap_text(str(module_a["summary"]), 34, 3)
    detail_b_lines = _wrap_text(str(module_b["summary"]), 34, 3)
    detail_c_lines = _wrap_text(str(module_c["summary"]), 26, 3)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <rect width="{width}" height="{height}" fill="#F6F8FB"/>
  <rect x="84" y="54" width="1272" height="82" rx="24" fill="#FFFFFF" stroke="#E5E7EB"/>
  <circle cx="132" cy="95" r="22" fill="{accent}"/>
  <text x="170" y="104" font-size="22" font-weight="760" fill="#172033" font-family="Inter, Arial, sans-serif">{_safe_text(app_name)}</text>
  <text x="1030" y="101" font-size="16" fill="#667085" font-family="Inter, Arial, sans-serif">{_safe_text(nav_text)}</text>
  <text x="96" y="218" font-size="15" font-weight="760" fill="{accent}" font-family="Inter, Arial, sans-serif">桌面端设计稿 · {_safe_text(str(schema["feature_label"]))}</text>
  <text x="96" y="302" font-size="64" font-weight="820" fill="#172033" font-family="Inter, Arial, sans-serif">{_safe_text(hero_title)}</text>
  {_svg_text(_wrap_text(hero_subtitle, 26, 2), 100, 354, 21, "#667085", 34)}
  <rect x="96" y="520" width="190" height="54" rx="14" fill="{accent}"/>
  <text x="134" y="554" font-size="17" font-weight="760" fill="#FFFFFF" font-family="Inter, Arial, sans-serif">{_safe_text(primary_cta)}</text>
  <rect x="304" y="520" width="168" height="54" rx="14" fill="#FFFFFF" stroke="#E5E7EB"/>
  <text x="346" y="554" font-size="17" font-weight="760" fill="#172033" font-family="Inter, Arial, sans-serif">{_safe_text(secondary_cta)}</text>
  <rect x="792" y="196" width="468" height="418" rx="30" fill="#FFFFFF" stroke="#E5E7EB"/>
  <rect x="824" y="230" width="404" height="120" rx="24" fill="{accent_soft}"/>
  <text x="854" y="270" font-size="16" font-weight="760" fill="{accent}" font-family="Inter, Arial, sans-serif">{html_lib.escape(str(module_a["label"]))}</text>
  <text x="854" y="306" font-size="24" font-weight="800" fill="#172033" font-family="Inter, Arial, sans-serif">{html_lib.escape(str(module_a["title"]))}</text>
  <rect x="824" y="378" width="192" height="198" rx="22" fill="#F8FAFC" stroke="#E5E7EB"/>
  <rect x="1036" y="378" width="192" height="198" rx="22" fill="#F8FAFC" stroke="#E5E7EB"/>
  <text x="852" y="422" font-size="16" font-weight="760" fill="#172033" font-family="Inter, Arial, sans-serif">{html_lib.escape(str(module_b["label"]))}</text>
  <text x="1064" y="422" font-size="16" font-weight="760" fill="#172033" font-family="Inter, Arial, sans-serif">{html_lib.escape(str(module_c["label"]))}</text>
  <text x="852" y="454" font-size="13" fill="#667085" font-family="Inter, Arial, sans-serif">{html_lib.escape(str(module_b["title"]))}</text>
  <text x="1064" y="454" font-size="13" fill="#667085" font-family="Inter, Arial, sans-serif">{html_lib.escape(str(module_c["title"]))}</text>
  <rect x="96" y="700" width="390" height="228" rx="24" fill="#FFFFFF" stroke="#E5E7EB"/>
  <text x="128" y="750" font-size="22" font-weight="760" fill="#172033" font-family="Inter, Arial, sans-serif">{html_lib.escape(str(module_a["label"]))}</text>
  <text x="128" y="784" font-size="17" font-weight="760" fill="#172033" font-family="Inter, Arial, sans-serif">{html_lib.escape(str(module_a["title"]))}</text>
  {_svg_text(detail_a_lines, 128, 820, 15, "#667085", 24)}
  <rect x="128" y="880" width="132" height="36" rx="18" fill="{accent_soft}"/>
  <text x="158" y="903" font-size="14" font-weight="700" fill="{accent}" font-family="Inter, Arial, sans-serif">{_safe_text(primary_cta)}</text>
  <rect x="526" y="700" width="390" height="228" rx="24" fill="#FFFFFF" stroke="#E5E7EB"/>
  <text x="558" y="750" font-size="22" font-weight="760" fill="#172033" font-family="Inter, Arial, sans-serif">{html_lib.escape(str(module_b["label"]))}</text>
  <text x="558" y="784" font-size="17" font-weight="760" fill="#172033" font-family="Inter, Arial, sans-serif">{html_lib.escape(str(module_b["title"]))}</text>
  {_svg_text(detail_b_lines, 558, 820, 15, "#667085", 24)}
  <rect x="956" y="700" width="300" height="228" rx="24" fill="#FFFFFF" stroke="#E5E7EB"/>
  <text x="988" y="750" font-size="22" font-weight="760" fill="#172033" font-family="Inter, Arial, sans-serif">{html_lib.escape(str(module_c["label"]))}</text>
  <text x="988" y="784" font-size="17" font-weight="760" fill="#172033" font-family="Inter, Arial, sans-serif">{html_lib.escape(str(module_c["title"]))}</text>
  {_svg_text(detail_c_lines, 988, 820, 15, "#667085", 24)}
</svg>'''


async def _create_workspace_artifact(
    db: AsyncSession,
    workspace: Workspace,
    user: User,
    artifact_type: str,
    filename: str,
    content: bytes,
    mime_type: str,
) -> Artifact:
    artifact_dir = settings.artifacts_dir / "workspaces" / workspace.id / artifact_type
    artifact_dir.mkdir(parents=True, exist_ok=True)
    path = artifact_dir / filename
    path.write_bytes(content)

    artifact = Artifact(
        session_type="workspace",
        session_id=workspace.id,
        artifact_type=artifact_type,
        filename=filename,
        path=str(path),
        mime_type=mime_type,
        size_bytes=len(content),
        checksum=hashlib.sha256(content).hexdigest(),
        source="generated",
        created_by=user.id,
    )
    db.add(artifact)
    await db.flush()
    return artifact


def _load_recommendation(stage: WorkspaceStage) -> Dict[str, Any]:
    if not stage.recommendation_json:
        return {}
    try:
        value = json.loads(stage.recommendation_json)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}


def _upsert_artifact_reference(
    recommendation: Dict[str, Any],
    artifact: Artifact,
    label: str,
) -> Dict[str, Any]:
    artifacts = recommendation.get("artifacts")
    if not isinstance(artifacts, list):
        artifacts = []
    artifacts = [
        item for item in artifacts
        if not (isinstance(item, dict) and item.get("type") == artifact.artifact_type and item.get("artifact_id") == artifact.id)
    ]
    artifacts.insert(0, {
        "type": artifact.artifact_type,
        "status": "ready",
        "label": label,
        "artifact_id": artifact.id,
        "url": f"/api/workspaces/{artifact.session_id}/artifacts/{artifact.id}",
        "mime_type": artifact.mime_type,
        "created_at": artifact.created_at.isoformat() if artifact.created_at else None,
    })
    recommendation["artifacts"] = artifacts
    recommendation.setdefault("summary", "已生成真实 HTML 原型，可直接预览页面效果。")
    recommendation["recommended_action"] = "请查看 HTML 原型预览。如果结构和视觉方向可以接受，就确认通过；否则提交反馈后重新生成。"
    return recommendation


async def _find_latest_workspace_artifact(
    db: AsyncSession,
    workspace_id: str,
    artifact_types: List[str],
) -> Optional[Artifact]:
    result = await db.execute(
        select(Artifact)
        .where(
            Artifact.session_type == "workspace",
            Artifact.session_id == workspace_id,
            Artifact.artifact_type.in_(artifact_types),
        )
        .order_by(Artifact.created_at.desc())
    )
    return result.scalars().first()


def _build_development_report(
    workspace: Workspace,
    stages: List[WorkspaceStage],
    preview_artifact: Artifact,
) -> str:
    requirement = _stage_content(stages, WorkspaceStageKey.REQUIREMENTS) or (workspace.description or "未补充")
    product = _stage_content(stages, WorkspaceStageKey.PRODUCT) or "未补充"
    ui_direction = _stage_selected_option(stages, WorkspaceStageKey.UI_DIRECTION) or "未明确"
    technical = _stage_content(stages, WorkspaceStageKey.TECHNICAL) or "未补充"
    return "\n".join([
        f"# {workspace.name} 开发执行记录",
        "",
        "## 本次交付状态",
        "- 已产出可预览页面文件。",
        "- 已为验收阶段准备可直接查看的 HTML 预览。",
        "- 当前为工作区内的阶段性交付，不宣称已完成完整业务开发。",
        "",
        "## 已锁定输入",
        f"- 需求确认：{requirement[:240]}",
        f"- 产品方案：{product[:240]}",
        f"- UI 方向：{ui_direction}",
        f"- 技术方案：{technical[:240]}",
        "",
        "## 本次交付物",
        f"- 预览文件：{preview_artifact.filename}",
        "- 交付目标：让用户在验收前先看到真实页面结构、主操作区和当前界面方向。",
        "",
        "## 下一步建议",
        "1. 进入验收阶段检查页面结构、主流程入口和视觉方向。",
        "2. 如需继续开发，可基于当前预览和反馈继续补功能。",
        "3. 如当前方向正确，再进入后续更真实的代码执行链。",
    ])


def _build_acceptance_report(
    workspace: Workspace,
    preview_artifact: Artifact,
    report_artifact: Optional[Artifact],
) -> str:
    lines = [
        f"# {workspace.name} 验收结论草稿",
        "",
        "## 验收对象",
        f"- 预览文件：{preview_artifact.filename}",
    ]
    if report_artifact:
        lines.append(f"- 开发记录：{report_artifact.filename}")
    lines.extend([
        "",
        "## 用户应重点检查",
        "1. 首屏是否准确表达产品目标。",
        "2. 核心按钮和主操作是否清楚。",
        "3. 页面结构是否符合前面确认的产品方案和 UI 方向。",
        "4. 当前预览是否足以作为继续开发的基础。",
        "",
        "## 当前结论",
        "- 已具备可查看、可讨论、可继续迭代的真实预览产物。",
        "- 还不应把当前结果表述为“完整业务已开发完成”，除非后续接入真实代码执行结果。",
        "",
        "## 建议动作",
        "- 如果页面方向正确：确认通过并继续后续开发/部署准备。",
        "- 如果页面方向不正确：回到相关阶段提交调整意见。",
    ])
    return "\n".join(lines)


def _workspace_project_dir(workspace: Workspace) -> Path:
    if workspace.storage_mode == "local" and workspace.root_path:
        return Path(workspace.root_path).expanduser()
    return settings.data_dir / "workspace-projects" / workspace.id


def _execution_manifest_path(project_dir: Path) -> Path:
    return project_dir / ".agent-workspace.json"


def _read_workspace_manifest(project_dir: Path) -> Dict[str, Any]:
    manifest_path = _execution_manifest_path(project_dir)
    if not manifest_path.exists():
        return {}
    try:
        content = json.loads(manifest_path.read_text(encoding="utf-8"))
        return content if isinstance(content, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


async def _upsert_workspace_execution_session(
    db: AsyncSession,
    workspace: Workspace,
    project_dir: Path,
) -> ExecutionSession:
    result = await db.execute(
        select(ExecutionSession).where(ExecutionSession.plan_id == workspace.id)
    )
    exec_session = result.scalar_one_or_none()
    if exec_session:
        exec_session.project_path = str(project_dir.resolve())
        if exec_session.status == ExecutionStatus.CREATED:
            exec_session.status = ExecutionStatus.READY
        exec_session.summary = f"{workspace.name} 工作区开发执行目录已准备完成。"
        return exec_session

    exec_session = ExecutionSession(
        plan_id=workspace.id,
        proposal_id=None,
        user_id=workspace.owner_id,
        status=ExecutionStatus.READY,
        project_path=str(project_dir.resolve()),
        summary=f"{workspace.name} 工作区开发执行目录已准备完成。",
    )
    db.add(exec_session)
    await db.flush()
    return exec_session


async def _replace_workspace_execution_tasks(
    db: AsyncSession,
    workspace: Workspace,
    exec_session: ExecutionSession,
    preview_artifact: Artifact,
) -> List[Task]:
    result = await db.execute(
        select(Task).where(
            Task.session_type == "workspace",
            Task.session_id == workspace.id,
            Task.execution_session_id == exec_session.id,
        )
    )
    existing_tasks = list(result.scalars().all())
    for task in existing_tasks:
        await db.delete(task)
    await db.flush()

    tasks = [
        Task(
            session_type="workspace",
            session_id=workspace.id,
            execution_session_id=exec_session.id,
            title="初始化工作区项目目录",
            description="创建当前工作区的实际项目目录，并写入基础元数据文件。",
            status=TaskStatus.COMPLETED,
            assigned_agent="implementation-engineer",
            owner_role="developer",
            target_paths_json=json.dumps(["README.md", ".agent-workspace.json"], ensure_ascii=False),
            result_summary="项目目录和基础元数据已创建。",
            order=0,
        ),
        Task(
            session_type="workspace",
            session_id=workspace.id,
            execution_session_id=exec_session.id,
            title="同步当前页面预览",
            description="把当前确认的原型同步到项目目录中的 index.html，作为可继续开发的入口。",
            status=TaskStatus.COMPLETED,
            assigned_agent="implementation-engineer",
            owner_role="developer",
            target_paths_json=json.dumps(["index.html"], ensure_ascii=False),
            result_summary=f"已生成 {preview_artifact.filename} 对应的项目预览入口。",
            order=1,
        ),
        Task(
            session_type="workspace",
            session_id=workspace.id,
            execution_session_id=exec_session.id,
            title="准备进入人工验收",
            description="汇总当前交付物、执行记录和下一步建议，供验收阶段直接使用。",
            status=TaskStatus.READY,
            assigned_agent="qa-reviewer",
            owner_role="tester",
            target_paths_json=json.dumps([], ensure_ascii=False),
            result_summary="开发交付已准备好，可进入验收。",
            order=2,
        ),
    ]
    db.add_all(tasks)
    await db.flush()
    return tasks


async def _record_workspace_checkpoint(
    db: AsyncSession,
    workspace: Workspace,
    user: User,
    label: str,
    state: Dict[str, Any],
) -> Checkpoint:
    checkpoint = Checkpoint(
        session_type="workspace",
        session_id=workspace.id,
        checkpoint_type="business",
        label=label,
        state_json=json.dumps(state, ensure_ascii=False),
        created_by=user.id,
    )
    db.add(checkpoint)
    await db.flush()
    return checkpoint


def _write_workspace_project_files(
    workspace: Workspace,
    stages: List[WorkspaceStage],
    project_dir: Path,
    preview_html: str,
    ui_direction: str,
) -> Dict[str, str]:
    project_dir.mkdir(parents=True, exist_ok=True)
    index_path = project_dir / "index.html"
    readme_path = project_dir / "README.md"
    manifest_path = _execution_manifest_path(project_dir)
    existing_manifest = _read_workspace_manifest(project_dir)
    binding_id = _ensure_workspace_binding_id(workspace)
    origin_workspace_id = str(existing_manifest.get("origin_workspace_id") or existing_manifest.get("workspace_id") or workspace.id)

    index_path.write_text(preview_html, encoding="utf-8")
    readme_path.write_text(
        "\n".join([
            f"# {workspace.name}",
            "",
            "这个目录由工作区开发阶段自动生成，用来承接当前已确认的页面方向和真实预览。",
            "",
            "## 当前内容",
            "- `index.html`：当前可预览页面入口",
            "- `.agent-workspace.json`：工作区执行元数据",
            "",
            "## 已确认上下文",
            f"- 需求：{(_stage_content(stages, WorkspaceStageKey.REQUIREMENTS) or workspace.description or '未补充')[:240]}",
            f"- 产品方案：{(_stage_content(stages, WorkspaceStageKey.PRODUCT) or '未补充')[:240]}",
            f"- UI 方向：{ui_direction or '未明确'}",
        ]),
        encoding="utf-8",
    )
    manifest_path.write_text(
        json.dumps(
            {
                "binding_id": binding_id,
                "workspace_id": workspace.id,
                "origin_workspace_id": origin_workspace_id,
                "workspace_name": workspace.name,
                "target_platform": workspace.target_platform,
                "storage_mode": workspace.storage_mode,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "last_bound_at": datetime.now(timezone.utc).isoformat(),
                "binding_version": 1,
                "importable": True,
                "current_stage": "development",
                "ui_direction": ui_direction,
                "requirements": _stage_content(stages, WorkspaceStageKey.REQUIREMENTS),
                "product": _stage_content(stages, WorkspaceStageKey.PRODUCT),
                "technical": _stage_content(stages, WorkspaceStageKey.TECHNICAL),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "index": str(index_path.resolve()),
        "readme": str(readme_path.resolve()),
        "manifest": str(manifest_path.resolve()),
    }


async def _generate_workspace_development_stage(
    db: AsyncSession,
    workspace: Workspace,
    user: User,
    stages: List[WorkspaceStage],
    stage: WorkspaceStage,
) -> WorkspaceStage:
    source_artifact = await _find_latest_workspace_artifact(
        db,
        workspace.id,
        ["prototype_html"],
    )
    if not source_artifact:
        raise HTTPException(status_code=400, detail="请先在原型确认阶段生成 HTML 原型，再进入开发执行")

    source_path = Path(source_artifact.path)
    if not source_path.exists():
        raise HTTPException(status_code=404, detail="原型文件不存在，无法生成开发预览")

    preview_bytes = source_path.read_bytes()
    preview_html = preview_bytes.decode("utf-8")
    project_dir = _workspace_project_dir(workspace)
    ui_direction = _stage_selected_option(stages, WorkspaceStageKey.UI_DIRECTION) or "未明确"
    written_files = _write_workspace_project_files(
        workspace=workspace,
        stages=stages,
        project_dir=project_dir,
        preview_html=preview_html,
        ui_direction=ui_direction,
    )
    exec_session = await _upsert_workspace_execution_session(db, workspace, project_dir)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    preview_artifact = await _create_workspace_artifact(
        db=db,
        workspace=workspace,
        user=user,
        artifact_type="development_preview",
        filename=f"development-preview-{timestamp}.html",
        content=preview_bytes,
        mime_type="text/html",
    )
    tasks = await _replace_workspace_execution_tasks(db, workspace, exec_session, preview_artifact)
    checkpoint = await _record_workspace_checkpoint(
        db=db,
        workspace=workspace,
        user=user,
        label="development_workspace_ready",
        state={
            "execution_session_id": exec_session.id,
            "project_path": exec_session.project_path,
            "preview_artifact_id": preview_artifact.id,
            "written_files": written_files,
            "task_ids": [task.id for task in tasks],
        },
    )
    report_content = _build_development_report(workspace, stages, preview_artifact).encode("utf-8")
    report_artifact = await _create_workspace_artifact(
        db=db,
        workspace=workspace,
        user=user,
        artifact_type="development_report",
        filename=f"development-report-{timestamp}.md",
        content=report_content,
        mime_type="text/markdown",
    )

    recommendation = _load_recommendation(stage)
    recommendation = _upsert_artifact_reference(recommendation, preview_artifact, "开发阶段预览")
    recommendation = _upsert_artifact_reference(recommendation, report_artifact, "开发执行记录")
    recommendation.update({
        "source": "development_generator_v1",
        "agent_name": _workspace_stage_agent_name(WorkspaceStageKey.DEVELOPMENT),
        "summary": "开发阶段已生成真实预览和执行记录，当前可以明确看到本轮交付结果。",
        "recommended_action": "先查看开发阶段预览和执行记录；如果方向正确，再进入验收阶段。",
        "focus": ["真实预览", "执行记录", "当前交付边界", "是否继续迭代"],
        "execution_session_id": exec_session.id,
        "project_path": exec_session.project_path,
        "checkpoint_id": checkpoint.id,
        "task_items": [
            {
                "id": task.id,
                "title": task.title,
                "status": task.status.value,
                "assigned_agent": task.assigned_agent,
                "result_summary": task.result_summary,
            }
            for task in tasks
        ],
    })

    stage.recommendation_json = json.dumps(recommendation, ensure_ascii=False)
    stage.content = "\n".join([
        "当前已生成开发阶段交付物，并已落下真实项目目录。",
        "",
        "本阶段已产出：",
        f"1. 开发预览：{preview_artifact.filename}",
        f"2. 执行记录：{report_artifact.filename}",
        f"3. 项目目录：{exec_session.project_path}",
        f"4. Checkpoint：{checkpoint.id}",
        "",
        "你现在可以直接查看预览效果，并判断这是否已经接近你要的页面方向。",
    ])
    stage.status = WorkspaceStageStatus.AWAITING_CONFIRMATION
    workspace.current_stage = WorkspaceStageKey.DEVELOPMENT
    workspace.updated_at = datetime.now(timezone.utc)
    return stage


async def _generate_workspace_acceptance_stage(
    db: AsyncSession,
    workspace: Workspace,
    user: User,
    stages: List[WorkspaceStage],
    stage: WorkspaceStage,
) -> WorkspaceStage:
    preview_artifact = await _find_latest_workspace_artifact(
        db,
        workspace.id,
        ["development_preview", "prototype_html"],
    )
    if not preview_artifact:
        raise HTTPException(status_code=400, detail="请先生成开发预览或 HTML 原型，再进入验收阶段")

    report_artifact = await _find_latest_workspace_artifact(
        db,
        workspace.id,
        ["development_report"],
    )
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    acceptance_report = await _create_workspace_artifact(
        db=db,
        workspace=workspace,
        user=user,
        artifact_type="acceptance_report",
        filename=f"acceptance-report-{timestamp}.md",
        content=_build_acceptance_report(workspace, preview_artifact, report_artifact).encode("utf-8"),
        mime_type="text/markdown",
    )

    recommendation = _load_recommendation(stage)
    recommendation = _upsert_artifact_reference(recommendation, preview_artifact, "验收预览")
    recommendation = _upsert_artifact_reference(recommendation, acceptance_report, "验收结论草稿")
    if report_artifact:
        recommendation = _upsert_artifact_reference(recommendation, report_artifact, "开发执行记录")
    recommendation.update({
        "source": "acceptance_generator_v1",
        "agent_name": _workspace_stage_agent_name(WorkspaceStageKey.ACCEPTANCE),
        "summary": "验收阶段已绑定真实预览和验收结论草稿，用户可以直接对结果做判断。",
        "recommended_action": "查看验收预览。如果页面方向和主流程符合预期，再确认通过；否则返回前面阶段继续调整。",
        "focus": ["验收预览", "主流程", "页面方向", "是否通过当前交付"],
    })
    development_stage = next((item for item in stages if item.stage_key == WorkspaceStageKey.DEVELOPMENT), None)
    development_recommendation = _load_recommendation(development_stage) if development_stage else {}
    for key in ("execution_session_id", "project_path", "checkpoint_id", "task_items"):
        if development_recommendation.get(key) is not None:
            recommendation[key] = development_recommendation.get(key)

    stage.recommendation_json = json.dumps(recommendation, ensure_ascii=False)
    stage.content = "\n".join([
        "当前已进入基于真实预览的验收，而不是只看描述文字。",
        "",
        f"验收预览文件：{preview_artifact.filename}",
        f"验收报告：{acceptance_report.filename}",
        "",
        "请重点确认：页面结构、主要操作入口、视觉方向是否符合你前面已确认的方案。",
    ])
    stage.status = WorkspaceStageStatus.AWAITING_CONFIRMATION
    workspace.current_stage = WorkspaceStageKey.ACCEPTANCE
    workspace.updated_at = datetime.now(timezone.utc)
    return stage


async def _resolve_generation_model(
    db: AsyncSession,
    user: User,
) -> tuple[Optional[str], Optional[str], Optional[List[str]]]:
    settings_result = await db.execute(
        select(ModelSettings).where(ModelSettings.user_id == user.id)
    )
    settings = settings_result.scalars().first()

    model = None
    provider = None
    fallback_chain = None
    if settings:
        model = settings.planning_model or settings.default_model
        if settings.fallback_chain_json:
            try:
                fallback_chain = json.loads(settings.fallback_chain_json)
            except json.JSONDecodeError:
                fallback_chain = None

    provider_result = await db.execute(
        select(ProviderConfig).where(
            ProviderConfig.user_id == user.id,
            ProviderConfig.enabled == True,
        )
    )
    providers = list(provider_result.scalars().all())
    keyed_providers = [provider for provider in providers if provider.api_key_encrypted]

    if model:
        for item in keyed_providers:
            prefix = f"{item.provider_name}/"
            if model.startswith(prefix):
                provider = item.provider_name
                model = model[len(prefix):]
                break
        if not provider:
            for item in keyed_providers:
                if item.default_model == model:
                    provider = item.provider_name
                    break

    if fallback_chain:
        normalized_fallback = []
        for entry in fallback_chain:
            if not isinstance(entry, str):
                continue
            for item in keyed_providers:
                prefix = f"{item.provider_name}/"
                if entry.startswith(prefix):
                    normalized_fallback.append(entry)
                    break
            else:
                for item in keyed_providers:
                    if item.default_model == entry:
                        normalized_fallback.append(f"{item.provider_name}/{entry}")
                        break
        fallback_chain = normalized_fallback or None

    if model and not provider:
        for item in keyed_providers:
            if item.default_model == model:
                provider = item.provider_name
                break

    if not model or not provider:
        for item in keyed_providers:
            if item.default_model:
                model = item.default_model
                provider = item.provider_name
                break

    if not model or not provider:
        return None, None, fallback_chain
    return model, provider, fallback_chain


async def _prefer_low_latency_generation_model(
    db: AsyncSession,
    user: User,
    provider_name: Optional[str],
    model: Optional[str],
) -> Optional[str]:
    if not provider_name or not model:
        return model
    model_lower = model.lower()
    if "mini" in model_lower or "spark" in model_lower:
        return model

    result = await db.execute(
        select(ProviderConfig).where(
            ProviderConfig.user_id == user.id,
            ProviderConfig.provider_name == provider_name,
        )
    )
    provider = result.scalars().first()
    if not provider or not provider.models_json:
        return model
    try:
        raw_models = json.loads(provider.models_json)
    except json.JSONDecodeError:
        return model

    available = {
        str(item.get("model_id") or item.get("id") or "").strip()
        for item in raw_models
        if isinstance(item, dict)
    }
    available.discard("")
    for candidate in ("gpt-5.4-mini", "gpt-5.3-codex-spark", "gpt-5.2"):
        if candidate in available:
            return candidate
    return model


def _build_stage_generation_prompt(
    workspace: Workspace,
    stages: List[WorkspaceStage],
    stage: WorkspaceStage,
    instruction: Optional[str],
) -> str:
    brief = _build_design_brief(workspace, stages)
    stage_context = []
    for item in stages:
        if item.content:
            stage_context.append({
                "stage": item.stage_key.value,
                "title": item.title,
                "status": item.status.value,
                "content": item.content[:2500],
                "user_feedback": item.user_feedback,
            })

    payload = {
        "workspace": {
            "name": workspace.name,
            "description": workspace.description,
            "target_platform": workspace.target_platform,
        },
        "current_stage": {
            "stage_key": stage.stage_key.value,
            "title": stage.title,
            "description": stage.description,
            "status": stage.status.value,
            "current_content": stage.content,
            "user_feedback": stage.user_feedback,
            "extra_instruction": instruction,
        },
        "design_brief": brief,
        "known_stage_outputs": stage_context,
    }
    return _json_dumps(payload)


def _workspace_stage_agent_name(stage_key: WorkspaceStageKey) -> str:
    return STAGE_AGENT_NAMES.get(stage_key, "orchestrator")


async def _resolve_workspace_stage_agent(
    db: AsyncSession,
    stage_key: WorkspaceStageKey,
) -> tuple[str, str]:
    agent_name = _workspace_stage_agent_name(stage_key)
    result = await db.execute(select(AgentTemplate).where(AgentTemplate.name == agent_name))
    agent = result.scalar_one_or_none()
    if agent and agent.system_prompt and agent.system_prompt.strip():
        return agent_name, agent.system_prompt.strip()

    builtin = BUILTIN_AGENT_MAP.get(agent_name, {})
    system_prompt = str(builtin.get("system_prompt") or "").strip()
    if not system_prompt:
        orchestrator_prompt = str(BUILTIN_AGENT_MAP.get("orchestrator", {}).get("system_prompt") or "").strip()
        system_prompt = orchestrator_prompt or "你是一个负责推进工作区阶段产出的专业 AI Agent。"
    return agent_name, system_prompt


def _stage_generation_contract(stage_key: WorkspaceStageKey) -> str:
    stage_rules = {
        WorkspaceStageKey.REQUIREMENTS: [
            "收敛目标用户、核心问题、MVP 范围、明确暂缓事项。",
            "不要默认加入复杂权限、运营、商业化、后台系统。",
            "必须保持 design_brief 里的产品本体一致，不要把博客写成待办、把商城写成博客。",
        ],
        WorkspaceStageKey.PRODUCT: [
            "输出页面列表、首页结构、主流程、关键状态和 P0/P1/P2 优先级。",
            "不同方案必须体现结构差异，不只是换说法。",
            "页面结构必须与 design_brief 的 core_pages、core_actions 保持一致。",
        ],
        WorkspaceStageKey.UI_DIRECTION: [
            "输出 2-3 个用户能理解的界面方向，并说明推荐理由。",
            "每个方向都要明确影响布局、信息层级、按钮强调和页面气质。",
            "方向是同一个产品本体的不同视觉表达，不允许把产品本体改成别的东西。",
        ],
        WorkspaceStageKey.PROTOTYPE: [
            "输出必须面向真实页面原型，不要把需求说明直接铺在页面上。",
            "优先提供可确认的页面结构、关键操作区、状态反馈和预览产物说明。",
            "原型必须像 design_brief 识别出的那个产品，而不是泛化页面。",
        ],
        WorkspaceStageKey.TECHNICAL: [
            "明确技术栈、模块边界、数据结构、运行方式、预览方式和不做项。",
            "方案必须能直接给开发执行阶段使用。",
        ],
        WorkspaceStageKey.DEVELOPMENT: [
            "输出真实开发执行视角：改动范围、执行步骤、验证方式、预览结果、阻塞风险。",
            "如果没有真实执行结果，不得伪装成已完成开发。",
        ],
        WorkspaceStageKey.ACCEPTANCE: [
            "必须围绕真实产物给出验收项、通过标准、问题项和结论。",
            "不要基于空文案给出通过判断。",
        ],
        WorkspaceStageKey.DEPLOYMENT: [
            "必须给出部署前检查、访问地址或失败原因、日志摘要和回滚建议。",
            "不要输出不可验证的部署成功文案。",
        ],
    }
    rules = stage_rules.get(stage_key, [])
    rules_block = "\n".join(f"- {rule}" for rule in rules)
    return f"""
你当前只负责 `{stage_key.value}` 阶段产出。请延续你的专业角色风格完成本阶段任务。

你必须只返回 JSON，不要 Markdown，不要代码块。JSON 格式：
{{
  "recommendation": {{
    "summary": "一句话说明本阶段建议",
    "recommended_action": "用户下一步应该怎么确认",
    "focus": ["确认点1", "确认点2"],
    "options": [
      {{"title": "方案名", "description": "给小白用户看的解释", "content": "这个方案对应的一版详细产物草稿", "recommended": true}}
    ],
    "selected_option": "默认选中的方案名",
    "artifacts": [
      {{"type": "concept_image|desktop_screenshot|mobile_screenshot|vision_review|document|preview|code_change|acceptance_report|deployment_log", "status": "pending", "label": "产物名称"}}
    ]
  }},
  "content": "当前默认选中方案对应的详细产物，使用中文，可包含编号列表"
}}

输出要求：
- 如果返回 options，每个 option 必须附带 content，让用户切换方案后当前产物立即变化。
- recommendation.summary、recommended_action 必须是用户能直接理解的短句。
- content 必须是当前阶段真正要确认的产物，不是空泛说明。
- 如果你判断当前阶段只能给一个强默认方案，也仍然要返回至少一个 option，并标记 recommended=true。

当前阶段附加规则：
{rules_block}
""".strip()


async def _generate_stage_artifact_with_llm(
    db: AsyncSession,
    user: User,
    workspace: Workspace,
    stages: List[WorkspaceStage],
    stage: WorkspaceStage,
    instruction: Optional[str],
) -> Optional[tuple[Dict[str, Any], str]]:
    llm_timeout_seconds = 20
    brief = _build_design_brief(workspace, stages)

    try:
        model, provider, fallback_chain = await _resolve_generation_model(db, user)
        if not model or not provider:
            return None

        await llm_router.load_providers(db, user_id=user.id)
        agent_name, agent_prompt = await _resolve_workspace_stage_agent(db, stage.stage_key)
        system_prompt = f"{agent_prompt}\n\n{_stage_generation_contract(stage.stage_key)}"

        result = await asyncio.wait_for(
            llm_router.call(
                messages=[
                    LLMMessage(role="system", content=system_prompt),
                    LLMMessage(role="user", content=_build_stage_generation_prompt(
                        workspace=workspace,
                        stages=stages,
                        stage=stage,
                        instruction=instruction,
                    )),
                ],
                model=model,
                provider_name=provider,
                fallback_chain=fallback_chain,
                # Stage recommendations are structured JSON; keeping this tighter
                # avoids long reasoning-only waits on models such as Xiaomi MiMo.
                max_tokens=1400,
                temperature=0.35,
                session_id=workspace.id,
                session_type="workspace",
                agent_name=agent_name,
            ),
            timeout=llm_timeout_seconds,
        )
        recommendation, content = _sanitize_llm_artifact(_parse_json_object(result.content))
        if not _llm_artifact_matches_brief(workspace, stage.stage_key, brief, recommendation, content):
            logger.warning(
                "workspace stage llm generation rejected by brief consistency check: workspace=%s stage=%s identity=%s",
                workspace.id,
                stage.stage_key.value,
                brief.get("identity"),
            )
            return None
        recommendation["source"] = "llm"
        recommendation["model"] = result.model
        recommendation["provider"] = result.provider
        recommendation["agent_name"] = agent_name
        return recommendation, content
    except Exception as exc:
        logger.warning(
            "workspace stage llm generation fell back to rules: workspace=%s stage=%s reason=%s",
            workspace.id,
            stage.stage_key.value,
            exc,
        )
        return None


def _generate_stage_artifact(
    workspace: Workspace,
    stages: List[WorkspaceStage],
    stage: WorkspaceStage,
    instruction: Optional[str],
) -> tuple[Dict[str, Any], str]:
    context = _stage_context(workspace, stages)
    requirement = context.get("requirements") or context["initial_requirement"] or "用户还没有补充详细需求"
    feedback = stage.user_feedback or instruction or ""
    platform_label = {
        "website": "网站",
        "miniapp": "小程序",
        "dashboard": "管理后台",
        "app": "应用",
    }.get(workspace.target_platform, workspace.target_platform)

    base = {
        "source": "rule_based_v1",
        "feedback_used": feedback,
        "artifacts": [],
    }
    brief = _build_design_brief(workspace, stages)
    identity_label = str(brief.get("identity_label") or brief.get("subject") or workspace.name)
    core_pages = brief.get("core_pages") if isinstance(brief.get("core_pages"), list) else []
    page_names = [str(item.get("name") or "").strip() for item in core_pages if isinstance(item, dict)]
    core_objects = brief.get("core_objects") if isinstance(brief.get("core_objects"), list) else []
    core_actions = brief.get("core_actions") if isinstance(brief.get("core_actions"), list) else []
    constraints = brief.get("constraints") if isinstance(brief.get("constraints"), list) else []
    key_states = brief.get("key_states") if isinstance(brief.get("key_states"), list) else []
    interaction_modes = brief.get("interaction_modes") if isinstance(brief.get("interaction_modes"), list) else []

    if stage.stage_key == WorkspaceStageKey.REQUIREMENTS:
        mvp_content = "\n".join([
            f"项目：{workspace.name}",
            f"目标类型：{platform_label}",
            f"识别出的产品本体：{identity_label}",
            "",
            "当前建议采用默认推荐解：",
            f"1. 先把第一版明确做成「{identity_label}」，不要把产品本体泛化成别的通用工具。",
            f"2. 第一版先打通核心闭环：{brief.get('core_loop_text') or _compact_join(core_actions)}。",
            f"3. 第一版优先保留这些核心页面：{_compact_join(page_names)}。",
            f"4. 页面里最关键的对象先围绕这些内容组织：{_compact_join(core_objects)}。",
            "",
            "本阶段需要用户确认：",
            f"- 这是不是你要的那个东西：{identity_label}",
            f"- 第一版最重要的动作是不是这些：{_compact_join(core_actions)}",
            f"- 页面骨架是否先按这些来：{_compact_join(page_names)}",
            f"- 暂缓项是否接受：{_compact_join(constraints, 3)}",
        ])
        full_content = "\n".join([
            f"项目：{workspace.name}",
            f"目标类型：{platform_label}",
            f"识别出的产品本体：{identity_label}",
            "",
            "当前建议采用扩展探索版：",
            f"1. 仍然保持「{identity_label}」这个产品本体不变，但第一版同时覆盖更多页面与状态。",
            f"2. 除核心闭环外，同步纳入这些状态或辅助模块：{_compact_join(key_states, 4)}。",
            f"3. 适合已经很清楚方向，希望首轮就看到更完整产品感的情况。",
            "4. 风险是生成时间更长，用户确认成本也更高。",
            "",
            "本阶段需要用户确认：",
            "- 是否真的要在第一版同时展开更多页面和状态",
            f"- 是否接受更大的首版范围：{_compact_join(page_names)}",
            "- 是否接受后续设计和开发成本一起上升",
        ])
        recommendation = {
            **base,
            "summary": f"建议先锁定产品本体为「{identity_label}」，第一版直接做出它最小但完整的一版默认解。",
            "recommended_action": f"先确认“这就是你要的 {identity_label}”以及第一版核心页面和动作；其他扩展能力先放后面。",
            "focus": ["产品本体", "核心页面", "核心动作", "暂缓事项"],
            "options": [
                _make_option("默认推荐解", f"先产出一版明确像「{identity_label}」的版本，不把需求泛化成别的产品。", mvp_content, True),
                _make_option("扩展探索版", "保持产品本体不变，但首版覆盖更多页面与状态。", full_content, False),
            ],
            "selected_option": "默认推荐解",
        }
        recommendation, content = _finalize_recommendation(recommendation, mvp_content)
        return recommendation, content

    if stage.stage_key == WorkspaceStageKey.PRODUCT:
        standard_content = "\n".join([
            f"产品方案草稿：{identity_label} 的默认结构",
            f"1. 产品本体保持为：{identity_label}。",
            f"2. 核心页面先按这条主线展开：{_compact_join(page_names)}。",
            f"3. 用户主流程优先围绕这些动作组织：{_compact_join(core_actions)}。",
            f"4. 页面之间的主要交互模式：{_compact_join(interaction_modes)}。",
            "",
            "页面说明：",
            *[
                f"- {item.get('name')}: {item.get('purpose')}"
                for item in core_pages[:5]
                if isinstance(item, dict)
            ],
            "",
            "优先级建议：",
            f"- P0：{_compact_join(page_names[:3] or page_names)}",
            f"- P1：{_compact_join(page_names[3:5]) if len(page_names) > 3 else _compact_join(key_states, 3)}",
            f"- P2：{_compact_join(constraints, 3)}",
        ])
        experience_content = "\n".join([
            f"产品方案草稿：{identity_label} 的体验强化版",
            f"1. 保持产品本体不变，重点强化这些关键状态：{_compact_join(key_states, 4)}。",
            f"2. 首页和主页面优先突出这些核心对象：{_compact_join(core_objects)}。",
            "3. 更强调首页第一屏的产品感、主操作位置和状态反馈。",
            "4. 适合希望先把“像不像那个东西”做得更强的情况。",
            "",
            "体验强化重点：",
            f"- 首页第一屏直接体现：{identity_label}",
            f"- 主操作聚焦：{_compact_join(core_actions, 3)}",
            f"- 必须出现的状态：{_compact_join(key_states, 3)}",
        ])
        structure_content = "\n".join([
            f"产品方案草稿：{identity_label} 的完整结构版",
            f"1. 在默认结构基础上，补齐更多辅助页面与说明信息。",
            f"2. 核心对象继续围绕：{_compact_join(core_objects)}。",
            f"3. 页面之间加入更多导航与辅助模块，但产品本体仍然不能漂移。",
            "4. 适合已经确认方向，想先看更完整信息架构的情况。",
            "",
            "补充结构建议：",
            f"- 主页面：{_compact_join(page_names)}",
            f"- 关键状态：{_compact_join(key_states, 4)}",
            f"- 暂缓内容：{_compact_join(constraints, 3)}",
        ])
        recommendation = {
            **base,
            "summary": f"建议先把「{identity_label}」的页面骨架和主流程定下来，后面所有原型和设计稿都严格继承它。",
            "recommended_action": "先确认页面列表、主流程和关键状态；确认后再进入 UI 方向选择。",
            "focus": ["页面列表", "主流程", "关键状态", "产品本体一致性"],
            "options": [
                _make_option("默认结构版", "先把最核心的页面骨架和主流程锁定下来。", standard_content, True),
                _make_option("体验强化版", "更强调首页产品感、主操作和关键状态。", experience_content, False),
                _make_option("完整结构版", "在保持产品本体不变的前提下补齐更多辅助结构。", structure_content, False),
            ],
        }
        recommendation, content = _finalize_recommendation(recommendation, standard_content)
        return recommendation, content

    if stage.stage_key == WorkspaceStageKey.UI_DIRECTION:
        options = _build_ui_direction_options(brief)
        selected_title = next((item["title"] for item in options if item.get("recommended")), options[0]["title"])
        default_content = next((item["content"] for item in options if item["title"] == selected_title), options[0]["content"])
        recommendation = {
            **base,
            "summary": f"建议先在「{identity_label}」这个产品本体下给出 2-3 个可理解的视觉方向，不让方向选择破坏产品本体。",
            "recommended_action": "先确认哪一种视觉表达最像你想要的那个产品，再进入原型生成。",
            "focus": ["视觉方向", "参考图", "色彩倾向", "组件密度"],
            "options": options,
            "selected_option": selected_title,
            "artifacts": [
                {"type": "concept_image", "status": "pending", "label": "概念风格图"},
                {"type": "reference_image", "status": "pending", "label": "用户参考图分析"},
            ],
        }
        recommendation, content = _finalize_recommendation(recommendation, default_content)
        return recommendation, content

    if stage.stage_key == WorkspaceStageKey.PROTOTYPE:
        recommendation = {
            **base,
            "summary": "原型确认阶段应优先展示真实 HTML/CSS 页面截图，而不是只展示无法落地的效果图。",
            "recommended_action": "先生成桌面端和移动端关键页面截图，用户确认后再进入开发。",
            "focus": ["真实页面截图", "移动端适配", "多模态 UI 审查", "用户反馈"],
            "artifacts": [
                {"type": "desktop_screenshot", "status": "pending", "label": "桌面端预览截图"},
                {"type": "mobile_screenshot", "status": "pending", "label": "移动端预览截图"},
                {"type": "vision_review", "status": "pending", "label": "多模态审查结果"},
            ],
        }
        content = "\n".join([
            "原型确认计划：",
            "1. 根据已确认的产品方案和 UI 方向生成真实页面原型。",
            "2. 自动启动本地预览并截取桌面端和移动端图片。",
            "3. 用多模态模型检查遮挡、错位、信息层级和移动端可读性。",
            "4. 用户确认后再进入代码开发。",
        ])
        return recommendation, content

    if stage.stage_key == WorkspaceStageKey.TECHNICAL:
        recommendation = {
            **base,
            "summary": "建议采用本地优先的多用户工作区架构，桌面端负责体验，本地后端负责调度，项目代码按工作区隔离。",
            "recommended_action": "确认技术栈、数据边界和部署测试方式后，再创建代码项目。",
            "focus": ["技术栈", "数据隔离", "执行边界", "部署方式"],
            "options": [
                _make_option(
                    "本地优先",
                    "适合当前测试阶段，成本低，对服务器压力小。",
                    "\n".join([
                        "技术方案：本地优先",
                        f"- 目标类型：{platform_label}",
                        "- 数据隔离：user_id + workspace_id 双层隔离。",
                        "- 项目目录：每个工作区一个独立代码目录。",
                        "- 执行方式：主要在本地完成生成、预览和修改。",
                        "- 优点：成本低、调试快、对服务器依赖小。",
                    ]),
                    True,
                ),
                _make_option(
                    "云端执行",
                    "便于多人协作，但服务器成本和安全边界要求更高。",
                    "\n".join([
                        "技术方案：云端执行",
                        f"- 目标类型：{platform_label}",
                        "- 数据隔离：需要更严格的租户隔离和任务沙箱。",
                        "- 项目目录：代码与任务运行环境在云端统一托管。",
                        "- 执行方式：Agent 和预览环境主要放在服务器侧。",
                        "- 风险：基础设施复杂度、成本和权限边界要求更高。",
                    ]),
                    False,
                ),
            ],
        }
        recommendation, content = _finalize_recommendation(
            recommendation,
            "\n".join([
                "技术方案草稿：",
                f"- 目标类型：{platform_label}",
                "- 数据隔离：user_id + workspace_id 双层隔离。",
                "- 项目目录：每个工作区一个独立代码目录。",
                "- 修改保护：每次开发执行前创建 checkpoint。",
                "- 预览方式：本地启动预览服务，截图给用户确认。",
                "- 部署测试：优先发布到用户自己的测试服务器或临时预览地址。",
            ]),
        )
        return recommendation, content

    if stage.stage_key == WorkspaceStageKey.DEVELOPMENT:
        recommendation = {
            **base,
            "summary": "开发阶段必须走 checkpoint -> 修改代码 -> 运行检查 -> 展示变更 -> 用户验收。",
            "recommended_action": "先生成开发任务清单，确认后再允许 Agent 写入工作区代码目录。",
            "focus": ["任务拆解", "checkpoint", "文件变更", "自动检查"],
        }
        content = "\n".join([
            "开发执行计划：",
            "1. 创建开发前 checkpoint。",
            "2. 按阶段产物拆解代码任务。",
            "3. Agent 只允许修改当前工作区代码目录。",
            "4. 修改后展示文件变更、运行结果和预览地址。",
            "5. 用户可以接受、继续调整或回滚。",
        ])
        return recommendation, content

    if stage.stage_key == WorkspaceStageKey.ACCEPTANCE:
        recommendation = {
            **base,
            "summary": "验收阶段要让用户看运行效果，而不是看代码或日志。",
            "recommended_action": "展示预览地址、截图、变更摘要和可选回滚入口。",
            "focus": ["预览地址", "变更摘要", "验收反馈", "回滚"],
        }
        content = "\n".join([
            "预览验收清单：",
            "- 页面是否符合确认过的 UI 方向？",
            "- 核心流程是否能跑通？",
            "- 移动端是否可读可操作？",
            "- 是否需要继续调整？",
        ])
        return recommendation, content

    recommendation = {
        **base,
        "summary": "部署测试阶段优先面向低配置服务器，保持流程简单、可回滚。",
        "recommended_action": "确认测试服务器、环境变量和部署命令，再执行发布。",
        "focus": ["测试服务器", "环境变量", "部署日志", "回滚方案"],
    }
    content = "\n".join([
        "部署测试计划：",
        "1. 检查构建命令和环境变量。",
        "2. 发布到测试服务器。",
        "3. 记录访问地址和部署日志。",
        "4. 失败时保留错误原因并支持回滚。",
    ])
    return recommendation, content


def _restore_workspace_stages_from_manifest(
    workspace: Workspace,
    requirement: str,
    manifest: Dict[str, Any],
) -> List[WorkspaceStage]:
    stages = _seed_stages(workspace, requirement)
    current_stage_raw = str(manifest.get("current_stage") or WorkspaceStageKey.DEVELOPMENT.value).strip()
    try:
        current_stage = WorkspaceStageKey(current_stage_raw)
    except ValueError:
        current_stage = WorkspaceStageKey.DEVELOPMENT
    current_index = [definition[0] for definition in STAGE_DEFINITIONS].index(current_stage)

    for index, stage in enumerate(stages):
        if index < current_index:
            stage.status = WorkspaceStageStatus.APPROVED
        elif index == current_index:
            stage.status = WorkspaceStageStatus.AWAITING_CONFIRMATION
        else:
            stage.status = WorkspaceStageStatus.DRAFT

        if stage.stage_key == WorkspaceStageKey.PRODUCT and manifest.get("product"):
            stage.content = str(manifest.get("product"))
        elif stage.stage_key == WorkspaceStageKey.TECHNICAL and manifest.get("technical"):
            stage.content = str(manifest.get("technical"))
        elif stage.stage_key == WorkspaceStageKey.DEVELOPMENT:
            stage.content = "该工作区已从本地目录重新导入，可继续生成开发预览并恢复执行链。"
            recommendation = _load_recommendation(stage)
            recommendation.update({
                "source": "local_import_v1",
                "summary": "已识别到本地目录绑定信息，可以继续把这份目录接回当前工作区。",
                "recommended_action": "先检查目录绑定是否正确，再继续生成开发预览或进入验收。",
                "focus": ["本地目录", "binding_id", "恢复执行链"],
            })
            stage.recommendation_json = json.dumps(recommendation, ensure_ascii=False)
    return stages


@router.post("/workspaces", response_model=WorkspaceResponse)
async def create_workspace(
    req: CreateWorkspaceRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    storage_mode = _normalize_storage_mode(req.storage_mode)
    workspace = Workspace(
        owner_id=user.id,
        name=req.name,
        description=req.description,
        target_platform=req.target_platform,
        binding_id=_new_binding_id(),
        storage_mode=storage_mode,
        created_by=user.id,
        current_stage=WorkspaceStageKey.REQUIREMENTS,
    )
    db.add(workspace)
    await db.flush()
    workspace.root_path = _normalize_root_path(storage_mode, req.root_path, workspace.id)

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


@router.post("/workspaces/import-local", response_model=ImportLocalWorkspaceResponse)
async def import_local_workspace(
    req: ImportLocalWorkspaceRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    root_path = _normalize_root_path("local", req.root_path)
    project_dir = Path(root_path)
    manifest = _read_workspace_manifest(project_dir)
    if not manifest:
        raise HTTPException(status_code=400, detail="所选目录没有 .agent-workspace.json，暂时无法导入")

    binding_id = str(manifest.get("binding_id") or "").strip()
    if not binding_id:
        raise HTTPException(status_code=400, detail="目录缺少 binding_id，无法识别为可导入工作区")

    result = await db.execute(
        select(Workspace, WorkspaceMember)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(
            Workspace.binding_id == binding_id,
            WorkspaceMember.user_id == user.id,
        )
    )
    existing_row = result.first()
    if existing_row:
        workspace, member = existing_row
        workspace.root_path = root_path
        workspace.storage_mode = "local"
        workspace.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(workspace)
        stages = await _get_stages(db, workspace.id)
        workspace_response = WorkspaceResponse.from_model(workspace, member.role, stages)
        return ImportLocalWorkspaceResponse(
            workspace=workspace_response.model_dump(),
            import_mode="rebind_existing",
            message="已识别到同一个 binding_id，当前目录已重新绑定回已有工作区。",
        )

    requirement = str(
        req.description
        or manifest.get("requirements")
        or manifest.get("product")
        or "从本地目录导入的工作区"
    )
    workspace = Workspace(
        owner_id=user.id,
        name=(req.name or str(manifest.get("workspace_name") or "导入的本地工作区")).strip(),
        description=requirement,
        target_platform=str(manifest.get("target_platform") or "website"),
        binding_id=binding_id,
        storage_mode="local",
        root_path=root_path,
        created_by=user.id,
        current_stage=WorkspaceStageKey.DEVELOPMENT,
    )
    db.add(workspace)
    await db.flush()

    member = WorkspaceMember(
        workspace_id=workspace.id,
        user_id=user.id,
        role=WorkspaceMemberRole.OWNER,
    )
    db.add(member)
    stages = _restore_workspace_stages_from_manifest(workspace, requirement, manifest)
    db.add_all(stages)
    workspace.current_stage = next(
        (stage.stage_key for stage in stages if stage.status == WorkspaceStageStatus.AWAITING_CONFIRMATION),
        WorkspaceStageKey.DEVELOPMENT,
    )
    await db.commit()
    await db.refresh(workspace)
    workspace_response = WorkspaceResponse.from_model(workspace, member.role, stages)
    return ImportLocalWorkspaceResponse(
        workspace=workspace_response.model_dump(),
        import_mode="restore_new",
        message="原工作区记录不存在，已基于本地目录和 binding_id 恢复出一个新的工作区记录。",
    )


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
    if req.storage_mode is not None:
        workspace.storage_mode = _normalize_storage_mode(req.storage_mode)
        workspace.root_path = _normalize_root_path(workspace.storage_mode, req.root_path, workspace.id)
    elif req.root_path is not None:
        workspace.root_path = _normalize_root_path(workspace.storage_mode, req.root_path, workspace.id)
    if req.status is not None:
        workspace.status = req.status

    await db.commit()
    await db.refresh(workspace)
    stages = await _get_stages(db, workspace.id)
    return WorkspaceResponse.from_model(workspace, member.role, stages)


@router.post("/workspaces/{workspace_id}/rebind-local", response_model=WorkspaceResponse)
async def rebind_local_workspace_directory(
    workspace_id: str,
    req: RebindWorkspaceDirectoryRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    workspace, member = await _get_accessible_workspace(db, workspace_id, user)
    _require_editor(member)

    if workspace.storage_mode != "local":
        raise HTTPException(status_code=400, detail="只有本地目录模式的工作区才支持重新绑定目录")

    root_path = _normalize_root_path("local", req.root_path)
    project_dir = Path(root_path)
    if not project_dir.exists() or not project_dir.is_dir():
        raise HTTPException(status_code=400, detail="所选目录不存在")

    manifest = _read_workspace_manifest(project_dir)
    if not manifest:
        raise HTTPException(status_code=400, detail="所选目录缺少 .agent-workspace.json，无法重新绑定")

    manifest_binding_id = str(manifest.get("binding_id") or "").strip()
    workspace_binding_id = _ensure_workspace_binding_id(workspace)
    if not manifest_binding_id:
        raise HTTPException(status_code=400, detail="目录 manifest 缺少 binding_id，无法重新绑定")
    if manifest_binding_id != workspace_binding_id:
        raise HTTPException(status_code=400, detail="这个目录属于另一个工作区绑定，不能直接重新绑定到当前工作区")

    workspace.root_path = root_path
    workspace.updated_at = datetime.now(timezone.utc)
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


@router.post("/workspaces/{workspace_id}/prototype", response_model=WorkspaceStageResponse)
async def generate_workspace_prototype(
    workspace_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    workspace, member = await _get_accessible_workspace(db, workspace_id, user)
    _require_editor(member)

    stages = await _get_stages(db, workspace_id)
    prototype_stage = next((stage for stage in stages if stage.stage_key == WorkspaceStageKey.PROTOTYPE), None)
    if not prototype_stage:
        raise HTTPException(status_code=404, detail="Prototype stage not found")

    prototype_html, model, provider = await _generate_llm_prototype_html(
        db=db,
        user=user,
        workspace=workspace,
        stages=stages,
    )
    html_content = prototype_html.encode("utf-8")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    artifact = await _create_workspace_artifact(
        db=db,
        workspace=workspace,
        user=user,
        artifact_type="prototype_html",
        filename=f"prototype-{timestamp}.html",
        content=html_content,
        mime_type="text/html",
    )

    recommendation = _upsert_artifact_reference(
        _load_recommendation(prototype_stage),
        artifact,
        "HTML 原型预览",
    )
    recommendation["source"] = "prototype_generator_llm_v1"
    recommendation["model"] = model
    recommendation["provider"] = provider
    recommendation["summary"] = f"已通过 {provider}/{model} 生成真实 HTML 原型，不再使用默认骨架模板。"
    recommendation["recommended_action"] = "请查看 HTML 原型预览。如果内容、布局和产品气质仍不像你的需求，请提交具体反馈后重新生成。"
    recommendation["focus"] = ["真实产品内容", "首屏产品感", "核心操作", "移动端可读性"]
    prototype_stage.recommendation_json = json.dumps(recommendation, ensure_ascii=False)
    prototype_stage.content = "\n".join([
        "当前已通过模型生成真实 HTML 原型，系统不再使用默认骨架模板兜底。",
        "",
        "本阶段重点检查：",
        "1. 首屏是否已经像你要做的那个产品。",
        "2. 页面内容、列表、卡片、按钮和状态是否是真实业务内容。",
        "3. 核心操作和移动端展示是否对路。",
        "",
        f"原型文件：{artifact.filename}",
    ])
    prototype_stage.status = WorkspaceStageStatus.AWAITING_CONFIRMATION
    workspace.current_stage = WorkspaceStageKey.PROTOTYPE
    workspace.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(prototype_stage)
    return WorkspaceStageResponse.from_model(prototype_stage)


@router.post("/workspaces/{workspace_id}/designs", response_model=WorkspaceStageResponse)
async def generate_workspace_designs(
    workspace_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    workspace, member = await _get_accessible_workspace(db, workspace_id, user)
    _require_editor(member)

    stages = await _get_stages(db, workspace_id)
    prototype_stage = next((stage for stage in stages if stage.stage_key == WorkspaceStageKey.PROTOTYPE), None)
    if not prototype_stage:
        raise HTTPException(status_code=404, detail="Prototype stage not found")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    recommendation = _load_recommendation(prototype_stage)
    try:
        provider_selection = await resolve_openai_image_provider(db, user.id)
        desktop_result = await generate_openai_image(
            provider_selection,
            _build_design_image_prompt(workspace, stages, "desktop"),
            size="1536x1024",
        )
        mobile_result = await generate_openai_image(
            provider_selection,
            _build_design_image_prompt(workspace, stages, "mobile"),
            size="1024x1536",
        )
        desktop_artifact = await _create_workspace_artifact(
            db=db,
            workspace=workspace,
            user=user,
            artifact_type="desktop_design",
            filename=f"desktop-design-{timestamp}.png",
            content=desktop_result.content,
            mime_type=desktop_result.mime_type,
        )
        mobile_artifact = await _create_workspace_artifact(
            db=db,
            workspace=workspace,
            user=user,
            artifact_type="mobile_design",
            filename=f"mobile-design-{timestamp}.png",
            content=mobile_result.content,
            mime_type=mobile_result.mime_type,
        )

        recommendation = _upsert_artifact_reference(recommendation, desktop_artifact, "桌面端设计稿")
        recommendation = _upsert_artifact_reference(recommendation, mobile_artifact, "移动端设计稿")
        recommendation["source"] = "design_generator_v3_image"
        recommendation["model"] = desktop_result.model
        recommendation["provider"] = desktop_result.provider
        recommendation["summary"] = f"已通过 {desktop_result.provider}/{desktop_result.model} 生成桌面端和移动端图片设计稿，可直接判断这个产品到底像不像你要的东西。"
    except Exception as exc:
        image_error = str(exc)
        try:
            desktop_html, desktop_model, desktop_provider = await _generate_llm_design_html(
                db=db,
                user=user,
                workspace=workspace,
                stages=stages,
                viewport="desktop",
            )
            mobile_html, mobile_model, _mobile_provider = await _generate_llm_design_html(
                db=db,
                user=user,
                workspace=workspace,
                stages=stages,
                viewport="mobile",
            )
        except HTTPException as html_exc:
            raise HTTPException(
                status_code=400,
                detail=f"图片设计稿生成失败：{image_error}；HTML 设计稿生成也失败：{html_exc.detail}",
            ) from html_exc

        desktop_artifact = await _create_workspace_artifact(
            db=db,
            workspace=workspace,
            user=user,
            artifact_type="desktop_design",
            filename=f"desktop-design-{timestamp}.html",
            content=desktop_html.encode("utf-8"),
            mime_type="text/html",
        )
        mobile_artifact = await _create_workspace_artifact(
            db=db,
            workspace=workspace,
            user=user,
            artifact_type="mobile_design",
            filename=f"mobile-design-{timestamp}.html",
            content=mobile_html.encode("utf-8"),
            mime_type="text/html",
        )
        recommendation = _upsert_artifact_reference(recommendation, desktop_artifact, "桌面端 HTML 设计稿")
        recommendation = _upsert_artifact_reference(recommendation, mobile_artifact, "移动端 HTML 设计稿")
        recommendation["source"] = "design_generator_llm_html_v1"
        recommendation["model"] = desktop_model
        recommendation["provider"] = desktop_provider
        recommendation["image_generation_error"] = image_error
        recommendation["summary"] = f"图片模型不可用，已改用 {desktop_provider}/{desktop_model} 生成桌面端和移动端 HTML 设计稿。"
    recommendation["recommended_action"] = "请查看设计稿。如果内容、布局和视觉气质已经像你要做的产品，就确认通过；否则提交更明确反馈后重新生成。"
    recommendation["focus"] = ["像不像目标产品", "真实界面内容感", "主操作是否明确", "桌面端与移动端一致性"]

    prototype_stage.recommendation_json = json.dumps(recommendation, ensure_ascii=False)
    prototype_stage.content = "\n".join([
        "当前已生成桌面端和移动端图片设计稿，重点是直接看这个产品长什么样，而不是继续看描述文字。",
        "",
        "本阶段重点检查：",
        "1. 内容是不是你的目标产品本身，而不是模板化页面。",
        "2. 列表、卡片、按钮、标题这些关键界面是否像真实产品。",
        "3. 桌面端和移动端是否都已经具备正确的信息结构和视觉气质。",
        "",
        f"桌面端设计稿：{desktop_artifact.filename}",
        f"移动端设计稿：{mobile_artifact.filename}",
    ])
    prototype_stage.status = WorkspaceStageStatus.AWAITING_CONFIRMATION
    workspace.current_stage = WorkspaceStageKey.PROTOTYPE
    workspace.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(prototype_stage)
    return WorkspaceStageResponse.from_model(prototype_stage)


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
        )
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
        recommendation, content = _generate_stage_artifact(
            workspace=workspace,
            stages=stages,
            stage=stage,
            instruction=req.instruction,
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
    if not _has_generated_recommendation(stage):
        raise HTTPException(status_code=400, detail="请先生成推荐方案，再确认通过当前阶段")

    recommendation = _load_recommendation(stage)
    selected_option = recommendation.get("selected_option")
    if isinstance(selected_option, str):
        selected = next(
            (item for item in recommendation.get("options", []) if isinstance(item, dict) and item.get("title") == selected_option),
            None,
        )
        if selected and isinstance(selected.get("content"), str) and selected.get("content").strip():
            stage.content = selected["content"].strip()

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
