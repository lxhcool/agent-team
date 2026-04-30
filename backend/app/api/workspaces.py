"""Workspace API endpoints."""

import hashlib
import html as html_lib
import json
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

router = APIRouter()
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


def _looks_like_todo_app(text: str) -> bool:
    lowered = (text or "").lower()
    keywords = ["todo", "todolist", "to-do", "待办", "任务清单", "任务列表"]
    return any(keyword in lowered for keyword in keywords)


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

    if _looks_like_todo_app(f"{workspace.name}\n{requirement}\n{product}"):
        return _build_todo_prototype_html(workspace, requirement)

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
    product_mode = _stage_selected_option(stages, WorkspaceStageKey.PRODUCT) or "标准转化路径"
    ui_mode = _stage_selected_option(stages, WorkspaceStageKey.UI_DIRECTION) or "专业清晰型"
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

    hero_title = "今日任务"
    hero_subtitle = "把重要事项收敛到一个清晰的主界面"
    primary_cta = "新建任务"
    secondary_cta = "查看全部"
    if "工具工作台" in product_mode:
        hero_title = "团队工作台"
        hero_subtitle = "待处理事项、优先级和进度一屏掌握"
        primary_cta = "创建任务"
        secondary_cta = "切换视图"
    elif "内容浏览" in product_mode:
        hero_title = "推荐内容"
        hero_subtitle = "先浏览重点内容，再进入详情和操作"
        primary_cta = "开始浏览"
        secondary_cta = "查看分类"

    task_lines = [
        ("今天要完成什么", "给用户一个清晰的主行动入口"),
        ("处理中的事项", "突出进度、提醒和当前状态"),
        ("历史与回顾", "保留查看记录和继续编辑能力"),
    ]

    if is_mobile:
        width, height = 390, 844
        title_size = 32
        margin = 22
        card_width = 346
        hero_lines = _wrap_text(hero_subtitle, 19, 3)
        mode_lines = _wrap_text(f"{product_mode} · {ui_mode}", 22, 3)
        requirement_lines = _wrap_text(requirement, 22, 3)
        return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <rect width="{width}" height="{height}" rx="34" fill="#F7F8FC"/>
  <rect x="18" y="18" width="354" height="808" rx="28" fill="#FFFFFF" stroke="#E5E7EB"/>
  <rect x="{margin}" y="34" width="{card_width}" height="54" rx="18" fill="{accent_soft}"/>
  <circle cx="55" cy="61" r="16" fill="{accent}"/>
  <text x="82" y="67" font-size="16" font-weight="700" fill="#172033" font-family="Inter, Arial, sans-serif">{_safe_text(workspace.name)}</text>
  <text x="{margin}" y="130" font-size="12" font-weight="700" fill="#4F46E5" font-family="Inter, Arial, sans-serif">移动端设计稿</text>
  <text x="{margin}" y="178" font-size="{title_size}" font-weight="800" fill="#172033" font-family="Inter, Arial, sans-serif">{_safe_text(hero_title)}</text>
  {_svg_text(hero_lines, margin, 218, 14, "#667085", 22)}
  <rect x="{margin}" y="286" width="{card_width}" height="148" rx="24" fill="{accent}"/>
  <text x="46" y="330" font-size="20" font-weight="760" fill="#FFFFFF" font-family="Inter, Arial, sans-serif">{_safe_text(primary_cta)}</text>
  <text x="46" y="360" font-size="13" fill="#E0E7FF" font-family="Inter, Arial, sans-serif">{html_lib.escape(task_lines[0][1])}</text>
  <rect x="238" y="314" width="84" height="92" rx="22" fill="#FFFFFF" opacity=".16"/>
  <rect x="{margin}" y="458" width="{card_width}" height="150" rx="22" fill="{surface}" stroke="#E5E7EB"/>
  <text x="46" y="498" font-size="16" font-weight="760" fill="#172033" font-family="Inter, Arial, sans-serif">界面方向</text>
  {_svg_text(mode_lines, 46, 530, 12, "#667085", 18)}
  <rect x="46" y="556" width="118" height="30" rx="15" fill="#FFFFFF"/>
  <text x="66" y="575" font-size="12" font-weight="700" fill="{accent}" font-family="Inter, Arial, sans-serif">{_safe_text(secondary_cta)}</text>
  <rect x="{margin}" y="632" width="{card_width}" height="150" rx="22" fill="#FFFFFF" stroke="#E5E7EB"/>
  <text x="46" y="672" font-size="16" font-weight="760" fill="#172033" font-family="Inter, Arial, sans-serif">待确认重点</text>
  {_svg_text(requirement_lines, 46, 706, 12, "#667085", 18)}
  <circle cx="56" cy="748" r="8" fill="{accent_soft}" stroke="{accent}"/>
  <circle cx="56" cy="772" r="8" fill="{accent_soft}" stroke="{accent}"/>
  <text x="74" y="752" font-size="12" fill="#172033" font-family="Inter, Arial, sans-serif">{html_lib.escape(task_lines[1][0])}</text>
  <text x="74" y="776" font-size="12" fill="#172033" font-family="Inter, Arial, sans-serif">{html_lib.escape(task_lines[2][0])}</text>
