"""Workspace API endpoints."""

import hashlib
import html as html_lib
import json
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
from app.models.models import (
    Artifact,
    ModelSettings,
    PlanningSession,
    PlanningStatus,
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
optional_bearer = HTTPBearer(auto_error=False)


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


def _safe_text(value: Optional[str], fallback: str = "") -> str:
    return html_lib.escape(value or fallback)


def _stage_content(stages: List[WorkspaceStage], key: WorkspaceStageKey) -> str:
    for stage in stages:
        if stage.stage_key == key and stage.content:
            return stage.content
    return ""


def _paragraphs(value: str) -> str:
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    if not lines:
        return "<p>等待补充。</p>"
    return "\n".join(f"<p>{html_lib.escape(line)}</p>" for line in lines[:12])


def _build_prototype_html(workspace: Workspace, stages: List[WorkspaceStage]) -> str:
    requirement = _stage_content(stages, WorkspaceStageKey.REQUIREMENTS) or workspace.description or ""
    product = _stage_content(stages, WorkspaceStageKey.PRODUCT)
    ui_direction = _stage_content(stages, WorkspaceStageKey.UI_DIRECTION)
    technical = _stage_content(stages, WorkspaceStageKey.TECHNICAL)
    platform_label = {
        "website": "网站",
        "miniapp": "小程序",
        "dashboard": "管理后台",
        "app": "应用",
    }.get(workspace.target_platform, workspace.target_platform)

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{_safe_text(workspace.name)} - Prototype</title>
  <style>
    :root {{
      color-scheme: light;
      --ink: #172033;
      --muted: #667085;
      --line: #e5e7eb;
      --bg: #f6f8fb;
      --card: #ffffff;
      --accent: #4f46e5;
      --accent-soft: #eef2ff;
      --good: #0f9f6e;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--ink);
      line-height: 1.5;
    }}
    .shell {{ min-height: 100vh; display: flex; flex-direction: column; }}
    header {{
      height: 64px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 0 32px;
      border-bottom: 1px solid var(--line);
      background: rgba(255,255,255,.88);
      backdrop-filter: blur(16px);
      position: sticky;
      top: 0;
      z-index: 10;
    }}
    .brand {{ display: flex; align-items: center; gap: 12px; font-weight: 760; }}
    .mark {{ width: 34px; height: 34px; border-radius: 10px; background: var(--accent); display: grid; place-items: center; color: #fff; }}
    nav {{ display: flex; gap: 20px; color: var(--muted); font-size: 14px; }}
    .hero {{
      padding: 58px 32px 30px;
      display: grid;
      grid-template-columns: minmax(0, 1.05fr) minmax(320px, .95fr);
      gap: 32px;
      max-width: 1180px;
      width: 100%;
      margin: 0 auto;
    }}
    .eyebrow {{ color: var(--accent); font-size: 13px; font-weight: 720; margin-bottom: 12px; }}
    h1 {{ font-size: clamp(34px, 5vw, 60px); line-height: 1.03; margin: 0; letter-spacing: 0; }}
    .lead {{ margin-top: 18px; max-width: 640px; color: var(--muted); font-size: 17px; }}
    .actions {{ display: flex; gap: 12px; margin-top: 26px; flex-wrap: wrap; }}
    button {{ border: 0; border-radius: 10px; padding: 12px 16px; font-weight: 700; cursor: default; }}
    .primary {{ background: var(--accent); color: white; }}
    .secondary {{ background: white; color: var(--ink); border: 1px solid var(--line); }}
    .panel {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 18px;
      box-shadow: 0 18px 40px rgba(23, 32, 51, .08);
      overflow: hidden;
    }}
    .panel-head {{ padding: 18px 20px; border-bottom: 1px solid var(--line); display: flex; justify-content: space-between; align-items: center; }}
    .status {{ color: var(--good); background: #ecfdf5; padding: 5px 9px; border-radius: 999px; font-size: 12px; font-weight: 720; }}
    .panel-body {{ padding: 20px; display: grid; gap: 14px; }}
    .row {{ display: flex; gap: 12px; align-items: flex-start; padding: 14px; border: 1px solid var(--line); border-radius: 14px; }}
    .num {{ width: 28px; height: 28px; border-radius: 8px; background: var(--accent-soft); color: var(--accent); display: grid; place-items: center; font-weight: 760; flex: 0 0 auto; }}
    .row-title {{ font-weight: 720; margin-bottom: 4px; }}
    .row-text {{ color: var(--muted); font-size: 13px; }}
    .sections {{ max-width: 1180px; width: 100%; margin: 0 auto; padding: 16px 32px 54px; display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }}
    .card {{ background: white; border: 1px solid var(--line); border-radius: 16px; padding: 20px; min-height: 210px; }}
    .card h2 {{ margin: 0 0 12px; font-size: 18px; }}
    .card p {{ margin: 0 0 9px; color: var(--muted); font-size: 13px; }}
    footer {{ padding: 22px 32px; text-align: center; color: var(--muted); font-size: 12px; border-top: 1px solid var(--line); background: white; }}
    @media (max-width: 820px) {{
      header {{ padding: 0 18px; }}
      nav {{ display: none; }}
      .hero {{ grid-template-columns: 1fr; padding: 34px 18px 20px; }}
      .sections {{ grid-template-columns: 1fr; padding: 10px 18px 34px; }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <header>
      <div class="brand"><div class="mark">AI</div><span>{_safe_text(workspace.name)}</span></div>
      <nav><span>首页</span><span>功能</span><span>流程</span><span>关于</span></nav>
    </header>
    <main>
      <section class="hero">
        <div>
          <div class="eyebrow">{_safe_text(platform_label)} · AI 生成原型</div>
          <h1>{_safe_text(workspace.name)}</h1>
          <p class="lead">{_safe_text(requirement, "这是根据当前工作区阶段产物生成的真实 HTML 原型，用于确认信息结构和视觉方向。")}</p>
          <div class="actions">
            <button class="primary">开始体验</button>
            <button class="secondary">查看方案</button>
          </div>
        </div>
        <div class="panel">
          <div class="panel-head"><strong>核心流程</strong><span class="status">可预览</span></div>
          <div class="panel-body">
            <div class="row"><div class="num">1</div><div><div class="row-title">理解需求</div><div class="row-text">确认用户、场景和第一版范围。</div></div></div>
            <div class="row"><div class="num">2</div><div><div class="row-title">选择方案</div><div class="row-text">对产品结构和 UI 方向做确认。</div></div></div>
            <div class="row"><div class="num">3</div><div><div class="row-title">预览验收</div><div class="row-text">查看真实页面并继续调整。</div></div></div>
          </div>
        </div>
      </section>
      <section class="sections">
        <article class="card"><h2>产品方案</h2>{_paragraphs(product)}</article>
        <article class="card"><h2>UI 方向</h2>{_paragraphs(ui_direction)}</article>
        <article class="card"><h2>技术约束</h2>{_paragraphs(technical)}</article>
      </section>
    </main>
    <footer>Generated by Team Agent workspace prototype flow</footer>
  </div>
</body>
</html>
"""


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


def _build_design_svg(workspace: Workspace, stages: List[WorkspaceStage], viewport: str) -> str:
    requirement = _stage_content(stages, WorkspaceStageKey.REQUIREMENTS) or workspace.description or "请补充项目需求"
    product = _stage_content(stages, WorkspaceStageKey.PRODUCT) or "核心页面、用户主流程和功能优先级将在这里呈现。"
    ui_direction = _stage_content(stages, WorkspaceStageKey.UI_DIRECTION) or "建议先确认专业清晰型、轻快亲和型或高级品牌型。"
    is_mobile = viewport == "mobile"

    if is_mobile:
        width, height = 390, 844
        title_size = 32
        margin = 22
        card_width = 346
        hero_lines = _wrap_text(requirement, 19, 4)
        product_lines = _wrap_text(product, 22, 5)
        ui_lines = _wrap_text(ui_direction, 22, 4)
        return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <rect width="{width}" height="{height}" rx="34" fill="#F7F8FC"/>
  <rect x="18" y="18" width="354" height="808" rx="28" fill="#FFFFFF" stroke="#E5E7EB"/>
  <rect x="{margin}" y="34" width="{card_width}" height="54" rx="18" fill="#EEF2FF"/>
  <circle cx="55" cy="61" r="16" fill="#4F46E5"/>
  <text x="82" y="67" font-size="16" font-weight="700" fill="#172033" font-family="Inter, Arial, sans-serif">{_safe_text(workspace.name)}</text>
  <text x="{margin}" y="130" font-size="12" font-weight="700" fill="#4F46E5" font-family="Inter, Arial, sans-serif">移动端设计稿</text>
  <text x="{margin}" y="178" font-size="{title_size}" font-weight="800" fill="#172033" font-family="Inter, Arial, sans-serif">{_safe_text(workspace.name)}</text>
  {_svg_text(hero_lines, margin, 218, 14, "#667085", 22)}
  <rect x="{margin}" y="328" width="{card_width}" height="132" rx="22" fill="#4F46E5"/>
  <text x="46" y="368" font-size="18" font-weight="760" fill="#FFFFFF" font-family="Inter, Arial, sans-serif">开始体验</text>
  <text x="46" y="397" font-size="13" fill="#E0E7FF" font-family="Inter, Arial, sans-serif">清晰的主行动入口</text>
  <rect x="236" y="356" width="84" height="84" rx="20" fill="#FFFFFF" opacity=".16"/>
  <rect x="{margin}" y="488" width="{card_width}" height="144" rx="22" fill="#F8FAFC" stroke="#E5E7EB"/>
  <text x="46" y="526" font-size="16" font-weight="760" fill="#172033" font-family="Inter, Arial, sans-serif">产品结构</text>
  {_svg_text(product_lines, 46, 558, 12, "#667085", 18)}
  <rect x="{margin}" y="654" width="{card_width}" height="128" rx="22" fill="#F8FAFC" stroke="#E5E7EB"/>
  <text x="46" y="692" font-size="16" font-weight="760" fill="#172033" font-family="Inter, Arial, sans-serif">UI 方向</text>
  {_svg_text(ui_lines, 46, 724, 12, "#667085", 18)}
</svg>'''

    width, height = 1440, 1040
    requirement_lines = _wrap_text(requirement, 38, 4)
    product_lines = _wrap_text(product, 34, 6)
    ui_lines = _wrap_text(ui_direction, 34, 6)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <rect width="{width}" height="{height}" fill="#F6F8FB"/>
  <rect x="84" y="54" width="1272" height="82" rx="24" fill="#FFFFFF" stroke="#E5E7EB"/>
  <circle cx="132" cy="95" r="22" fill="#4F46E5"/>
  <text x="170" y="104" font-size="22" font-weight="760" fill="#172033" font-family="Inter, Arial, sans-serif">{_safe_text(workspace.name)}</text>
  <text x="1030" y="101" font-size="16" fill="#667085" font-family="Inter, Arial, sans-serif">首页　功能　流程　关于</text>
  <text x="96" y="218" font-size="15" font-weight="760" fill="#4F46E5" font-family="Inter, Arial, sans-serif">桌面端设计稿 · AI 生成</text>
  <text x="96" y="302" font-size="64" font-weight="820" fill="#172033" font-family="Inter, Arial, sans-serif">{_safe_text(workspace.name)}</text>
  {_svg_text(requirement_lines, 100, 354, 21, "#667085", 34)}
  <rect x="96" y="520" width="190" height="54" rx="14" fill="#4F46E5"/>
  <text x="134" y="554" font-size="17" font-weight="760" fill="#FFFFFF" font-family="Inter, Arial, sans-serif">开始体验</text>
  <rect x="304" y="520" width="168" height="54" rx="14" fill="#FFFFFF" stroke="#E5E7EB"/>
  <text x="346" y="554" font-size="17" font-weight="760" fill="#172033" font-family="Inter, Arial, sans-serif">查看方案</text>
  <rect x="792" y="196" width="468" height="418" rx="28" fill="#FFFFFF" stroke="#E5E7EB"/>
  <rect x="830" y="238" width="392" height="74" rx="18" fill="#EEF2FF"/>
  <text x="864" y="284" font-size="20" font-weight="760" fill="#4F46E5" font-family="Inter, Arial, sans-serif">核心流程</text>
  <rect x="830" y="340" width="392" height="74" rx="18" fill="#F8FAFC" stroke="#E5E7EB"/>
  <text x="864" y="386" font-size="18" font-weight="720" fill="#172033" font-family="Inter, Arial, sans-serif">1. 理解需求</text>
  <rect x="830" y="436" width="392" height="74" rx="18" fill="#F8FAFC" stroke="#E5E7EB"/>
  <text x="864" y="482" font-size="18" font-weight="720" fill="#172033" font-family="Inter, Arial, sans-serif">2. 选择方案</text>
  <rect x="830" y="532" width="392" height="74" rx="18" fill="#F8FAFC" stroke="#E5E7EB"/>
  <text x="864" y="578" font-size="18" font-weight="720" fill="#172033" font-family="Inter, Arial, sans-serif">3. 预览验收</text>
  <rect x="96" y="700" width="390" height="228" rx="24" fill="#FFFFFF" stroke="#E5E7EB"/>
  <text x="128" y="750" font-size="22" font-weight="760" fill="#172033" font-family="Inter, Arial, sans-serif">产品方案</text>
  {_svg_text(product_lines, 128, 792, 15, "#667085", 24)}
  <rect x="526" y="700" width="390" height="228" rx="24" fill="#FFFFFF" stroke="#E5E7EB"/>
  <text x="558" y="750" font-size="22" font-weight="760" fill="#172033" font-family="Inter, Arial, sans-serif">UI 方向</text>
  {_svg_text(ui_lines, 558, 792, 15, "#667085", 24)}
  <rect x="956" y="700" width="300" height="228" rx="24" fill="#FFFFFF" stroke="#E5E7EB"/>
  <text x="988" y="750" font-size="22" font-weight="760" fill="#172033" font-family="Inter, Arial, sans-serif">确认点</text>
  <text x="988" y="798" font-size="15" fill="#667085" font-family="Inter, Arial, sans-serif">真实页面可落地</text>
  <text x="988" y="832" font-size="15" fill="#667085" font-family="Inter, Arial, sans-serif">支持继续调整</text>
  <text x="988" y="866" font-size="15" fill="#667085" font-family="Inter, Arial, sans-serif">进入代码开发前确认</text>
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

    html_content = _build_prototype_html(workspace, stages).encode("utf-8")
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
    recommendation["source"] = "prototype_generator_v1"
    prototype_stage.recommendation_json = json.dumps(recommendation, ensure_ascii=False)
    prototype_stage.content = "\n".join([
        "已生成真实 HTML 原型。",
        f"文件：{artifact.filename}",
        "你可以在右侧预览区域查看页面长相；后续会继续接入桌面/移动端截图和多模态 UI 审查。",
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
    desktop_svg = _build_design_svg(workspace, stages, "desktop").encode("utf-8")
    mobile_svg = _build_design_svg(workspace, stages, "mobile").encode("utf-8")

    desktop_artifact = await _create_workspace_artifact(
        db=db,
        workspace=workspace,
        user=user,
        artifact_type="desktop_design",
        filename=f"desktop-design-{timestamp}.svg",
        content=desktop_svg,
        mime_type="image/svg+xml",
    )
    mobile_artifact = await _create_workspace_artifact(
        db=db,
        workspace=workspace,
        user=user,
        artifact_type="mobile_design",
        filename=f"mobile-design-{timestamp}.svg",
        content=mobile_svg,
        mime_type="image/svg+xml",
    )

    recommendation = _load_recommendation(prototype_stage)
    recommendation = _upsert_artifact_reference(recommendation, desktop_artifact, "桌面端设计稿")
    recommendation = _upsert_artifact_reference(recommendation, mobile_artifact, "移动端设计稿")
    recommendation["source"] = "design_generator_v1"
    recommendation["summary"] = "已生成桌面端和移动端设计稿，可作为用户确认 UI 的视觉依据。"
    recommendation["recommended_action"] = "请查看设计稿。如果视觉方向、信息结构和移动端布局可以接受，就确认通过；否则提交反馈后重新生成。"

    prototype_stage.recommendation_json = json.dumps(recommendation, ensure_ascii=False)
    prototype_stage.content = "\n".join([
        "已生成设计稿。",
        f"桌面端：{desktop_artifact.filename}",
        f"移动端：{mobile_artifact.filename}",
        "设计稿用于给用户确认视觉方向；HTML 原型仍作为后续代码落地基础。",
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
