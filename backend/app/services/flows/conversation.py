"""Conversation orchestration for staged flow interactions."""

import json
import logging
import re
import asyncio
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.router import LLMError, LLMMessage, ProviderAdapter, llm_router
from app.models.models import Artifact, User, Workspace, WorkspaceStage, WorkspaceStageKey, WorkspaceStageMessage, WorkspaceStageStatus

logger = logging.getLogger(__name__)


def build_requirements_chat_prompt(
    workspace: Workspace,
    messages: List[WorkspaceStageMessage],
) -> str:
    transcript = messages_to_prompt_text(messages)
    user_messages = requirements_user_messages(messages)
    latest = requirements_latest_user_message(messages)
    is_first_turn = len(user_messages) <= 1
    requirement = (workspace.description or workspace.name or "").strip()
    return f"""
你现在扮演的是一个会推进项目的产品同事，不是在写需求分析报告。

你要做的是：基于当前对话，直接回复用户下一句该说的话。

对话要求：
1. 这是对话，不是文档，不要重复整段已有内容。
2. 如果是第一轮，不要只反问用户。你要先像产品同事一样主动给出“我建议第一版先这样做”的方案：
   - 先判断用户要做的是什么产品/网站/小程序。
   - 把这个产品成立所必需的核心流程直接纳入第一版，不要询问用户是否需要这些基础能力。
   - 基于常见业务做法补充推荐默认方案，并明确说明“如果你没有特别要求，我先按这个处理”。
   - 最后只问 1-2 个真正会影响范围、成本或后续设计的关键选择。
3. 如果不是第一轮，只回应用户这次新补充带来的变化：
   - 先确认你收到了什么新决定
   - 再更新“那第一版现在我先按什么做”，把已确认内容沉淀成更明确的方案
   - 如果关键信息已经够了，就直接告诉用户：如果没有别的补充，他可以结束这一阶段，你会整理阶段结论
4. 区分三类信息：
   - 必须项：没有它就无法形成完整闭环的内容，直接写入方案，不要问。
   - 推荐默认项：你根据常识给出建议，用户可以直接同意或修改。
   - 关键决策项：确实会改变范围、复杂度或交付方式的，才问用户。
5. 关键决策可以给 A/B/C 选择，但必须有推荐项；不要把所有内容都变成选择题。用户可以直接回复“可以/OK”，表示接受你的推荐。
6. 不要再问从原句就能直接判断的问题，比如博客网站就不要再问“这是不是博客”“核心动作是什么”“主要给谁用”。
7. 不要使用“当前匹配判断、已对上的信息、第一版主线、范围边界、非目标”这类报告式标题。
8. 不要输出 JSON，不要输出模板说明，不要解释你的推理过程。
9. 用自然中文，像正常同事在微信里推进事情。
10. 回复控制在 8 到 18 行内；内容要具体贴合用户输入，不要用通用套话撑篇幅。

原始需求：
{requirement}

是否第一轮：
{"是" if is_first_turn else "否"}

本轮用户最新补充：
{latest}

到目前为止的对话：
{transcript}
""".strip()


def build_generic_stage_chat_prompt(
    workspace: Workspace,
    stage: WorkspaceStage,
    stages: List[WorkspaceStage],
    messages: List[WorkspaceStageMessage],
) -> str:
    transcript = messages_to_prompt_text(messages)
    latest = requirements_latest_user_message(messages)
    approved_context: List[str] = []
    for item in stages:
        if item.id == stage.id or item.status != WorkspaceStageStatus.APPROVED or not item.content:
            continue
        approved_context.append(f"[{item.title}]\n{item.content.strip()[:1800]}")
    approved_block = "\n\n".join(approved_context) or "暂无"
    requirement = (workspace.description or workspace.name or "").strip()
    return f"""
你现在处于一个多阶段产品流程里，当前阶段是「{stage.title}」。

你的任务不是直接产出最终文档，而是像同事一样继续和用户对话，把这一阶段逐步收敛清楚。

回复要求：
1. 只回应这次用户新补充带来的变化，不要把前文大段重写一遍。
2. 这是对话，不是报告，不要使用固定小标题和模板化段落。
3. 先说你基于这次补充更新了什么，再说这一阶段接下来怎么收。
4. 如果信息还不够，就只追问 1-3 个真正阻塞这一阶段的问题。
5. 如果这一阶段信息已经够了，就直接告诉用户：如果没有别的补充，可以结束这一阶段，你会整理结论。
6. 不要脱离当前阶段职责。不要提前跳去实现代码，也不要机械补全页面模板。
7. 用自然中文，像微信里协作推进，控制在 5 到 12 行内。

项目原始需求：
{requirement}

当前阶段说明：
{stage.description or stage.title}

已确认的上游阶段信息：
{approved_block}

用户本轮最新补充：
{latest}

当前阶段到目前为止的对话：
{transcript}
""".strip()


def build_requirements_conclusion_prompt(
    workspace: Workspace,
    messages: List[WorkspaceStageMessage],
) -> str:
    transcript = messages_to_prompt_text(messages)
    requirement = (workspace.description or workspace.name or "").strip()
    return f"""
你现在要把一段需求澄清对话整理成“阶段结论”。

要求：
1. 这不是对话回复，而是这个阶段的正式结论文档。
2. 不要复述“我收到你的补充了”这类过程话术。
3. 只保留已经确认下来的关键信息，不要把整段对话原文再抄一遍。
4. 请严格按下面 7 个标题输出：
   - ## 项目定义
   - ## 当前目标
   - ## 第一版范围
   - ## 关键规则
   - ## 暂不处理
   - ## 待确认项
   - ## 阶段结论
5. 如果某一块没有内容，也要给出简短结论，不能空着。
6. 如果没有未确认项，就直接写“当前没有阻塞下一阶段的待确认项”。
7. 阶段结论要明确说明：是否已经可以进入下一阶段。

原始需求：
{requirement}

对话记录：
{transcript}
""".strip()


