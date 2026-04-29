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
    ModelSettings,
    ProviderConfig,
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


class GenerateStageRequest(BaseModel):
    instruction: Optional[str] = None


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


def _stage_context(workspace: Workspace, stages: List[WorkspaceStage]) -> Dict[str, str]:
    context = {
        "workspace_name": workspace.name,
        "target_platform": workspace.target_platform,
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

    sanitized = {
        "summary": str(recommendation.get("summary") or "AI 团队已生成本阶段推荐方案。"),
        "recommended_action": str(recommendation.get("recommended_action") or "请确认本阶段方案，或提交反馈让 AI 重新调整。"),
        "focus": recommendation.get("focus") if isinstance(recommendation.get("focus"), list) else [],
        "options": recommendation.get("options") if isinstance(recommendation.get("options"), list) else [],
        "artifacts": recommendation.get("artifacts") if isinstance(recommendation.get("artifacts"), list) else [],
    }
    if not content:
        content = "AI 已生成推荐，但没有返回详细产物。请提交反馈后重新生成。"
    return sanitized, content


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


def _build_stage_generation_prompt(
    workspace: Workspace,
    stages: List[WorkspaceStage],
    stage: WorkspaceStage,
    instruction: Optional[str],
) -> str:
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
        "known_stage_outputs": stage_context,
    }
    return _json_dumps(payload)


