"""LLM-backed stage generation and model resolution helpers."""

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.router import LLMMessage, llm_router
from app.models.models import AgentTemplate, ModelSettings, ProviderConfig, User, Workspace, WorkspaceStage, WorkspaceStageKey

logger = logging.getLogger(__name__)


async def resolve_generation_model(
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


async def prefer_low_latency_generation_model(
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


def build_stage_generation_prompt(
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
        "conversation_goal": "基于用户输入和已确认阶段内容，产出当前阶段真正可确认、可交接的文档内容，不套固定页面模板。",
        "known_stage_outputs": stage_context,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def workspace_stage_agent_name(stage_key: WorkspaceStageKey, stage_agent_names: Dict[WorkspaceStageKey, str]) -> str:
    return stage_agent_names.get(stage_key, "orchestrator")


async def resolve_workspace_stage_agent(
    db: AsyncSession,
    stage_key: WorkspaceStageKey,
    *,
    stage_agent_names: Dict[WorkspaceStageKey, str],
    builtin_agent_map: Dict[str, Any],
) -> tuple[str, str]:
    agent_name = workspace_stage_agent_name(stage_key, stage_agent_names)
    result = await db.execute(select(AgentTemplate).where(AgentTemplate.name == agent_name))
    agent = result.scalar_one_or_none()
    if agent and agent.system_prompt and agent.system_prompt.strip():
        return agent_name, agent.system_prompt.strip()

    builtin = builtin_agent_map.get(agent_name, {})
    system_prompt = str(builtin.get("system_prompt") or "").strip()
    if not system_prompt:
        orchestrator_prompt = str(builtin_agent_map.get("orchestrator", {}).get("system_prompt") or "").strip()
        system_prompt = orchestrator_prompt or "你是一个负责推进工作区阶段产出的专业 AI Agent。"
    return agent_name, system_prompt


def stage_generation_contract(stage_key: WorkspaceStageKey) -> str:
    stage_rules = {
        WorkspaceStageKey.REQUIREMENTS: [
            "只做需求澄清：收敛目标、对象、动作、边界、缺失信息。",
            "不要提前输出页面方案、官网结构、后台模块或技术实现。",
            "必须紧贴用户原话，明确哪些是事实，哪些只是推断。",
        ],
        WorkspaceStageKey.PRODUCT: [
            "输出本期范围、主流程、关键对象、关键状态、P0/P1/P2 和明确不做项。",
            "不要机械套页面栏目，不要默认补首页、关于、案例、联系等结构。",
            "如果给多个方案，差异必须体现在范围取舍，不只是换词。",
        ],
        WorkspaceStageKey.UI_DIRECTION: [
            "输出同一个产品的 2-3 个表达方向，重点是信息组织、语气、密度和强调方式。",
            "不要把这一阶段写成具体页面模板，更不要改变产品本体。",
            "每个方向都要说明为什么适合当前需求，而不是抽象审美描述。",
        ],
        WorkspaceStageKey.PROTOTYPE: [
            "输出必须面向真实页面原型，不要把需求说明直接铺在页面上。",
            "优先提供可确认的页面结构、关键操作区、状态反馈和预览产物说明。",
            "原型必须像当前需求指向的那个产品，而不是泛化页面。",
        ],
        WorkspaceStageKey.TECHNICAL: [
            "明确实现边界、协作方式、外部依赖、数据/内容来源、风险和不做项。",
            "默认真实开发在本地 IDE 完成，这里只整理实现约束与交接条件。",
        ],
        WorkspaceStageKey.DEVELOPMENT: [
            "输出接手文档：模块拆分建议、任务顺序、依赖项、待确认项、风险和验收前提。",
            "不要把实现准备文档写成已经编码完成的结果。",
        ],
        WorkspaceStageKey.ACCEPTANCE: [
            "必须围绕当前产物给出验收项、通过标准、问题项和结论。",
            "不要基于空文案给出通过判断，要明确哪些还缺信息。",
        ],
        WorkspaceStageKey.DEPLOYMENT: [
            "必须输出交付总览，包括产物索引、版本说明、已确认事项和建议下一步。",
            "不要输出部署成功或代码执行完成这类超出当前边界的结论。",
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


async def generate_stage_artifact_with_llm(
    db: AsyncSession,
    user: User,
    workspace: Workspace,
    stages: List[WorkspaceStage],
    stage: WorkspaceStage,
    instruction: Optional[str],
    *,
    stage_agent_names: Dict[WorkspaceStageKey, str],
    builtin_agent_map: Dict[str, Any],
    parse_json_object,
    sanitize_llm_artifact,
) -> Optional[tuple[Dict[str, Any], str]]:
    llm_timeout_seconds = 20

    try:
        model, provider, fallback_chain = await resolve_generation_model(db, user)
        if not model or not provider:
            return None

        await llm_router.load_providers(db, user_id=user.id)
        agent_name, agent_prompt = await resolve_workspace_stage_agent(
            db,
            stage.stage_key,
            stage_agent_names=stage_agent_names,
            builtin_agent_map=builtin_agent_map,
        )
        system_prompt = f"{agent_prompt}\n\n{stage_generation_contract(stage.stage_key)}"

        result = await asyncio.wait_for(
            llm_router.call(
                messages=[
                    LLMMessage(role="system", content=system_prompt),
                    LLMMessage(
                        role="user",
                        content=build_stage_generation_prompt(
                            workspace=workspace,
                            stages=stages,
                            stage=stage,
                            instruction=instruction,
                        ),
                    ),
                ],
                model=model,
                provider_name=provider,
                fallback_chain=fallback_chain,
                max_tokens=1400,
                temperature=0.35,
                session_id=workspace.id,
                session_type="workspace",
                agent_name=agent_name,
            ),
            timeout=llm_timeout_seconds,
        )
        recommendation, content = sanitize_llm_artifact(parse_json_object(result.content))
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