def build_generic_stage_conclusion_prompt(
    workspace: Workspace,
    stage: WorkspaceStage,
    stages: List[WorkspaceStage],
    messages: List[WorkspaceStageMessage],
) -> str:
    transcript = messages_to_prompt_text(messages)
    approved_context: List[str] = []
    for item in stages:
        if item.id == stage.id or item.status != WorkspaceStageStatus.APPROVED or not item.content:
            continue
        approved_context.append(f"[{item.title}]\n{item.content.strip()[:1800]}")
    approved_block = "\n\n".join(approved_context) or "暂无"
    requirement = (workspace.description or workspace.name or "").strip()
    return f"""
你现在要把「{stage.title}」阶段的对话整理成正式结论。

要求：
1. 这是阶段结论文档，不是聊天回复。
2. 只总结已经确认下来的内容，不要把整段对话原文重抄。
3. 结合当前阶段职责来总结，突出当前阶段真正产出的决定、边界、规则和待确认项。
4. 输出结构要清楚、可交接、可继续进入下一阶段。
5. 如果当前没有阻塞下一阶段的待确认项，要明确写出来。
6. 最后必须明确说明：当前是否可以进入下一阶段。
7. 用 Markdown 输出，篇幅控制在 300 到 700 字之间。

项目原始需求：
{requirement}

已确认的上游阶段信息：
{approved_block}

当前阶段：
{stage.title}
{stage.description or ""}

对话记录：
{transcript}
""".strip()


async def stream_llm_text(
    db: AsyncSession,
    user: User,
    *,
    messages: List[LLMMessage],
    session_id: str,
    agent_name: str,
    resolve_generation_model: Callable[[AsyncSession, User], Awaitable[tuple[Optional[str], Optional[str], Optional[List[str]]]]],
    max_tokens: int = 900,
    temperature: float = 0.35,
) -> tuple[Optional[AsyncIterator[Dict[str, str]]], Optional[str], Optional[str]]:
    model, provider, _fallback_chain = await resolve_generation_model(db, user)
    if not model or not provider:
        return None, None, None

    await llm_router.load_providers(db, user_id=user.id)
    provider_info = llm_router.get_provider(provider)
    if not provider_info:
        return None, None, None

    adapter = ProviderAdapter(provider_info)
    stream = await adapter.complete(
        messages=messages,
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        stream=True,
    )
    return stream, model, provider


async def get_stage_messages(db: AsyncSession, stage_id: str) -> List[WorkspaceStageMessage]:
    result = await db.execute(
        select(WorkspaceStageMessage)
        .where(WorkspaceStageMessage.stage_id == stage_id)
        .order_by(WorkspaceStageMessage.created_at.asc())
    )
    return list(result.scalars().all())


async def append_stage_message(
    db: AsyncSession,
    stage: WorkspaceStage,
    role: str,
    content: str,
    kind: str = "chat",
    artifact_id: Optional[str] = None,
) -> WorkspaceStageMessage:
    message = WorkspaceStageMessage(
        stage_id=stage.id,
        role=role,
        kind=kind,
        content=content,
        artifact_id=artifact_id,
    )
    db.add(message)
    await db.flush()
    return message


def messages_to_prompt_text(messages: List[WorkspaceStageMessage]) -> str:
    lines: List[str] = []
    for message in messages:
        speaker = "用户" if message.role == "user" else "助手"
        lines.append(f"{speaker}：{message.content.strip()}")
    return "\n".join(lines).strip()


def requirements_user_messages(messages: List[WorkspaceStageMessage]) -> List[str]:
    return [item.content.strip() for item in messages if item.role == "user" and item.content.strip()]


def requirements_latest_user_message(messages: List[WorkspaceStageMessage]) -> str:
    for item in reversed(messages):
        if item.role == "user" and item.content.strip():
            return item.content.strip()
    return ""


def bootstrap_stage_instruction(workspace: Workspace, stage: WorkspaceStage) -> str:
    if stage.stage_key == WorkspaceStageKey.REQUIREMENTS:
        return (workspace.description or workspace.name or "").strip()
    return "请基于当前项目背景和前面已确认的内容，直接开始这一阶段的整理，并明确还需要我确认什么。"


def build_stage_chat_instruction(
    workspace: Workspace,
    stage: WorkspaceStage,
    messages: List[WorkspaceStageMessage],
) -> str:
    transcript = messages_to_prompt_text(messages)
    if transcript:
        return "\n".join([
            f"当前阶段：{stage.title}",
            "",
            "以下是本阶段到目前为止的对话，请基于这些内容继续回答，不要忽略用户刚补充的信息：",
            transcript,
        ]).strip()
    return bootstrap_stage_instruction(workspace, stage)