async def _generate_stage_artifact_with_llm(
    db: AsyncSession,
    user: User,
    workspace: Workspace,
    stages: List[WorkspaceStage],
    stage: WorkspaceStage,
    instruction: Optional[str],
) -> Optional[tuple[Dict[str, Any], str]]:
    model, provider, fallback_chain = await _resolve_generation_model(db, user)
    if not model or not provider:
        return None

    await llm_router.load_providers(db, user_id=user.id)

    system_prompt = """
你是一个 AI 产品开发团队的项目负责人，负责把普通用户的想法推进为可确认的阶段产物。
你的输出面向完全不懂代码的用户，所以必须具体、可选择、可确认。

请只返回 JSON，不要 Markdown，不要代码块。JSON 格式：
{
  "recommendation": {
    "summary": "一句话说明本阶段建议",
    "recommended_action": "用户下一步应该怎么确认",
    "focus": ["确认点1", "确认点2"],
    "options": [
      {"title": "方案名", "description": "给小白用户看的解释", "recommended": true}
    ],
    "artifacts": [
      {"type": "concept_image|desktop_screenshot|mobile_screenshot|vision_review|document", "status": "pending", "label": "产物名称"}
    ]
  },
  "content": "本阶段给用户看的详细产物，使用中文，可包含编号列表"
}

阶段规则：
- 需求确认：帮用户收敛目标用户、核心问题、MVP 范围、暂缓事项。
- 产品方案：给页面列表、用户主流程、功能优先级。
- UI 方向：给 2-3 个小白能理解的视觉方向，并说明推荐理由；要保留概念图/参考图分析产物。
- 原型确认：强调真实页面截图和多模态 UI 审查，不要只承诺漂亮效果图。
- 技术方案：给技术栈、数据隔离、执行边界、部署测试建议。
- 开发执行：给 checkpoint、代码修改、检查、预览和回滚流程。
- 预览验收：给用户验收清单。
- 部署测试：给测试服务器发布、日志和回滚计划。
""".strip()

    try:
        result = await llm_router.call(
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
            max_tokens=2200,
            temperature=0.35,
            session_id=workspace.id,
            session_type="workspace",
            agent_name="workspace_stage_generator",
        )
        recommendation, content = _sanitize_llm_artifact(_parse_json_object(result.content))
        recommendation["source"] = "llm"
        recommendation["model"] = result.model
        recommendation["provider"] = result.provider
        return recommendation, content
    except (LLMError, json.JSONDecodeError, KeyError, TypeError, ValueError):
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

    if stage.stage_key == WorkspaceStageKey.REQUIREMENTS:
        recommendation = {
            **base,
            "summary": f"建议先把「{workspace.name}」定义为一个面向明确用户场景的{platform_label}，第一版只保留最核心闭环。",
            "recommended_action": "确认目标用户、核心场景和 MVP 范围；不清楚的商业化、复杂权限和高级运营能力先放到后续版本。",
            "focus": ["目标用户", "核心问题", "MVP 范围", "暂缓事项"],
            "options": [
                {
                    "title": "小步快跑 MVP",
                    "description": "优先做一个可演示、可测试、能收集反馈的版本。",
                    "recommended": True,
                },
                {
                    "title": "完整业务版",
                    "description": "一次性覆盖更多业务流程，但设计和开发周期更长。",
                    "recommended": False,
                },
            ],
        }
        content = "\n".join([
            f"项目：{workspace.name}",
            f"目标类型：{platform_label}",
            "",
            "需求理解：",
            requirement,
            "",
            "建议确认：",
            "1. 主要用户是谁？",
            "2. 用户最想解决的一个问题是什么？",
            "3. 第一版必须包含哪些页面和功能？",
            "4. 哪些能力可以先不做？",
        ])
        return recommendation, content

    if stage.stage_key == WorkspaceStageKey.PRODUCT:
        recommendation = {
            **base,
            "summary": "建议把产品方案拆成首页/核心操作/结果反馈/设置或管理四类页面，先打通主流程。",
            "recommended_action": "先确认页面列表和用户主路径，再进入 UI 方向选择。",
            "focus": ["页面列表", "主流程", "功能优先级", "异常状态"],
            "options": [
                {"title": "标准转化路径", "description": "首页说明价值，用户提交需求，系统展示结果。", "recommended": True},
                {"title": "工具工作台路径", "description": "进入后直接看到任务、数据和操作入口。", "recommended": workspace.target_platform == "dashboard"},
                {"title": "内容浏览路径", "description": "更适合内容、电商、本地生活类项目。", "recommended": workspace.target_platform == "miniapp"},
            ],
        }
        content = "\n".join([
            "产品方案草稿：",
            "1. 首页/入口：说明产品价值，并引导用户开始主流程。",
            "2. 核心页面：承载用户最主要的操作。",
            "3. 结果页：展示系统输出、订单、任务或内容状态。",
            "4. 设置/个人中心：管理账号、偏好和基础信息。",
            "",
            "第一版优先级：",
            "- P0：用户能完成核心闭环。",
            "- P1：补充筛选、历史记录、状态提示。",
            "- P2：运营、统计、复杂权限和自动化能力。",
        ])
        return recommendation, content

    if stage.stage_key == WorkspaceStageKey.UI_DIRECTION:
        recommendation = {
            **base,
            "summary": "建议先给用户 3 个可理解的 UI 方向，不让小白用户直接面对设计术语。",
            "recommended_action": "推荐先采用专业清晰型；如果目标用户偏消费端，再选择轻快亲和型。",
            "focus": ["视觉方向", "参考图", "色彩倾向", "组件密度"],
            "options": [
                {
                    "title": "专业清晰型",
                    "description": "适合 SaaS、工具、管理后台，强调可信、清楚和效率。",
                    "recommended": workspace.target_platform in {"website", "dashboard"},
                },
                {
                    "title": "轻快亲和型",
                    "description": "适合小程序、消费产品和本地生活，颜色更明亮，引导更直接。",
                    "recommended": workspace.target_platform == "miniapp",
                },
                {
                    "title": "高级品牌型",
                    "description": "适合官网、作品集和高客单价服务，强调质感和视觉冲击。",
                    "recommended": False,
                },
            ],
            "artifacts": [
                {"type": "concept_image", "status": "pending", "label": "概念风格图"},
                {"type": "reference_image", "status": "pending", "label": "用户参考图分析"},
            ],
        }
        content = "\n".join([
            "UI 方向建议：",
            "推荐方案：专业清晰型",
            "",
            "理由：",
            "1. 小白用户更容易判断页面是否清楚、可信。",
            "2. 后续真实页面截图更容易和代码落地保持一致。",
            "3. 可以在确认后再生成概念图和页面截图。",
            "",
            "下一步需要用户确认：选择一个方向，或上传参考图让多模态模型分析。",
        ])
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
                {"title": "本地优先", "description": "适合当前测试阶段，成本低，对服务器压力小。", "recommended": True},
                {"title": "云端执行", "description": "便于多人协作，但服务器成本和安全边界要求更高。", "recommended": False},
            ],
        }
        content = "\n".join([
            "技术方案草稿：",
            f"- 目标类型：{platform_label}",
            "- 数据隔离：user_id + workspace_id 双层隔离。",
            "- 项目目录：每个工作区一个独立代码目录。",
            "- 修改保护：每次开发执行前创建 checkpoint。",
            "- 预览方式：本地启动预览服务，截图给用户确认。",
            "- 部署测试：优先发布到用户自己的测试服务器或临时预览地址。",
        ])
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
