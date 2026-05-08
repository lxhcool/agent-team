"""Artifact and document generation helpers for staged flows."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.models import Artifact, User, Workspace, WorkspaceStage, WorkspaceStageKey, WorkspaceStageStatus


async def create_workspace_artifact(
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


def load_recommendation(stage: WorkspaceStage) -> Dict[str, Any]:
    if not stage.recommendation_json:
        return {}
    try:
        value = json.loads(stage.recommendation_json)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}


def upsert_artifact_reference(
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
    recommendation.setdefault("summary", "已生成阶段产物，可继续确认。")
    recommendation["recommended_action"] = "请查看产物内容；如果可以，就确认通过当前阶段。"
    return recommendation


async def find_latest_workspace_artifact(
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


def _stage_content(stages: List[WorkspaceStage], key: WorkspaceStageKey) -> str:
    for stage in stages:
        if stage.stage_key == key and stage.content:
            return stage.content
    return ""


def build_development_report(
    workspace: Workspace,
    stages: List[WorkspaceStage],
    report_artifact: Artifact,
) -> str:
    requirement = _stage_content(stages, WorkspaceStageKey.REQUIREMENTS) or (workspace.description or "未补充")
    product = _stage_content(stages, WorkspaceStageKey.PRODUCT) or "未补充"
    solution = _stage_content(stages, WorkspaceStageKey.UI_DIRECTION) or "未补充"
    technical = _stage_content(stages, WorkspaceStageKey.TECHNICAL) or "未补充"
    return "\n".join([
        f"# {workspace.name} 实现准备说明",
        "",
        "## 当前状态",
        "- 已整理当前项目的实现准备文档。",
        "- 已沉淀需求、范围、方案整理和实现约束的关键输入。",
        "- 当前结果用于支持人工进入设计、开发或进一步协作，不代表系统已完成代码开发。",
        "",
        "## 已锁定输入",
        f"- 需求确认：{requirement[:240]}",
        f"- 产品方案：{product[:240]}",
        f"- 方案整理：{solution[:240]}",
        f"- 实现约束：{technical[:240]}",
        "",
        "## 当前产物",
        f"- 实现准备文档：{report_artifact.filename}",
        "- 交付目标：让设计、开发或协作者可以直接接手，不要求系统在线完成全部实现。",
        "",
        "## 建议包含",
        "1. 模块拆分建议和协作分工。",
        "2. 数据字段、接口边界、依赖项和风险项。",
        "3. 已确认内容与待确认内容的分界。",
        "4. 进入 IDE 或后续协作前需要准备的事项。",
        "",
        "## 下一步建议",
        "1. 进入验收口径阶段，确认完成标准、边界条件和风险提示。",
        "2. 如果内容足够清楚，基于当前文档进入人工开发或协作。",
        "3. 如果交接信息不足，回到前面阶段继续补充或修订。",
    ])


def build_acceptance_report(
    workspace: Workspace,
    development_report: Artifact,
    report_artifact: Optional[Artifact],
) -> str:
    lines = [
        f"# {workspace.name} 验收结论草稿",
        "",
        "## 验收对象",
        f"- 实现准备文档：{development_report.filename}",
    ]
    if report_artifact:
        lines.append(f"- 相关补充文档：{report_artifact.filename}")
    lines.extend([
        "",
        "## 应重点检查",
        "1. 需求、范围、方案和实现准备之间是否一致。",
        "2. 开发接手时是否已经明确边界、依赖、风险和验收标准。",
        "3. 是否还存在会阻塞开发或协作的关键缺口。",
        "4. 当前文档是否足以作为继续协作或人工开发的基础。",
        "",
        "## 当前结论",
        "- 当前阶段输出的是验收口径和交接基础，不应表述为“完整业务已开发完成”。",
        "- 重点是判断交付物是否足够清楚，而不是判断系统是否已经替代开发。",
        "",
        "## 建议动作",
        "- 如果交付物足够清楚：确认通过并进入交付总览。",
        "- 如果交付物仍有缺口：回到相关阶段提交调整意见。",
    ])
    return "\n".join(lines)


async def generate_workspace_development_stage(
    db: AsyncSession,
    workspace: Workspace,
    user: User,
    stages: List[WorkspaceStage],
    stage: WorkspaceStage,
    *,
    workspace_stage_agent_name: Callable[[WorkspaceStageKey], str],
) -> WorkspaceStage:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    report_content = build_development_report(
        workspace,
        stages,
        Artifact(
            session_type="workspace",
            session_id=workspace.id,
            artifact_type="development_report",
            filename=f"development-report-{timestamp}.md",
            path="",
            mime_type="text/markdown",
            size_bytes=0,
            source="generated",
        ),
    ).encode("utf-8")
    report_artifact = await create_workspace_artifact(
        db=db,
        workspace=workspace,
        user=user,
        artifact_type="development_report",
        filename=f"development-report-{timestamp}.md",
        content=report_content,
        mime_type="text/markdown",
    )

    recommendation = load_recommendation(stage)
    recommendation = upsert_artifact_reference(recommendation, report_artifact, "实现准备说明")
    recommendation.update({
        "source": "development_doc_generator_v2",
        "agent_name": workspace_stage_agent_name(WorkspaceStageKey.DEVELOPMENT),
        "summary": "实现准备阶段已整理成文档交付物，可直接给设计、开发或协作者接手。",
        "recommended_action": "先确认实现准备说明是否足够清楚；如果可以，就进入验收口径阶段。",
        "focus": ["交接说明", "模块拆分建议", "依赖与风险", "验收标准"],
    })

    stage.recommendation_json = json.dumps(recommendation, ensure_ascii=False)
    stage.content = "\n".join([
        "当前已生成实现准备阶段文档交付物。",
        "",
        "本阶段已产出：",
        f"1. 实现准备说明：{report_artifact.filename}",
        "",
        "你现在可以直接把这份说明交给设计、开发或协作者继续执行。",
    ])
    stage.status = WorkspaceStageStatus.AWAITING_CONFIRMATION
    workspace.current_stage = WorkspaceStageKey.DEVELOPMENT
    workspace.updated_at = datetime.now(timezone.utc)
    return stage


async def generate_workspace_acceptance_stage(
    db: AsyncSession,
    workspace: Workspace,
    user: User,
    stages: List[WorkspaceStage],
    stage: WorkspaceStage,
    *,
    workspace_stage_agent_name: Callable[[WorkspaceStageKey], str],
) -> WorkspaceStage:
    _ = stages
    development_report = await find_latest_workspace_artifact(
        db,
        workspace.id,
        ["development_report"],
    )
    if not development_report:
        raise HTTPException(status_code=400, detail="请先生成实现准备说明，再进入验收阶段")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    acceptance_report = await create_workspace_artifact(
        db=db,
        workspace=workspace,
        user=user,
        artifact_type="acceptance_report",
        filename=f"acceptance-report-{timestamp}.md",
        content=build_acceptance_report(workspace, development_report, None).encode("utf-8"),
        mime_type="text/markdown",
    )

    recommendation = load_recommendation(stage)
    recommendation = upsert_artifact_reference(recommendation, development_report, "实现准备说明")
    recommendation = upsert_artifact_reference(recommendation, acceptance_report, "验收结论草稿")
    recommendation.update({
        "source": "acceptance_doc_generator_v2",
        "agent_name": workspace_stage_agent_name(WorkspaceStageKey.ACCEPTANCE),
        "summary": "验收口径阶段已生成验收结论草稿，可直接判断当前交付物是否足够进入下一步。",
        "recommended_action": "查看验收标准、风险和待确认项；如果可以，就进入交付总览。",
        "focus": ["验收标准", "风险提示", "待确认项", "是否可交付"],
    })

    stage.recommendation_json = json.dumps(recommendation, ensure_ascii=False)
    stage.content = "\n".join([
        "当前已进入基于交付文档的验收口径确认。",
        "",
        f"实现准备说明：{development_report.filename}",
        f"验收报告：{acceptance_report.filename}",
        "",
        "请重点确认：交付物是否足够清楚、边界是否明确、是否还存在阻塞开发或协作的关键缺口。",
    ])
    stage.status = WorkspaceStageStatus.AWAITING_CONFIRMATION
    workspace.current_stage = WorkspaceStageKey.ACCEPTANCE
    workspace.updated_at = datetime.now(timezone.utc)
    return stage