async def should_finalize_stage_message(
    db: AsyncSession,
    user: User,
    workspace: Workspace,
    stage: WorkspaceStage,
    messages: List[WorkspaceStageMessage],
    *,
    resolve_generation_model: Callable[[AsyncSession, User], Awaitable[tuple[Optional[str], Optional[str], Optional[List[str]]]]],
    parse_json_object: Callable[[str], Dict[str, Any]],
) -> bool:
    if not messages:
        return False

    latest = requirements_latest_user_message(messages)
    if not latest:
        return False

    user_message_count = len(requirements_user_messages(messages))
    if user_message_count < 2:
        return False

    model, provider, fallback_chain = await resolve_generation_model(db, user)
    if not model or not provider:
        return False

    transcript = messages_to_prompt_text(messages[-6:])
    prompt = f"""
当前阶段：{stage.title}
任务：判断最后一条用户消息是否在表达“本阶段已经可以结束，请整理阶段结论/文档/进入下一阶段”。

最近对话：
{transcript}

只返回 JSON，不要解释：
{{"should_finalize": true, "reason": "用户明确要求整理阶段结论"}}

判断规则：
- 最后一条用户消息是在补充、修改、回答问题、提出新需求：false。
- 最后一条用户消息是在确认够了、要求整理结论、要求生成文档、要求进入下一阶段：true。
- 只看语义和上下文，不做固定词匹配。
""".strip()

    try:
        await llm_router.load_providers(db, user_id=user.id)
        result = await llm_router.call(
            messages=[
                LLMMessage(role="system", content="你是一个负责判断阶段是否可以收尾的助手。"),
                LLMMessage(role="user", content=prompt),
            ],
            model=model,
            provider_name=provider,
            fallback_chain=fallback_chain,
            max_tokens=700,
            temperature=0.0,
            session_id=workspace.id,
            session_type="workspace",
            agent_name="stage-finalize-judge",
        )
        content = (result.content or "").strip()
        try:
            parsed = parse_json_object(content)
            return bool(parsed.get("should_finalize"))
        except Exception:
            normalized = re.sub(r"\s+", "", content).lower()
            if normalized in {"true", "yes", "y"}:
                return True
            if normalized in {"false", "no", "n"}:
                return False
            raise
    except Exception as exc:
        logger.warning(
            "stage finalize judge failed conservatively: workspace=%s stage=%s reason=%s",
            workspace.id,
            stage.stage_key.value,
            exc,
        )
        return False


def build_requirements_chat_fallback(
    workspace: Workspace,
    messages: List[WorkspaceStageMessage],
) -> str:
    requirement = (workspace.description or workspace.name or "").strip()
    latest = requirements_latest_user_message(messages)
    if len(requirements_user_messages(messages)) <= 1:
        return "\n".join([
            "当前没有拿到模型回复。",
            "",
            f"我只知道你的原始需求是：{requirement}",
            "",
            "为了避免继续用固定话术假装对话，我先不自动往下编。",
            "请先检查模型配置；模型恢复后，我会基于你的真实输入继续澄清。",
        ])

    return "\n".join([
        "当前没有拿到模型回复。",
        "",
        f"你刚补充的是：{latest or '暂无'}",
        "",
        "为了避免继续用固定话术假装对话，我先不自动生成下一轮内容。",
        "请先检查模型配置；模型恢复后，我会继续基于这次补充往下推进。",
    ])


def build_model_unavailable_stage_output(
    stage: WorkspaceStage,
    *,
    summary: Optional[str] = None,
    recommended_action: Optional[str] = None,
    focus: Optional[List[str]] = None,
    content: Optional[str] = None,
) -> tuple[Dict[str, Any], str]:
    resolved_content = (content or "").strip() or "\n".join([
        "当前没有拿到可用的大模型回复。",
        "",
        "为了避免继续用固定规则或模板伪装成智能对话，这一阶段先不自动生成内容。",
        "请先检查模型配置；模型恢复后，再继续当前阶段。",
    ]).strip()
    recommendation = {
        "source": "model_unavailable",
        "summary": summary or "当前没有拿到可用的大模型回复。",
        "recommended_action": recommended_action or "请先检查模型配置，恢复后再继续当前阶段。",
        "focus": focus or ["模型可用性", "避免伪对话", "恢复真实生成"],
        "options": [
            {
                "title": "模型不可用",
                "description": "当前阶段未拿到可靠模型输出，系统不会再用固定模板代替。",
                "content": resolved_content,
                "recommended": True,
            }
        ],
        "selected_option": "模型不可用",
    }
    return recommendation, resolved_content


async def generate_requirements_chat_reply_with_llm(
    db: AsyncSession,
    user: User,
    workspace: Workspace,
    messages: List[WorkspaceStageMessage],
    *,
    resolve_generation_model: Callable[[AsyncSession, User], Awaitable[tuple[Optional[str], Optional[str], Optional[List[str]]]]],
) -> Optional[str]:
    model, provider, fallback_chain = await resolve_generation_model(db, user)
    if not model or not provider:
        return None

    prompt = build_requirements_chat_prompt(workspace, messages)

    try:
        await llm_router.load_providers(db, user_id=user.id)
        result = await llm_router.call(
            messages=[
                LLMMessage(role="system", content="你是一个擅长需求澄清和项目推进的产品负责人。"),
                LLMMessage(role="user", content=prompt),
            ],
            model=model,
            provider_name=provider,
            fallback_chain=fallback_chain,
            max_tokens=700,
            temperature=0.35,
            session_id=workspace.id,
            session_type="workspace",
            agent_name="requirements-conversation",
        )
        content = result.content.strip()
        return content or None
    except Exception as exc:
        logger.warning(
            "requirements conversation fell back to local heuristics: workspace=%s reason=%s",
            workspace.id,
            exc,
        )
        return None