</svg>'''

    width, height = 1440, 1040
    mode_lines = _wrap_text(f"{product_mode} · {ui_mode}", 30, 2)
    requirement_lines = _wrap_text(requirement, 34, 3)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <rect width="{width}" height="{height}" fill="#F6F8FB"/>
  <rect x="84" y="54" width="1272" height="82" rx="24" fill="#FFFFFF" stroke="#E5E7EB"/>
  <circle cx="132" cy="95" r="22" fill="{accent}"/>
  <text x="170" y="104" font-size="22" font-weight="760" fill="#172033" font-family="Inter, Arial, sans-serif">{_safe_text(workspace.name)}</text>
  <text x="1030" y="101" font-size="16" fill="#667085" font-family="Inter, Arial, sans-serif">首页　功能　流程　关于</text>
  <text x="96" y="218" font-size="15" font-weight="760" fill="{accent}" font-family="Inter, Arial, sans-serif">桌面端设计稿 · AI 生成</text>
  <text x="96" y="302" font-size="64" font-weight="820" fill="#172033" font-family="Inter, Arial, sans-serif">{_safe_text(hero_title)}</text>
  {_svg_text(_wrap_text(hero_subtitle, 26, 2), 100, 354, 21, "#667085", 34)}
  <rect x="96" y="520" width="190" height="54" rx="14" fill="{accent}"/>
  <text x="134" y="554" font-size="17" font-weight="760" fill="#FFFFFF" font-family="Inter, Arial, sans-serif">{_safe_text(primary_cta)}</text>
  <rect x="304" y="520" width="168" height="54" rx="14" fill="#FFFFFF" stroke="#E5E7EB"/>
  <text x="346" y="554" font-size="17" font-weight="760" fill="#172033" font-family="Inter, Arial, sans-serif">{_safe_text(secondary_cta)}</text>
  <rect x="792" y="196" width="468" height="418" rx="28" fill="#FFFFFF" stroke="#E5E7EB"/>
  <rect x="830" y="238" width="392" height="74" rx="18" fill="{accent_soft}"/>
  <text x="864" y="284" font-size="20" font-weight="760" fill="{accent}" font-family="Inter, Arial, sans-serif">主界面预览</text>
  <rect x="830" y="340" width="392" height="74" rx="18" fill="{surface}" stroke="#E5E7EB"/>
  <text x="864" y="386" font-size="18" font-weight="720" fill="#172033" font-family="Inter, Arial, sans-serif">{html_lib.escape(task_lines[0][0])}</text>
  <text x="1084" y="386" font-size="13" fill="#667085" font-family="Inter, Arial, sans-serif">立即操作</text>
  <rect x="830" y="436" width="392" height="74" rx="18" fill="{surface}" stroke="#E5E7EB"/>
  <text x="864" y="482" font-size="18" font-weight="720" fill="#172033" font-family="Inter, Arial, sans-serif">{html_lib.escape(task_lines[1][0])}</text>
  <text x="1084" y="482" font-size="13" fill="#667085" font-family="Inter, Arial, sans-serif">状态清楚</text>
  <rect x="830" y="532" width="392" height="74" rx="18" fill="{surface}" stroke="#E5E7EB"/>
  <text x="864" y="578" font-size="18" font-weight="720" fill="#172033" font-family="Inter, Arial, sans-serif">{html_lib.escape(task_lines[2][0])}</text>
  <text x="1084" y="578" font-size="13" fill="#667085" font-family="Inter, Arial, sans-serif">可继续编辑</text>
  <rect x="96" y="700" width="390" height="228" rx="24" fill="#FFFFFF" stroke="#E5E7EB"/>
  <text x="128" y="750" font-size="22" font-weight="760" fill="#172033" font-family="Inter, Arial, sans-serif">页面模式</text>
  {_svg_text(mode_lines, 128, 792, 15, "#667085", 24)}
  <rect x="128" y="828" width="132" height="36" rx="18" fill="{accent_soft}"/>
  <text x="158" y="851" font-size="14" font-weight="700" fill="{accent}" font-family="Inter, Arial, sans-serif">已按推荐布局</text>
  <rect x="526" y="700" width="390" height="228" rx="24" fill="#FFFFFF" stroke="#E5E7EB"/>
  <text x="558" y="750" font-size="22" font-weight="760" fill="#172033" font-family="Inter, Arial, sans-serif">用户需要确认</text>
  {_svg_text(requirement_lines, 558, 792, 15, "#667085", 24)}
  <rect x="956" y="700" width="300" height="228" rx="24" fill="#FFFFFF" stroke="#E5E7EB"/>
  <text x="988" y="750" font-size="22" font-weight="760" fill="#172033" font-family="Inter, Arial, sans-serif">确认点</text>
  <text x="988" y="798" font-size="15" fill="#667085" font-family="Inter, Arial, sans-serif">主入口是否清楚</text>
  <text x="988" y="832" font-size="15" fill="#667085" font-family="Inter, Arial, sans-serif">信息层级是否易懂</text>
  <text x="988" y="866" font-size="15" fill="#667085" font-family="Inter, Arial, sans-serif">视觉方向是否符合预期</text>
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
        ],
        WorkspaceStageKey.PRODUCT: [
            "输出页面列表、首页结构、主流程、关键状态和 P0/P1/P2 优先级。",
            "不同方案必须体现结构差异，不只是换说法。",
        ],
        WorkspaceStageKey.UI_DIRECTION: [
            "输出 2-3 个用户能理解的界面方向，并说明推荐理由。",
            "每个方向都要明确影响布局、信息层级、按钮强调和页面气质。",
        ],
        WorkspaceStageKey.PROTOTYPE: [
            "输出必须面向真实页面原型，不要把需求说明直接铺在页面上。",
            "优先提供可确认的页面结构、关键操作区、状态反馈和预览产物说明。",
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
    model, provider, fallback_chain = await _resolve_generation_model(db, user)
    if not model or not provider:
        return None

    await llm_router.load_providers(db, user_id=user.id)
    agent_name, agent_prompt = await _resolve_workspace_stage_agent(db, stage.stage_key)
    system_prompt = f"{agent_prompt}\n\n{_stage_generation_contract(stage.stage_key)}"

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
            agent_name=agent_name,
        )
        recommendation, content = _sanitize_llm_artifact(_parse_json_object(result.content))
        recommendation["source"] = "llm"
        recommendation["model"] = result.model
        recommendation["provider"] = result.provider
        recommendation["agent_name"] = agent_name
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
        mvp_content = "\n".join([
            f"项目：{workspace.name}",
            f"目标类型：{platform_label}",
            "",
            "当前建议采用小步快跑 MVP：",
            "1. 先锁定 1 类最核心用户，不同时满足太多人群。",
            "2. 第一版只解决 1 个最强痛点，例如记录、提醒、完成状态。",
            "3. 页面先保留最短闭环：列表、创建、编辑、完成。",
            "4. 暂缓复杂权限、支付、运营活动和高级统计。",
            "",
            "本阶段需要用户确认：",
            "- 目标用户是谁",
            "- 核心问题是什么",
            "- 第一版必须有哪些功能",
            "- 哪些内容暂缓",
        ])
        full_content = "\n".join([
            f"项目：{workspace.name}",
            f"目标类型：{platform_label}",
            "",
            "当前建议采用完整业务版：",
            "1. 在第一版就覆盖更多角色、更多流程和更多页面。",
            "2. 除核心闭环外，同时纳入分类、筛选、协作、统计等能力。",
            "3. 适合已经很清楚业务边界，且短期内就要面向更多用户验证。",
            "4. 风险是讨论时间更长，后续产品和开发成本更高。",
            "",
            "本阶段需要用户确认：",
            "- 是否真的要首版覆盖更多业务流程",
            "- 哪些复杂能力是必须同步上线的",
            "- 是否接受更长的设计与开发周期",
        ])
        recommendation = {
            **base,
            "summary": f"建议先把「{workspace.name}」定义为一个面向明确用户场景的{platform_label}，第一版只保留最核心闭环。",
            "recommended_action": "确认目标用户、核心场景和 MVP 范围；不清楚的商业化、复杂权限和高级运营能力先放到后续版本。",
            "focus": ["目标用户", "核心问题", "MVP 范围", "暂缓事项"],
            "options": [
                _make_option("小步快跑 MVP", "优先做一个可演示、可测试、能收集反馈的版本。", mvp_content, True),
                _make_option("完整业务版", "一次性覆盖更多业务流程，但设计和开发周期更长。", full_content, False),
            ],
            "selected_option": "小步快跑 MVP",
        }
        recommendation, content = _finalize_recommendation(recommendation, mvp_content)
        return recommendation, content

    if stage.stage_key == WorkspaceStageKey.PRODUCT:
        standard_content = "\n".join([
            "产品方案草稿：标准转化路径",
            "1. 首页/入口：说明产品价值，并引导用户开始主流程。",
            "2. 创建/提交页：让用户快速输入任务或需求。",
            "3. 结果/列表页：展示待办状态、完成情况和关键反馈。",
            "4. 设置页：管理偏好和基础信息。",
            "",
            "优先级建议：",
            "- P0：创建、编辑、完成、查看状态",
            "- P1：筛选、提醒、历史记录",
            "- P2：协作、统计、运营能力",
        ])
        workspace_content = "\n".join([
            "产品方案草稿：工具工作台路径",
            "1. 工作台首页：进入即看到今天任务、待处理项和快捷入口。",
            "2. 列表区域：按状态、优先级或负责人组织任务。",
            "3. 详情抽屉/面板：快速编辑任务内容和附加信息。",
            "4. 设置/成员管理：管理通知、账号和协作规则。",
            "",
            "优先级建议：",
            "- P0：工作台总览、任务列表、详情编辑",
            "- P1：筛选排序、批量操作、通知",
            "- P2：分析看板、复杂权限、自动化",
        ])
        browse_content = "\n".join([
            "产品方案草稿：内容浏览路径",
            "1. 首页：先展示推荐内容或任务集合，降低首次操作门槛。",
            "2. 分类/频道页：帮助用户浏览不同主题。",
            "3. 详情页：查看内容详情并触发核心动作。",
            "4. 个人页：查看历史记录、收藏和个人设置。",
            "",
            "优先级建议：",
            "- P0：内容浏览、详情查看、核心动作",
            "- P1：收藏、搜索、历史记录",
            "- P2：运营活动、个性化推荐、复杂会员能力",
        ])
        recommendation = {
            **base,
            "summary": "建议把产品方案拆成首页/核心操作/结果反馈/设置或管理四类页面，先打通主流程。",
            "recommended_action": "先确认页面列表和用户主路径，再进入 UI 方向选择。",
            "focus": ["页面列表", "主流程", "功能优先级", "异常状态"],
            "options": [
                _make_option("标准转化路径", "首页说明价值，用户提交需求，系统展示结果。", standard_content, True),
                _make_option("工具工作台路径", "进入后直接看到任务、数据和操作入口。", workspace_content, workspace.target_platform == "dashboard"),
                _make_option("内容浏览路径", "更适合内容、电商、本地生活类项目。", browse_content, workspace.target_platform == "miniapp"),
            ],
        }
        recommendation, content = _finalize_recommendation(recommendation, standard_content)
        return recommendation, content

    if stage.stage_key == WorkspaceStageKey.UI_DIRECTION:
        professional_content = "\n".join([
            "UI 方向：专业清晰型",
            "1. 视觉基调：留白明确、结构稳定、强调信息层级。",
            "2. 色彩建议：低饱和中性色 + 少量强调色，建立可信感。",
            "3. 组件密度：中等偏高，优先保证信息清楚和操作效率。",
            "4. 适用场景：SaaS、工具、管理后台、偏理性决策产品。",
            "",
            "下一步建议生成：",
            "- 桌面端关键页面图",
            "- 移动端适配图",
            "- 表单、列表、状态提示的视觉规范",
        ])
        friendly_content = "\n".join([
            "UI 方向：轻快亲和型",
            "1. 视觉基调：更明亮、更直接，降低使用门槛。",
            "2. 色彩建议：高一点的色彩对比，按钮和引导更明显。",
            "3. 组件密度：偏轻，突出主要操作，减少认知压力。",
            "4. 适用场景：消费产品、小程序、本地生活、轻任务工具。",
            "",
            "下一步建议生成：",
            "- 首页和核心操作页的轻量视觉图",
            "- 更明显的移动端 CTA 和卡片布局",
            "- 更友好的空状态、引导和反馈样式",
        ])
        brand_content = "\n".join([
            "UI 方向：高级品牌型",
            "1. 视觉基调：强调质感、品牌气质和视觉记忆点。",
            "2. 色彩建议：更强的品牌主色和对比关系，配合大图或高级排版。",
            "3. 组件密度：偏低，重点突出内容和视觉冲击。",
            "4. 适用场景：官网、作品集、高客单价服务展示页。",
            "",
            "下一步建议生成：",
            "- 品牌首页视觉稿",
            "- 更强氛围感的首屏与详情页",
            "- 关键营销区块的高保真设计图",
        ])
        recommendation = {
            **base,
            "summary": "建议先给用户 3 个可理解的 UI 方向，不让小白用户直接面对设计术语。",
            "recommended_action": "推荐先采用专业清晰型；如果目标用户偏消费端，再选择轻快亲和型。",
            "focus": ["视觉方向", "参考图", "色彩倾向", "组件密度"],
            "options": [
                _make_option("专业清晰型", "适合 SaaS、工具、管理后台，强调可信、清楚和效率。", professional_content, workspace.target_platform in {"website", "dashboard"}),
                _make_option("轻快亲和型", "适合小程序、消费产品和本地生活，颜色更明亮，引导更直接。", friendly_content, workspace.target_platform == "miniapp"),
                _make_option("高级品牌型", "适合官网、作品集和高客单价服务，强调质感和视觉冲击。", brand_content, False),
            ],
            "artifacts": [
                {"type": "concept_image", "status": "pending", "label": "概念风格图"},
                {"type": "reference_image", "status": "pending", "label": "用户参考图分析"},
            ],
        }
        recommendation, content = _finalize_recommendation(recommendation, professional_content)
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
        "当前已生成可运行的 HTML 原型，供你直接确认页面结构和主要操作入口。",
        "",
        "本阶段重点检查：",
        "1. 首屏是否一眼看懂产品要做什么。",
        "2. 主按钮和核心操作是否足够明显。",
        "3. 页面结构是否已经接近你想要的真实产品，而不只是文字描述。",
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
        "当前已生成桌面端和移动端设计稿，重点是让你判断页面长相，而不是继续看需求描述。",
        "",
        "本阶段重点检查：",
        "1. 视觉风格是不是你想要的方向。",
        "2. 列表、卡片、按钮这些关键界面是否像真实产品。",
        "3. 移动端布局是否清楚、按钮是否足够明显。",
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
