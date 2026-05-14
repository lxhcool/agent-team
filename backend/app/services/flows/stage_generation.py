"""Minimal runtime helpers for staged flow conversations."""

import json
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import ModelSettings, ProviderConfig, User, Workspace, WorkspaceStage, WorkspaceStageKey
from app.services.skill_registry import skill_registry
from app.services.tools import tool_registry

logger = logging.getLogger(__name__)


STAGE_SKILL_MAP: Dict[WorkspaceStageKey, List[str]] = {
    WorkspaceStageKey.PRODUCT: ["flow-product-structure-guard"],
    WorkspaceStageKey.UI_DIRECTION: ["flow-rule-clarity-guard"],
    WorkspaceStageKey.TECHNICAL: ["flow-technical-defaults-guard"],
    WorkspaceStageKey.DEPLOYMENT: ["flow-artifact-handoff-guard"],
}


async def resolve_generation_model(
    db: AsyncSession,
    user: User,
    overrides: Optional[Dict[str, Any]] = None,
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

    if overrides:
        override_model = str(overrides.get("model") or "").strip()
        override_provider = str(overrides.get("provider") or "").strip()
        if override_model:
            model = override_model
        if override_provider:
            provider = override_provider

    provider_result = await db.execute(
        select(ProviderConfig).where(
            ProviderConfig.user_id == user.id,
            ProviderConfig.enabled == True,
        )
    )
    providers = list(provider_result.scalars().all())
    keyed_providers = [item for item in providers if item.api_key_encrypted]

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


def build_stage_skill_context(
    stage_key: WorkspaceStageKey,
    *,
    enable_stage_skills: bool = True,
) -> str:
    if not enable_stage_skills:
        return ""

    blocks: List[str] = []
    for skill_name in STAGE_SKILL_MAP.get(stage_key, []):
        prompt = skill_registry.get_skill_prompt(skill_name)
        if not prompt:
            continue
        blocks.append(prompt.strip()[:350])
    if not blocks:
        return ""
    return "\n\n".join(block for block in blocks[:2] if block)


async def build_external_reference_context(
    db: AsyncSession,
    user: User,
    workspace: Workspace,
    stage: WorkspaceStage,
    latest_input: str,
    *,
    enable_web_search: bool = False,
) -> str:
    if not enable_web_search:
        return ""
    if stage.stage_key not in {WorkspaceStageKey.PRODUCT, WorkspaceStageKey.TECHNICAL}:
        return ""

    tool = tool_registry.get_tool("web_search")
    if tool is None:
        return ""

    topic = (latest_input or workspace.description or workspace.name or "").strip()
    if not topic:
        return ""

    query_map = {
        WorkspaceStageKey.PRODUCT: [
            f"{topic} 产品设计 功能模块 页面结构 用户流程 最佳实践",
            f"{topic} 成熟案例 产品方案",
        ],
        WorkspaceStageKey.TECHNICAL: [
            f"{topic} 系统设计 架构 接口 数据模型 最佳实践",
            f"{topic} 技术方案 成熟案例",
        ],
    }
    queries = query_map.get(stage.stage_key, [])
    if not queries:
        return ""

    lines: List[str] = []
    seen: set[str] = set()
    for query in queries[:2]:
        try:
            result = await tool.execute(query=query, max_results=3, user_id=user.id)
        except Exception as exc:
            logger.warning(
                "web search helper failed: workspace=%s stage=%s query=%s reason=%s",
                workspace.id,
                stage.stage_key.value,
                query,
                exc,
            )
            continue
        if not result.success or not isinstance(result.data, dict):
            continue
        for item in result.data.get("results", [])[:3]:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            snippet = str(item.get("snippet") or "").strip()
            url = str(item.get("url") or "").strip()
            dedupe_key = f"{title}|{url}|{snippet[:80]}"
            if not title or dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            line = f"- {title}"
            if snippet:
                line += f"：{snippet[:180]}"
            if url:
                line += f"（{url}）"
            lines.append(line)
        if len(lines) >= 5:
            break

    if not lines:
        return ""

    return "\n".join(lines[:5])