def fallback_requirements_conclusion(
    workspace: Workspace,
    messages: List[WorkspaceStageMessage],
) -> str:
    requirement = (workspace.description or workspace.name or "").strip()
    user_messages = requirements_user_messages(messages)
    captured = [f"- {item}" for item in user_messages[1:4]] or ["- 暂无。"]

    return "\n".join([
        "## 项目定义",
        f"- {requirement}",
        "",
        "## 当前目标",
        "- 先把当前需求的核心目标和第一版范围确认清楚。",
        "",
        "## 第一版范围",
        "- 当前无法可靠自动提炼，请基于对话人工确认。",
        "",
        "## 关键规则",
        *captured,
        "",
        "## 暂不处理",
        "- 当前没有可靠自动判断结果。",
        "",
        "## 待确认项",
        "- 当前因为模型不可用，系统没有可靠地产出自动总结，请人工复核后再进入下一阶段。",
        "",
        "## 阶段结论",
        "- 当前不建议自动进入下一阶段，需要先恢复模型能力或人工复核。",
    ]).strip()


async def generate_requirements_conclusion_with_llm(
    db: AsyncSession,
    user: User,
    workspace: Workspace,
    messages: List[WorkspaceStageMessage],
    *,
    resolve_generation_model: Callable[[AsyncSession, User], Awaitable[tuple[Optional[str], Optional[str], Optional[List[str]]]]],
) -> Optional[str]:
    model, provider, fallback_chain = await resolve_generation_model(db, user)
    if not model or not provider:
        return None

    prompt = build_requirements_conclusion_prompt(workspace, messages)
    try:
        await llm_router.load_providers(db, user_id=user.id)
        result = await llm_router.call(
            messages=[
                LLMMessage(role="system", content="你是一个擅长整理项目需求结论的产品负责人。"),
                LLMMessage(role="user", content=prompt),
            ],
            model=model,
            provider_name=provider,
            fallback_chain=fallback_chain,
            max_tokens=700,
            temperature=0.2,
            session_id=workspace.id,
            session_type="workspace",
            agent_name="requirements-conclusion",
        )
        content = result.content.strip()
        return content or None
    except Exception as exc:
        logger.warning(
            "requirements conclusion fell back to local heuristics: workspace=%s reason=%s",
            workspace.id,
            exc,
        )
        return None


async def generate_generic_stage_chat_reply_with_llm(
    db: AsyncSession,
    user: User,
    workspace: Workspace,
    stages: List[WorkspaceStage],
    stage: WorkspaceStage,
    messages: List[WorkspaceStageMessage],
    *,
    resolve_generation_model: Callable[[AsyncSession, User], Awaitable[tuple[Optional[str], Optional[str], Optional[List[str]]]]],
) -> Optional[str]:
    model, provider, fallback_chain = await resolve_generation_model(db, user)
    if not model or not provider:
        return None

    prompt = build_generic_stage_chat_prompt(workspace, stage, stages, messages)
    try:
        await llm_router.load_providers(db, user_id=user.id)
        result = await llm_router.call(
            messages=[
                LLMMessage(role="system", content=f"你是负责推进「{stage.title}」阶段的产品协作助手。"),
                LLMMessage(role="user", content=prompt),
            ],
            model=model,
            provider_name=provider,
            fallback_chain=fallback_chain,
            max_tokens=800,
            temperature=0.35,
            session_id=workspace.id,
            session_type="workspace",
            agent_name=f"{stage.stage_key.value}-conversation",
        )
        content = result.content.strip()
        return content or None
    except Exception as exc:
        logger.warning(
            "generic stage conversation fell back: workspace=%s stage=%s reason=%s",
            workspace.id,
            stage.stage_key.value,
            exc,
        )
        return None


async def generate_generic_stage_conclusion_with_llm(
    db: AsyncSession,
    user: User,
    workspace: Workspace,
    stages: List[WorkspaceStage],
    stage: WorkspaceStage,
    messages: List[WorkspaceStageMessage],
    *,
    resolve_generation_model: Callable[[AsyncSession, User], Awaitable[tuple[Optional[str], Optional[str], Optional[List[str]]]]],
) -> Optional[str]:
    model, provider, fallback_chain = await resolve_generation_model(db, user)
    if not model or not provider:
        return None

    prompt = build_generic_stage_conclusion_prompt(workspace, stage, stages, messages)
    try:
        await llm_router.load_providers(db, user_id=user.id)
        result = await llm_router.call(
            messages=[
                LLMMessage(role="system", content=f"你是负责整理「{stage.title}」阶段结论的产品协作助手。"),
                LLMMessage(role="user", content=prompt),
            ],
            model=model,
            provider_name=provider,
            fallback_chain=fallback_chain,
            max_tokens=1000,
            temperature=0.2,
            session_id=workspace.id,
            session_type="workspace",
            agent_name=f"{stage.stage_key.value}-conclusion",
        )
        content = result.content.strip()
        return content or None
    except Exception as exc:
        logger.warning(
            "generic stage conclusion fell back: workspace=%s stage=%s reason=%s",
            workspace.id,
            stage.stage_key.value,
            exc,
        )
        return None

