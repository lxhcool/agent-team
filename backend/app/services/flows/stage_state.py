"""Stage state and initialization helpers for staged flows."""

import json
from typing import Any, Dict, List, Optional

from app.models.models import Workspace, WorkspaceStage, WorkspaceStageKey, WorkspaceStageStatus


def default_recommendation(stage_key: WorkspaceStageKey, focus: List[str]) -> Dict[str, Any]:
    return {
        "source": "default",
        "stage_key": stage_key.value,
        "summary": "等待 AI 团队生成本阶段建议。",
        "recommended_action": "先确认本阶段方向，再进入下一步。",
        "focus": focus,
        "options": [],
        "artifacts": [],
    }


def seed_stages(
    workspace: Workspace,
    initial_requirement: Optional[str],
    stage_definitions: List[tuple[WorkspaceStageKey, str, str, List[str]]],
) -> List[WorkspaceStage]:
    stages = []
    for order, (stage_key, title, description, focus) in enumerate(stage_definitions):
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
                    default_recommendation(stage_key, focus), ensure_ascii=False
                ),
                content=content,
            )
        )
    return stages


def next_stage_key(stage_key: WorkspaceStageKey) -> Optional[WorkspaceStageKey]:
    main_flow = [
        WorkspaceStageKey.REQUIREMENTS,
        WorkspaceStageKey.PRODUCT,
        WorkspaceStageKey.UI_DIRECTION,
        WorkspaceStageKey.TECHNICAL,
        WorkspaceStageKey.DEVELOPMENT,
        WorkspaceStageKey.ACCEPTANCE,
        WorkspaceStageKey.DEPLOYMENT,
    ]
    if stage_key == WorkspaceStageKey.PROTOTYPE:
        return WorkspaceStageKey.TECHNICAL
    try:
        index = main_flow.index(stage_key)
    except ValueError:
        return None
    if index + 1 >= len(main_flow):
        return None
    return main_flow[index + 1]


def has_generated_recommendation(load_recommendation, stage: WorkspaceStage) -> bool:
    recommendation = load_recommendation(stage)
    return bool(recommendation.get("source"))
