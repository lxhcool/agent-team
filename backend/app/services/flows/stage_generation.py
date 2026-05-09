"""LLM-backed stage generation and model resolution helpers."""

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.router import LLMMessage, llm_router
from app.models.models import AgentTemplate, ModelSettings, ProviderConfig, User, Workspace, WorkspaceStage, WorkspaceStageKey, WorkspaceStageStatus
from app.services.flows.memory import build_workspace_memory_context
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
    if stage.stage_key not in {
        WorkspaceStageKey.PRODUCT,
        WorkspaceStageKey.TECHNICAL,
    }:
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
    memory_context: str,
    stage_skill_context: str = "",
    external_reference_context: str = "",
) -> str:
    stage_context = []
    for item in stages:
        if item.id != stage.id and item.status == WorkspaceStageStatus.APPROVED and item.content:
            stage_context.append({
                "stage": item.stage_key.value,
                "title": item.title,
                "status": item.status.value,
                "content": item.content[:1800],
                "user_feedback": item.user_feedback,
            })

    stage_guidance = {
        WorkspaceStageKey.REQUIREMENTS: {
            "document_identity": "产品理解确认文档",
            "must_answer": ["这是一个什么产品", "主要给谁用", "主要解决什么事", "当前结构性前提", "为什么现在可以确认这一阶段完成"],
            "avoid": ["不要提前输出模块设计", "不要提前输出页面结构", "不要写权限或技术实现方案", "不要把后续阶段议题写成第一阶段待确认项", "不要输出待确认项清单", "不要把实现性细节展开成完整规则方案"],
        },
        WorkspaceStageKey.PRODUCT: {
            "document_identity": "结构化方案稿",
            "must_answer": ["功能模块", "模块关系", "页面结构", "主要流程"],
            "avoid": ["不要重写需求背景", "不要重复上游约束", "不要深入规则细节"],
            "upstream_rule": "可以保留一个很短的承接前提，1 到 3 行即可；正文只写本阶段新增方案。",
            "forbidden_sections": ["产品定义", "目标用户", "核心价值", "项目目标", "结构性前提"],
        },
        WorkspaceStageKey.UI_DIRECTION: {
            "document_identity": "规则文档",
            "must_answer": ["权限", "状态流转", "异常处理", "数据口径", "关键边界"],
            "avoid": ["不要重讲整体方案", "不要写技术实现", "不要写页面设计说明"],
            "upstream_rule": "可以保留一个很短的承接前提，说明基于哪版方案继续细化；正文只写规则新增确认。",
            "forbidden_sections": ["产品定义", "目标用户", "功能模块", "页面结构", "主要流程"],
        },
        WorkspaceStageKey.TECHNICAL: {
            "document_identity": "技术方案文档",
            "must_answer": ["技术落地方式", "模块拆分", "接口组织", "数据结构", "风险与顺序"],
            "avoid": ["不要重复产品背景", "不要只写技术栈口号", "不要伪装成代码完成"],
            "upstream_rule": "可以保留一个很短的承接前提，说明承接哪版方案和规则；正文只写实现与交接层内容。",
            "forbidden_sections": ["产品定义", "目标用户", "核心价值", "功能模块详解", "规则总览"],
        },
        WorkspaceStageKey.DEPLOYMENT: {
            "document_identity": "交付索引文档",
            "must_answer": ["文档清单", "所属阶段", "简要说明", "单独下载", "整体打包"],
            "avoid": ["不要复述前面几份文档正文", "不要重新写一篇项目总结", "不要新增核心方案内容"],
            "upstream_rule": "这里只需要一小段最终说明；主体必须是文档集合和下载/交接说明。",
            "forbidden_sections": ["产品定义", "目标用户", "功能模块", "页面结构", "技术方案正文"],
        },
    }.get(stage.stage_key, {})

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
        "stage_document_guidance": stage_guidance,
        "workspace_memory_context": memory_context,
        "stage_skill_context": stage_skill_context or None,
        "external_reference_context": external_reference_context or None,
        "conversation_goal": "基于用户输入和已确认阶段内容，产出当前阶段真正可确认、可交接的文档内容。上游内容只可作为输入前提短承接，正文必须是当前阶段新增确认，不套固定页面模板。",
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
            "只做需求确认：先对齐这到底是个什么产品，不要把这一阶段写成功能盘点或方案设计。",
            "必须优先写清产品性质、主要使用者、核心目的、当前结构性前提，以及为什么现在已经可以确认第一阶段完成。",
            "不要提前输出模块清单、页面结构、权限模型、技术实现或接口方案。",
            "必须紧贴用户原话，明确哪些是事实，哪些只是合理推断；推断不能伪装成已确认事实。",
            "既然已经生成阶段结论文档，就不要再输出待确认项；如果当前阶段还有问题没收完，就不应该在这一轮产出结论文档。",
            "不要把社区功能、审核机制、商业模式、技术方案这类后续阶段议题提前挂到第一阶段文档里。",
            "不要把第一阶段做成固定提问模板。你要根据当前产品本身动态判断，当前还缺哪些会影响后续方案结构的结构性前提。",
            "如果用户输入还不足以支撑下一阶段方案设计，就继续追问少量关键结构问题，不要只确认产品名字和角色就结束。",
            "第一阶段优先确认结构性前提，不要提前展开实现性细节。",
            "不要把具体问题写死成清单；固定的是阶段目标和收口标准，具体追问内容由你根据产品情况自行判断。",
        ],
        WorkspaceStageKey.PRODUCT: [
            "输出完整方案设计，按 功能模块 -> 模块关系 -> 页面结构 -> 主要流程 的顺序展开。",
            "默认当前方案就是本次要做的完整方案，不要默认按 MVP 或分期切分。",
            "不要机械套页面栏目，不要脱离模块设计直接从页面列表开始。",
            "不要重写需求背景和上游已经确认的约束，除非它直接影响当前方案结构。",
            "如果需要承接上游，只允许用一个很短的“承接前提/输入前提”带过，控制在 1 到 3 行。",
            "正文不要出现“产品定义、目标用户、核心价值、项目目标、结构性前提”这类上游阶段栏目。",
            "功能模块、页面结构、主要流程都要写到可落文档的粒度，不能只停留在“用户端/后台端/预约流程”这种标题级概括。",
            "这一阶段固定的是目标，不是固定问题列表。要根据当前产品类型和上游结论，动态补齐方案骨架里真正缺的部分。",
        ],
        WorkspaceStageKey.UI_DIRECTION: [
            "只做细节确认，重点输出角色权限、状态流转、异常处理、数据口径、特殊业务规则和边界条件。",
            "不要把这一阶段写成技术实现方案，也不要重新发散成新的产品方向。",
            "如果需要承接上游，只允许用一个很短的“承接前提/输入前提”带过，控制在 1 到 3 行。",
            "正文不要回头重写功能模块、页面结构、主要流程。",
            "如果还有关键缺口，要明确指出它会影响哪个后续决策。",
            "不要重新讲模块划分和整体流程，只补本阶段新增规则。",
        ],
        WorkspaceStageKey.TECHNICAL: [
            "输出开发方案：技术落地方式、模块拆分、接口与数据组织、依赖项、风险和实施建议。",
            "默认真实开发在本地 IDE 完成，这里只整理可交接的开发方案，不写代码。",
            "如果需要承接上游，只允许用一个很短的“承接前提/输入前提”带过，控制在 1 到 3 行。",
            "正文不要回头重写产品定义、目标用户、业务方案总述或规则总览。",
            "必须明显区别于产品方案文档，重点放在实现与交接，而不是业务背景复述。",
            "优先采用成熟通用的工程默认方案，直接给出推荐实现和理由，不要把常规技术细节重新丢回给非技术用户拍板。",
        ],
        WorkspaceStageKey.DEPLOYMENT: [
            "必须输出交付清单，包括已确认文档、简要说明、单独下载和整体打包的交接说明。",
            "不要输出部署成功或代码执行完成这类超出当前边界的结论。",
            "不要把前面阶段正文重新拼成一篇总结，主体必须是文档列表和附件说明。",
            "可以保留一段很短的最终总结，但主体必须是附件列表或文档集合。",
            "如果上游文档已经齐全，就直接进入汇总，不要继续追问用户。",
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
- `content` 才是主体，必须直接给出当前阶段真正要确认的产物正文。
- `options` 不是必填。只有确实存在结构上明显不同的备选路线时才返回；如果只是给一版主方案，直接返回单份 `content` 即可。
- 如果返回 `options`，每个 option 必须附带 content，让用户切换方案后当前产物立即变化。
- recommendation.summary、recommended_action 必须是用户能直接理解的短句。
- 不要为了凑格式硬造多个方案；大多数情况下，一版成熟主方案就够了。
- 后续阶段默认继承上游已确认内容，只写本阶段新增确认，不要把前面文档重写一遍。
- 如果需要承接上游，只允许保留一个很短的“承接前提/输入前提”，控制在 1 到 3 行。
- 阶段结论文档一旦产出，就表示当前阶段已经闭环；不要再输出当前阶段未完成事项清单。

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
    runtime_options: Optional[Dict[str, Any]] = None,
    *,
    stage_agent_names: Dict[WorkspaceStageKey, str],
    builtin_agent_map: Dict[str, Any],
    parse_json_object,
    sanitize_llm_artifact,
) -> Optional[tuple[Dict[str, Any], str]]:
    llm_timeout_seconds = 20

    try:
        model, provider, fallback_chain = await resolve_generation_model(
            db,
            user,
            overrides=runtime_options,
        )
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
        memory_context = await build_workspace_memory_context(
            db,
            workspace.id,
            stage_order=stage.order,
            stages=stages,
        )
        stage_skill_context = build_stage_skill_context(
            stage.stage_key,
            enable_stage_skills=bool(
                True if runtime_options is None else runtime_options.get("enable_stage_skills", True)
            ),
        )
        external_reference_context = await build_external_reference_context(
            db,
            user,
            workspace,
            stage,
            instruction or stage.user_feedback or workspace.description or workspace.name or "",
            enable_web_search=bool(runtime_options and runtime_options.get("enable_web_search")),
        )

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
                            memory_context=memory_context,
                            stage_skill_context=stage_skill_context,
                            external_reference_context=external_reference_context,
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
                reasoning_effort=(
                    None if runtime_options is None else runtime_options.get("reasoning_effort")
                ),
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