async def generate_stage_conclusion_summary(
    db: AsyncSession,
    user: User,
    workspace: Workspace,
    stages: List[WorkspaceStage],
    stage: WorkspaceStage,
    messages: List[WorkspaceStageMessage],
    *,
    resolve_generation_model: Callable[[AsyncSession, User], Awaitable[tuple[Optional[str], Optional[str], Optional[List[str]]]]],
) -> str:
    if stage.stage_key == WorkspaceStageKey.REQUIREMENTS:
        summary = await generate_requirements_conclusion_with_llm(
            db=db,
            user=user,
            workspace=workspace,
            messages=messages,
            resolve_generation_model=resolve_generation_model,
        )
        return (summary or fallback_requirements_conclusion(workspace, messages)).strip()

    summary = await generate_generic_stage_conclusion_with_llm(
        db=db,
        user=user,
        workspace=workspace,
        stages=stages,
        stage=stage,
        messages=messages,
        resolve_generation_model=resolve_generation_model,
    )
    return (summary or (stage.content or "").strip() or "当前阶段已确认。").strip()


async def generate_requirements_stage_content(
    db: AsyncSession,
    user: User,
    workspace: Workspace,
    stages: List[WorkspaceStage],
    stage: WorkspaceStage,
    instruction: Optional[str],
    *,
    resolve_generation_model: Callable[[AsyncSession, User], Awaitable[tuple[Optional[str], Optional[str], Optional[List[str]]]]],
    messages: Optional[List[WorkspaceStageMessage]] = None,
) -> tuple[Dict[str, Any], str]:
    _ = stages
    _ = instruction
    if messages is not None:
        conversational = await generate_requirements_chat_reply_with_llm(
            db=db,
            user=user,
            workspace=workspace,
            messages=messages,
            resolve_generation_model=resolve_generation_model,
        )
        content = (conversational or build_requirements_chat_fallback(workspace, messages)).strip()
        recommendation = {
            "source": "requirements_conversation_v1",
            "summary": "这一阶段按对话方式持续推进，直到关键信息补齐。",
            "recommended_action": "继续补充；如果这一阶段已经完整，就明确表达可以收尾。",
            "focus": ["当前已确认的决定", "还缺什么", "是否可以进入下一阶段"],
            "options": [
                {
                    "title": "当前对话回复",
                    "description": "基于当前上下文给出的阶段内回复。",
                    "content": content,
                    "recommended": True,
                }
            ],
            "selected_option": "当前对话回复",
        }
        return recommendation, content

    return build_model_unavailable_stage_output(
        stage,
        summary="需求澄清阶段已经改成对话推进，不再生成固定分析稿。",
        recommended_action="请直接在当前阶段对话里补充需求，不再使用旧的生成入口。",
        focus=["阶段对话", "真实模型回复", "去模板化"],
        content="\n".join([
            "需求澄清阶段现在只走对话式推进。",
            "",
            "这条旧生成入口不再自动产出固定分析稿，避免把真实对话再次降级成模板。",
            "请直接进入当前阶段对话继续补充；系统会基于真实上下文回复，并在你确认后再整理阶段结论。",
        ]),
    )


async def generate_stage_chat_reply(
    db: AsyncSession,
    user: User,
    workspace: Workspace,
    stages: List[WorkspaceStage],
    stage: WorkspaceStage,
    instruction: str,
    *,
    resolve_generation_model: Callable[[AsyncSession, User], Awaitable[tuple[Optional[str], Optional[str], Optional[List[str]]]]],
    generate_stage_artifact_with_llm: Callable[[AsyncSession, User, Workspace, List[WorkspaceStage], WorkspaceStage, Optional[str]], Awaitable[Optional[tuple[Dict[str, Any], str]]]],
) -> tuple[Dict[str, Any], str]:
    if stage.stage_key == WorkspaceStageKey.REQUIREMENTS:
        return await generate_requirements_stage_content(
            db=db,
            user=user,
            workspace=workspace,
            stages=stages,
            stage=stage,
            instruction=instruction,
            resolve_generation_model=resolve_generation_model,
            messages=await get_stage_messages(db, stage.id),
        )

    chat_reply = await generate_generic_stage_chat_reply_with_llm(
        db=db,
        user=user,
        workspace=workspace,
        stages=stages,
        stage=stage,
        messages=await get_stage_messages(db, stage.id),
        resolve_generation_model=resolve_generation_model,
    )
    if chat_reply:
        recommendation = {
            "source": "stage_conversation_v1",
            "summary": f"{stage.title}阶段正在通过真实对话逐步收敛。",
            "recommended_action": "继续补充；如果这一阶段已经完整，就明确表达可以收尾。",
            "focus": [stage.title, "阶段对话", "收敛结论"],
            "options": [
                {
                    "title": "当前对话回复",
                    "description": "基于当前阶段上下文给出的继续推进回复。",
                    "content": chat_reply,
                    "recommended": True,
                }
            ],
            "selected_option": "当前对话回复",
        }
        return recommendation, chat_reply
    return build_model_unavailable_stage_output(
        stage,
        summary=f"{stage.title}阶段当前没有拿到可用的大模型回复。",
        recommended_action="请先检查模型配置；恢复后再继续当前阶段。",
        focus=["模型可用性", "避免规则兜底", "恢复真实阶段输出"],
    )


async def generate_and_append_stage_reply(
    db: AsyncSession,
    user: User,
    workspace: Workspace,
    stages: List[WorkspaceStage],
    stage: WorkspaceStage,
    messages: List[WorkspaceStageMessage],
    *,
    resolve_generation_model: Callable[[AsyncSession, User], Awaitable[tuple[Optional[str], Optional[str], Optional[List[str]]]]],
    generate_stage_artifact_with_llm: Callable[[AsyncSession, User, Workspace, List[WorkspaceStage], WorkspaceStage, Optional[str]], Awaitable[Optional[tuple[Dict[str, Any], str]]]],
) -> WorkspaceStageMessage:
    latest_user_message = next((item.content.strip() for item in reversed(messages) if item.role == "user"), None)
    instruction = (
        latest_user_message
        if stage.stage_key == WorkspaceStageKey.REQUIREMENTS and latest_user_message
        else build_stage_chat_instruction(workspace, stage, messages)
    )
    recommendation, content = await generate_stage_chat_reply(
        db=db,
        user=user,
        workspace=workspace,
        stages=stages,
        stage=stage,
        instruction=instruction,
        resolve_generation_model=resolve_generation_model,
        generate_stage_artifact_with_llm=generate_stage_artifact_with_llm,
    )
    stage.recommendation_json = json.dumps(recommendation, ensure_ascii=False)
    stage.content = content
    stage.user_feedback = latest_user_message
    stage.status = WorkspaceStageStatus.AWAITING_CONFIRMATION
    stage.approved_by = None
    stage.approved_at = None
    workspace.updated_at = datetime.now(timezone.utc)
    return await append_stage_message(
        db=db,
        stage=stage,
        role="assistant",
        content=content,
        kind="chat",
    )


async def bootstrap_stage_messages(
    db: AsyncSession,
    user: User,
    workspace: Workspace,
    stages: List[WorkspaceStage],
    stage: WorkspaceStage,
    *,
    resolve_generation_model: Callable[[AsyncSession, User], Awaitable[tuple[Optional[str], Optional[str], Optional[List[str]]]]],
    generate_stage_artifact_with_llm: Callable[[AsyncSession, User, Workspace, List[WorkspaceStage], WorkspaceStage, Optional[str]], Awaitable[Optional[tuple[Dict[str, Any], str]]]],
) -> List[WorkspaceStageMessage]:
    messages = await get_stage_messages(db, stage.id)
    if messages or stage.status == WorkspaceStageStatus.APPROVED:
        return messages

    messages = await ensure_stage_seed_messages(
        db=db,
        workspace=workspace,
        stage=stage,
        existing_messages=messages,
    )

    assistant_message = await generate_and_append_stage_reply(
        db=db,
        user=user,
        workspace=workspace,
        stages=stages,
        stage=stage,
        messages=messages,
        resolve_generation_model=resolve_generation_model,
        generate_stage_artifact_with_llm=generate_stage_artifact_with_llm,
    )
    return [*messages, assistant_message]


async def ensure_stage_seed_messages(
    db: AsyncSession,
    workspace: Workspace,
    stage: WorkspaceStage,
    existing_messages: Optional[List[WorkspaceStageMessage]] = None,
) -> List[WorkspaceStageMessage]:
    messages = existing_messages if existing_messages is not None else await get_stage_messages(db, stage.id)
    if stage.stage_key == WorkspaceStageKey.REQUIREMENTS:
        initial_requirement = (workspace.description or workspace.name or "").strip()
        has_user_message = any(item.role == "user" and item.content.strip() for item in messages)
        if initial_requirement and not has_user_message:
            await append_stage_message(
                db=db,
                stage=stage,
                role="user",
                content=initial_requirement,
                kind="chat",
            )
            messages = await get_stage_messages(db, stage.id)
    return messages


async def finalize_stage_conclusion(
    db: AsyncSession,
    user: User,
    workspace: Workspace,
    stages: List[WorkspaceStage],
    stage: WorkspaceStage,
    messages: List[WorkspaceStageMessage],
    *,
    resolve_generation_model: Callable[[AsyncSession, User], Awaitable[tuple[Optional[str], Optional[str], Optional[List[str]]]]],
    create_workspace_artifact: Callable[..., Awaitable[Artifact]],
    load_recommendation: Callable[[WorkspaceStage], Dict[str, Any]],
    upsert_artifact_reference: Callable[[Dict[str, Any], Artifact, str], Dict[str, Any]],
) -> WorkspaceStageMessage:
    transcript = []
    for message in messages:
        speaker = "用户" if message.role == "user" else "系统"
        transcript.append(f"## {speaker}\n{message.content}")
    body = "\n\n".join(transcript).strip()
    summary = await generate_stage_conclusion_summary(
        db=db,
        user=user,
        workspace=workspace,
        stages=stages,
        stage=stage,
        messages=messages,
        resolve_generation_model=resolve_generation_model,
    )
    md_content = "\n".join([
        f"# {workspace.name} - {stage.title}",
        "",
        "## 阶段结论",
        "",
        summary,
        "",
        "## 对话记录",
        "",
        body,
    ]).strip()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    artifact = await create_workspace_artifact(
        db=db,
        workspace=workspace,
        user=user,
        artifact_type=f"{stage.stage_key.value}_conclusion",
        filename=f"{stage.stage_key.value}-conclusion-{timestamp}.md",
        content=md_content.encode("utf-8"),
        mime_type="text/markdown",
    )
    recommendation = load_recommendation(stage)
    recommendation = upsert_artifact_reference(recommendation, artifact, f"{stage.title}结论文档")
    stage.recommendation_json = json.dumps(recommendation, ensure_ascii=False)
    stage.content = summary
    stage.status = WorkspaceStageStatus.AWAITING_CONFIRMATION
    stage.approved_by = None
    stage.approved_at = None
    workspace.updated_at = datetime.now(timezone.utc)
    return await append_stage_message(
        db=db,
        stage=stage,
        role="assistant",
        content=summary,
        kind="conclusion",
        artifact_id=artifact.id,
    )


async def stream_stage_reply(
    db: AsyncSession,
    user: User,
    workspace: Workspace,
    stages: List[WorkspaceStage],
    stage: WorkspaceStage,
    messages: List[WorkspaceStageMessage],
    *,
    resolve_generation_model: Callable[[AsyncSession, User], Awaitable[tuple[Optional[str], Optional[str], Optional[List[str]]]]],
    create_workspace_artifact: Callable[..., Awaitable[Artifact]],
    load_recommendation: Callable[[WorkspaceStage], Dict[str, Any]],
    upsert_artifact_reference: Callable[[Dict[str, Any], Artifact, str], Dict[str, Any]],
    serialize_stage_response: Callable[[WorkspaceStage], Dict[str, Any]],
    serialize_stage_message_response: Callable[[WorkspaceStageMessage], Dict[str, Any]],
    parse_json_object: Callable[[str], Dict[str, Any]],
    force_finalize: bool = False,
):
    async def event_generator():
        built_content = ""
        built_reasoning = ""
        try:
            recommendation: Optional[Dict[str, Any]] = None
            should_finalize = force_finalize or await should_finalize_stage_message(
                db=db,
                user=user,
                workspace=workspace,
                stage=stage,
                messages=messages,
                resolve_generation_model=resolve_generation_model,
                parse_json_object=parse_json_object,
            )

            if should_finalize:
                prompt = (
                    build_requirements_conclusion_prompt(workspace, messages)
                    if stage.stage_key == WorkspaceStageKey.REQUIREMENTS
                    else build_generic_stage_conclusion_prompt(workspace, stage, stages, messages)
                )
                system_text = (
                    "你是一个擅长整理项目需求结论的产品负责人。"
                    if stage.stage_key == WorkspaceStageKey.REQUIREMENTS
                    else f"你是负责整理「{stage.title}」阶段结论的产品协作助手。"
                )
                agent_name = f"{stage.stage_key.value}-conclusion"
            else:
                prompt = (
                    build_requirements_chat_prompt(workspace, messages)
                    if stage.stage_key == WorkspaceStageKey.REQUIREMENTS
                    else build_generic_stage_chat_prompt(workspace, stage, stages, messages)
                )
                system_text = (
                    "你是一个擅长需求澄清和项目推进的产品负责人。"
                    if stage.stage_key == WorkspaceStageKey.REQUIREMENTS
                    else f"你是负责推进「{stage.title}」阶段的产品协作助手。"
                )
                agent_name = (
                    "requirements-conversation"
                    if stage.stage_key == WorkspaceStageKey.REQUIREMENTS
                    else f"{stage.stage_key.value}-conversation"
                )
                recommendation = {
                    "source": "stream_conversation",
                    "summary": f"{stage.title}阶段正在通过真实模型对话推进。",
                    "recommended_action": "继续在当前阶段补充；确认足够后再让系统整理结论。",
                    "focus": [stage.title, "真实模型回复", "阶段收敛"],
                    "options": [],
                    "selected_option": None,
                }

            stream, model, provider = await stream_llm_text(
                db=db,
                user=user,
                messages=[
                    LLMMessage(role="system", content=system_text),
                    LLMMessage(role="user", content=prompt),
                ],
                session_id=workspace.id,
                agent_name=agent_name,
                resolve_generation_model=resolve_generation_model,
                max_tokens=1000 if should_finalize else 850,
                temperature=0.2 if should_finalize else 0.35,
            )
            if stream is None:
                yield {"event": "error", "data": "没有可用的大模型配置，无法生成真实回复。"}
                return

            async for item in stream:
                item_type = str(item.get("type") or "")
                piece = str(item.get("content") or "")
                if not piece:
                    continue
                if item_type == "reasoning":
                    built_reasoning += piece
                    yield {"event": "reasoning", "data": piece}
                    continue
                built_content += piece
                yield {"event": "content", "data": piece}

            if not built_content.strip():
                yield {"event": "error", "data": "模型没有返回正文内容，请检查模型是否支持当前流式接口。"}
                return

            if should_finalize:
                summary = built_content.strip()
                transcript = []
                for message in messages:
                    speaker = "用户" if message.role == "user" else "系统"
                    transcript.append(f"## {speaker}\n{message.content}")
                body = "\n\n".join(transcript).strip()
                md_content = "\n".join([
                    f"# {workspace.name} - {stage.title}",
                    "",
                    "## 阶段结论",
                    "",
                    summary,
                    "",
                    "## 对话记录",
                    "",
                    body,
                ]).strip()
                timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
                artifact = await create_workspace_artifact(
                    db=db,
                    workspace=workspace,
                    user=user,
                    artifact_type=f"{stage.stage_key.value}_conclusion",
                    filename=f"{stage.stage_key.value}-conclusion-{timestamp}.md",
                    content=md_content.encode("utf-8"),
                    mime_type="text/markdown",
                )
                conclusion_recommendation = load_recommendation(stage)
                conclusion_recommendation = upsert_artifact_reference(
                    conclusion_recommendation,
                    artifact,
                    f"{stage.title}结论文档",
                )
                stage.recommendation_json = json.dumps(conclusion_recommendation, ensure_ascii=False)
                stage.content = summary
                stage.status = WorkspaceStageStatus.AWAITING_CONFIRMATION
                stage.approved_by = None
                stage.approved_at = None
                workspace.updated_at = datetime.now(timezone.utc)
                assistant_message = await append_stage_message(
                    db=db,
                    stage=stage,
                    role="assistant",
                    content=summary,
                    kind="conclusion",
                    artifact_id=artifact.id,
                )
            else:
                final_content = built_content.strip()
                stage.recommendation_json = json.dumps(recommendation or {}, ensure_ascii=False)
                stage.content = final_content
                stage.user_feedback = messages[-1].content if messages else None
                stage.status = WorkspaceStageStatus.AWAITING_CONFIRMATION
                stage.approved_by = None
                stage.approved_at = None
                workspace.updated_at = datetime.now(timezone.utc)
                assistant_message = await append_stage_message(
                    db=db,
                    stage=stage,
                    role="assistant",
                    content=final_content,
                    kind="chat",
                )

            await db.commit()
            await db.refresh(stage)
            yield {
                "event": "complete",
                "data": json.dumps(
                    {
                        "stage": serialize_stage_response(stage),
                        "message": serialize_stage_message_response(assistant_message),
                        "reasoning": built_reasoning,
                        "model": model,
                        "provider": provider,
                    },
                    ensure_ascii=False,
                ),
            }
        except Exception as exc:
            logger.exception(
                "stage stream failed: workspace=%s stage=%s",
                workspace.id,
                stage.stage_key.value,
            )
            if db.in_transaction():
                await db.rollback()
            message = "模型流式生成或阶段产物写入失败，请查看后端日志。"
            if isinstance(exc, LLMError):
                message = str(exc)
            yield {"event": "error", "data": message}

    return event_generator()


async def stream_requirements_stage_document(
    db: AsyncSession,
    user: User,
    workspace: Workspace,
    stages: List[WorkspaceStage],
    stage: WorkspaceStage,
    instruction: Optional[str],
    *,
    resolve_generation_model: Callable[[AsyncSession, User], Awaitable[tuple[Optional[str], Optional[str], Optional[List[str]]]]],
    build_model_unavailable_stage_output: Callable[..., tuple[Dict[str, Any], str]],
    generate_requirements_stage_content: Callable[..., Awaitable[tuple[Dict[str, Any], str]]],
    serialize_stage_response: Callable[[WorkspaceStage], Dict[str, Any]],
):
    model, provider, _fallback_chain = await resolve_generation_model(db, user)
    if not model or not provider:
        recommendation, content = build_model_unavailable_stage_output(
            stage,
            summary="需求澄清阶段当前没有拿到可用的大模型回复。",
            recommended_action="请先检查模型配置；恢复后再回到阶段对话继续。",
            focus=["模型可用性", "阶段对话", "避免旧生成稿"],
            content="\n".join([
                "需求澄清阶段当前没有拿到可用的大模型回复。",
                "",
                "为了避免继续走旧的固定分析稿生成逻辑，这个入口现在不会再自动产出模板内容。",
                "请先检查模型配置；恢复后再回到当前阶段对话继续。",
            ]),
        )

        async def fallback_generator():
            built = ""
            for chunk in re.findall(r".{1,48}", content, flags=re.S):
                built += chunk
                yield {"event": "content", "data": chunk}
                await asyncio.sleep(0.02)

            stage.recommendation_json = json.dumps(recommendation, ensure_ascii=False)
            stage.content = built
            stage.status = WorkspaceStageStatus.AWAITING_CONFIRMATION
            stage.approved_by = None
            stage.approved_at = None
            workspace.updated_at = datetime.now(timezone.utc)
            await db.commit()
            await db.refresh(stage)
            yield {"event": "complete", "data": json.dumps(serialize_stage_response(stage), ensure_ascii=False)}

        return fallback_generator()

    async def event_generator():
        try:
            recommendation, normalized = await generate_requirements_stage_content(
                db=db,
                user=user,
                workspace=workspace,
                stages=stages,
                stage=stage,
                instruction=instruction,
                resolve_generation_model=resolve_generation_model,
            )
            if not normalized:
                raise LLMError("requirements interpretation returned empty content")

            for piece in re.findall(r".{1,48}", normalized, flags=re.S):
                yield {"event": "content", "data": piece}
                await asyncio.sleep(0.01)
        except Exception as exc:
            logger.warning("requirements stream failed, fallback to sync generation: workspace=%s reason=%s", workspace.id, exc)
            recommendation, content = await generate_requirements_stage_content(
                db=db,
                user=user,
                workspace=workspace,
                stages=stages,
                stage=stage,
                instruction=instruction,
                resolve_generation_model=resolve_generation_model,
            )
            full_text = ""
            for piece in re.findall(r".{1,48}", content, flags=re.S):
                full_text += piece
                yield {"event": "content", "data": piece}
                await asyncio.sleep(0.02)
            stage.recommendation_json = json.dumps(recommendation, ensure_ascii=False)
            stage.content = full_text
            stage.status = WorkspaceStageStatus.AWAITING_CONFIRMATION
            stage.approved_by = None
            stage.approved_at = None
            workspace.updated_at = datetime.now(timezone.utc)
            await db.commit()
            await db.refresh(stage)
            yield {"event": "complete", "data": json.dumps(serialize_stage_response(stage), ensure_ascii=False)}
            return

        stage.recommendation_json = json.dumps(recommendation, ensure_ascii=False)
        stage.content = normalized
        stage.status = WorkspaceStageStatus.AWAITING_CONFIRMATION
        stage.approved_by = None
        stage.approved_at = None
        workspace.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(stage)
        yield {"event": "complete", "data": json.dumps(serialize_stage_response(stage), ensure_ascii=False)}

    return event_generator()
