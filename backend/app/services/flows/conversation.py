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
from app.services.flows.memory import build_workspace_memory_context
from app.services.flows.prompts import (
    build_auxiliary_context_block,
    build_finalize_not_ready_instruction,
    build_generic_stage_chat_prompt,
    build_requirements_chat_prompt,
    build_requirements_conclusion_prompt,
    build_stage_finalization_readiness_prompt,
    build_stage_output_completion_prompt,
    build_stage_output_continuation_prompt,
    build_stage_readiness_instruction,
    format_bullets,
    merge_stage_instructions,
    messages_to_prompt_text,
    recent_messages_to_prompt_text,
    requirements_latest_user_message,
    requirements_user_messages,
    stage_responsibility_contract,
    stage_completion_requirements,
    stage_document_guidance,
)
from app.services.flows.runtime_state import (
    apply_stage_runtime_metadata,
    build_blocker_instruction,
    build_blocker_state,
    latest_user_message_id,
    stage_blocker_state,
    stage_is_ready_to_finalize,
    stage_runtime_metadata,
    update_blockers_after_assistant_reply,
)
from app.services.flows.stage_generation import build_external_reference_context, build_stage_skill_context
from app.services.flows.stage_snapshots import (
    collect_stage_artifacts,
    render_final_delivery_summary,
    with_stage_snapshot,
)

logger = logging.getLogger(__name__)
MAX_STAGE_OUTPUT_CONTINUATION_ROUNDS = 2


FINALIZE_INTENTS = {"closure_signal", "explicit_finalize_request"}


def should_discuss_stage_conclusion(finalize_intent: str) -> bool:
    return finalize_intent in FINALIZE_INTENTS


def build_new_information_chat_instruction() -> str:
    return """
先判断用户最新消息是在追问解释、纠正前文、补充需求，还是要求继续推进。
如果是在追问上一轮表达，先把上一轮没说明白的地方解释清楚。
如果是在补充需求，再更新当前理解。
不要生成阶段文档，不要展开下一阶段内容。
""".strip()


def build_first_turn_chat_instruction(stage: WorkspaceStage) -> str:
    return f"""
这是「{stage.title}」阶段的第一轮正式回复。
直接基于用户刚给出的信息表达当前理解，不要用总结式或结束式开场。
只说当前输入已经支持的内容；如果确实还缺关键前提，最后最多补 1 个问题。
""".strip()


def build_regular_stage_chat_instruction() -> str:
    return """
这是一轮常规阶段对话。
先回应用户最新消息本身，再结合已有上下文继续。
不要主动生成阶段文档，也不要顺手展开下一阶段内容。
""".strip()


def stage_chat_max_tokens(stage_key: WorkspaceStageKey) -> int:
    if stage_key == WorkspaceStageKey.REQUIREMENTS:
        return 480
    if stage_key == WorkspaceStageKey.PRODUCT:
        return 900
    if stage_key in {WorkspaceStageKey.UI_DIRECTION, WorkspaceStageKey.TECHNICAL}:
        return 780
    return 640


def stage_conclusion_max_tokens(stage_key: WorkspaceStageKey) -> int:
    if stage_key == WorkspaceStageKey.REQUIREMENTS:
        return 900
    if stage_key == WorkspaceStageKey.PRODUCT:
        return 1600
    if stage_key in {WorkspaceStageKey.UI_DIRECTION, WorkspaceStageKey.TECHNICAL}:
        return 1400
    return 1200


def runtime_reasoning_effort(runtime_options: Optional[Dict[str, Any]]) -> Optional[str]:
    if not runtime_options:
        return None
    value = str(runtime_options.get("reasoning_effort") or "").strip().lower()
    return value or None


def stage_runtime_reasoning_effort(
    stage_key: WorkspaceStageKey,
    runtime_options: Optional[Dict[str, Any]],
) -> Optional[str]:
    if stage_key == WorkspaceStageKey.REQUIREMENTS:
        return "low"
    return runtime_reasoning_effort(runtime_options)


def parse_json_object_loose(content: str) -> Optional[Dict[str, Any]]:
    text = content.strip()
    if not text:
        return None
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            return None
        try:
            value = json.loads(match.group(0))
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            return None


def _strip_terminal_markdown(text: str) -> str:
    stripped = (text or "").rstrip()
    while stripped and stripped[-1] in "*_`~":
        stripped = stripped[:-1].rstrip()
    return stripped


def looks_like_truncated_output(content: str) -> bool:
    stripped = _strip_terminal_markdown(content)
    if not stripped:
        return True
    if "�" in stripped:
        return True
    if stripped.endswith(("：", ":", "（", "(", "[", "{", "、", "，", ",", "-", "*", "#")):
        return True
    if re.search(r"(?:^|\n)\s*\d+\.\s*$", stripped):
        return True
    if re.search(r"(?:^|\n)\s*[-*]\s*$", stripped):
        return True
    if re.search(r"[A-Za-z0-9\u4e00-\u9fff]\s*�$", stripped):
        return True
    if re.search(r"(?:^|\n)\s*\d+\.\s+\S+\s*$", stripped) and not stripped.endswith(("。", "！", "？", "!", "?")):
        return True
    return False


def looks_like_incomplete_stage_chat_reply(content: str) -> bool:
    stripped = _strip_terminal_markdown(content)
    if not stripped:
        return True
    if looks_like_truncated_output(stripped):
        return True
    if stripped.endswith(("。", "！", "？", "!", "?")):
        return False
    return True


def _split_cn_sentences(content: str) -> List[str]:
    text = re.sub(r"[ \t]+", " ", (content or "").strip())
    if not text:
        return []
    parts = re.findall(r'[^。！？!?]+[。！？!?]?|.+$', text)
    return [part.strip() for part in parts if part and part.strip()]


def has_stage_user_input(messages: List[WorkspaceStageMessage]) -> bool:
    return any(item.role == "user" and item.content.strip() for item in messages)


def _active_blocker_texts(stage: WorkspaceStage) -> List[str]:
    return [
        str(item.get("text") or "").strip()
        for item in stage_blocker_state(stage)
        if str(item.get("status") or "").strip() != "closed" and str(item.get("text") or "").strip()
    ][:3]


def _last_safe_break_index(content: str) -> int:
    text = content or ""
    if not text:
        return 0

    safe_break = 0
    for match in re.finditer(r"(?:[。！？!?](?:[\"'”’」】）)]*)|(?:\n\s*\n)|(?:\n(?=\s*(?:[-*]\s+|\d+\.\s+|#{1,6}\s+))))", text):
        safe_break = match.end()
    return safe_break


def _merge_stage_output(existing: str, addition: str) -> str:
    left = existing or ""
    right = addition or ""
    if not left:
        return right
    if not right:
        return left
    if right.startswith(left):
        return right
    if left.endswith(right):
        return left

    max_overlap = min(len(left), len(right))
    for size in range(max_overlap, 0, -1):
        if left[-size:] == right[:size]:
            return left + right[size:]
    return left + right


def _streamable_delta(current: str, emitted: str, *, force_flush: bool = False) -> tuple[str, str]:
    if not current:
        return "", emitted

    flush_upto = len(current) if force_flush else _last_safe_break_index(current)
    if flush_upto <= len(emitted):
        return "", emitted
    return current[len(emitted):flush_upto], current[:flush_upto]


def normalize_sse_buffer(content: str) -> str:
    text = str(content or "").replace("\r\n", "\n").replace("\r", "\n")
    if not text:
        return ""

    current_event = ""
    saw_sse = False
    chunks: List[str] = []

    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            if saw_sse and chunks and chunks[-1] != "":
                chunks.append("")
            elif not saw_sse:
                chunks.append("")
            continue
        if line.startswith(":"):
            saw_sse = True
            continue
        if line.startswith("event:"):
            saw_sse = True
            current_event = line[6:].strip().lower()
            continue
        if line.startswith("data:"):
            saw_sse = True
            payload = line[5:].lstrip()
            if payload == "[DONE]":
                continue
            if current_event in {"", "content"}:
                chunks.append(payload)
            continue
        chunks.append(raw_line)

    normalized = "\n".join(chunks).strip()
    return normalized if saw_sse else text.strip()


def normalize_stage_output_paragraphs(content: str, *, should_finalize: bool) -> str:
    text = normalize_sse_buffer(content).strip()
    if not text:
        return text

    if "\n\n" in text:
        return text
    if re.search(r"^#{1,6}\s+|^\s*[-*]\s+|^\s*\d+\.\s+", text, re.MULTILINE):
        return text

    sentences = _split_cn_sentences(text)
    if len(sentences) < 3:
        return text

    paragraphs: List[str] = []
    current: List[str] = []
    limit = 3 if should_finalize else 2

    for index, sentence in enumerate(sentences):
        current.append(sentence)
        is_question = sentence.endswith(("？", "?"))
        is_last = index == len(sentences) - 1
        if is_question or len(current) >= limit:
            paragraphs.append("".join(current).strip())
            current = []
            continue
        if is_last:
            paragraphs.append("".join(current).strip())
            current = []

    if current:
        paragraphs.append("".join(current).strip())

    normalized = "\n\n".join(part for part in paragraphs if part) or text
    return normalized


def _normalize_dedupe_key(value: str) -> str:
    return re.sub(r"\s+", "", (value or "").strip()).lower()


def _looks_like_followup_bootstrap_block(block: str) -> bool:
    normalized = normalize_sse_buffer(block).strip()
    if not normalized:
        return False

    compact = _normalize_dedupe_key(normalized)
    if compact in {"---", "***", "___"}:
        return False

    question_count = normalized.count("？") + normalized.count("?")
    option_count = len(re.findall(r"(?:^|\n)\s*(?:[-*]|\d+\.)\s+", normalized))
    has_choice_language = any(token in normalized for token in ("请选择", "更倾向", "怎么选", "选哪种", "你的想法", "你来定"))
    has_framework_language = any(token in normalized for token in ("讨论框架", "对齐方向", "需要你做的决策", "逐一确认"))
    has_option_labels = bool(re.search(r"方案\s*[A-ZＡ-Ｚ0-9一二三四]", normalized))

    if has_option_labels:
        return True
    if has_framework_language and (question_count > 0 or option_count >= 2):
        return True
    if has_choice_language and question_count > 0:
        return True
    if question_count >= 2 and option_count >= 2:
        return True
    return False


def _trim_stage_document_tail(content: str) -> str:
    text = normalize_sse_buffer(content).strip()
    if not text:
        return text

    blocks = [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]
    if len(blocks) <= 2:
        return text

    kept: List[str] = []
    for index, block in enumerate(blocks):
        if index >= 2 and _looks_like_followup_bootstrap_block(block):
            break
        kept.append(block)

    while kept and _normalize_dedupe_key(kept[-1]) in {"---", "***", "___"}:
        kept.pop()

    trimmed = "\n\n".join(kept).strip()
    trimmed = re.sub(r"\n{3,}", "\n\n", trimmed)
    return trimmed


def cleanup_stage_document_markdown(stage: WorkspaceStage, content: str) -> str:
    text = _trim_stage_document_tail(content)
    if not text:
        return text

    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    cleaned_lines: List[str] = []
    previous_non_empty = ""
    for line in lines:
        current = line.rstrip()
        normalized = _normalize_dedupe_key(current)
        if normalized and normalized == previous_non_empty:
            continue
        cleaned_lines.append(current)
        if normalized:
            previous_non_empty = normalized

    text = "\n".join(cleaned_lines).strip()
    blocks = re.split(r"\n\s*\n", text)
    cleaned_blocks: List[str] = []
    previous_block = ""
    for block in blocks:
        current = block.strip()
        if not current:
            continue
        normalized = _normalize_dedupe_key(current)
        if normalized == previous_block:
            continue
        cleaned_blocks.append(current)
        previous_block = normalized

    text = "\n\n".join(cleaned_blocks).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


REQUIREMENTS_OFF_SCOPE_QUESTION_PATTERNS = (
    r"(?:需要|要|有哪些|包含哪些|设计|规划|拆分|列出).{0,8}(?:功能模块|功能清单|模块)",
    r"(?:页面|界面).{0,8}(?:怎么|如何|设计|规划|布局|结构)",
    r"(?:后台|管理端).{0,8}(?:怎么|如何|设计|做|规划)",
    r"(?:权限|角色).{0,8}(?:怎么|如何|设计|划分|分)",
    r"(?:技术|技术栈|架构).{0,8}(?:怎么|如何|选|设计|规划)",
    r"(?:接口|API|数据库|字段|数据表).{0,8}(?:怎么|如何|设计|规划|定义)",
    r"(?:完整流程|业务流程).{0,8}(?:是什么|怎么|如何|设计|规划|梳理)",
)


def _requirements_reply_has_off_scope_question(content: str) -> bool:
    sentences = _split_cn_sentences(normalize_sse_buffer(content))
    for sentence in sentences:
        if not sentence.endswith(("？", "?")):
            continue
        if any(re.search(pattern, sentence, re.I) for pattern in REQUIREMENTS_OFF_SCOPE_QUESTION_PATTERNS):
            return True
    return False


def _remove_requirements_off_scope_questions(content: str) -> str:
    sentences = _split_cn_sentences(normalize_sse_buffer(content))
    if not sentences:
        return normalize_sse_buffer(content)

    kept = [
        sentence
        for sentence in sentences
        if not (
            sentence.endswith(("？", "?"))
            and any(re.search(pattern, sentence, re.I) for pattern in REQUIREMENTS_OFF_SCOPE_QUESTION_PATTERNS)
        )
    ]
    cleaned = "".join(kept).strip()
    return normalize_stage_output_paragraphs(cleaned, should_finalize=False)


async def rewrite_stage_chat_reply_if_needed(
    db: AsyncSession,
    user: User,
    workspace: Workspace,
    stage: WorkspaceStage,
    messages: List[WorkspaceStageMessage],
    *,
    latest_input: str,
    memory_context: str,
    draft_reply: str,
    runtime_options: Optional[Dict[str, Any]],
    resolve_generation_model: Callable[[AsyncSession, User], Awaitable[tuple[Optional[str], Optional[str], Optional[List[str]]]]],
) -> str:
    _ = (
        db,
        user,
        workspace,
        stage,
        messages,
        latest_input,
        memory_context,
        runtime_options,
        resolve_generation_model,
    )
    if (
        stage.stage_key == WorkspaceStageKey.REQUIREMENTS
        and _requirements_reply_has_off_scope_question(draft_reply)
    ):
        cleaned_reply = _remove_requirements_off_scope_questions(draft_reply)
        if cleaned_reply:
            return cleaned_reply
    return draft_reply


async def is_stage_output_complete(
    db: AsyncSession,
    user: User,
    workspace: Workspace,
    stage: WorkspaceStage,
    *,
    content: str,
    should_finalize: bool,
    resolve_generation_model: Callable[[AsyncSession, User], Awaitable[tuple[Optional[str], Optional[str], Optional[List[str]]]]],
) -> bool:
    if looks_like_truncated_output(content):
        return False
    if not should_finalize:
        return not looks_like_incomplete_stage_chat_reply(content)
    if stage.stage_key == WorkspaceStageKey.DEPLOYMENT:
        normalized = normalize_sse_buffer(content).strip()
        if looks_like_incomplete_stage_chat_reply(normalized):
            return False
        if "## 项目最终结论" in normalized and "## 最终交付物说明" in normalized:
            return True
        if len(normalized) >= 500 and any(
            key in normalized for key in ("产品结论", "方案结论", "规则", "开发", "交付物")
        ):
            return True
        return False

    model, provider, fallback_chain = await resolve_generation_model(db, user)
    if not model or not provider:
        return True

    prompt = build_stage_output_completion_prompt(
        stage,
        should_finalize=should_finalize,
        content=content,
    )
    try:
        await llm_router.load_providers(db, user_id=user.id)
        result = await llm_router.call(
            messages=[
                LLMMessage(role="system", content="你是一个负责判断阶段输出是否已经完整的助手。"),
                LLMMessage(role="user", content=prompt),
            ],
            model=model,
            provider_name=provider,
            fallback_chain=fallback_chain,
            max_tokens=180,
            temperature=0.0,
            session_id=workspace.id,
            session_type="workspace",
            agent_name="stage-output-completeness-judge",
        )
        parsed = parse_json_object_loose(result.content or "")
        if parsed is None:
            return not looks_like_truncated_output(content) and not looks_like_incomplete_stage_chat_reply(content)
        return bool(parsed.get("is_complete")) and not looks_like_incomplete_stage_chat_reply(content)
    except Exception as exc:
        logger.warning(
            "stage output completeness check fell back to heuristics: workspace=%s stage=%s reason=%s",
            workspace.id,
            stage.stage_key.value,
            exc,
        )
        return not looks_like_truncated_output(content) and not looks_like_incomplete_stage_chat_reply(content)


async def assess_stage_finalization_readiness(
    db: AsyncSession,
    user: User,
    workspace: Workspace,
    stage: WorkspaceStage,
    messages: List[WorkspaceStageMessage],
    *,
    resolve_generation_model: Callable[[AsyncSession, User], Awaitable[tuple[Optional[str], Optional[str], Optional[List[str]]]]],
) -> tuple[bool, List[str]]:
    if not has_stage_user_input(messages):
        return False, ["当前阶段还没有有效用户输入，暂时还不能结束。"]

    model, provider, fallback_chain = await resolve_generation_model(db, user)
    if not model or not provider:
        existing_blockers = _active_blocker_texts(stage)
        return False, existing_blockers or ["当前无法可靠判断阶段信息是否已经足够，请先继续当前对话或稍后重试。"]

    prompt = build_stage_finalization_readiness_prompt(workspace, stage, messages)
    try:
        await llm_router.load_providers(db, user_id=user.id)
        result = await llm_router.call(
            messages=[
                LLMMessage(role="system", content="你是一个负责判断阶段是否可以正式结束的助手。"),
                LLMMessage(role="user", content=prompt),
            ],
            model=model,
            provider_name=provider,
            fallback_chain=fallback_chain,
            max_tokens=260,
            temperature=0.0,
            session_id=workspace.id,
            session_type="workspace",
            agent_name="stage-finalization-readiness-judge",
        )
        parsed = parse_json_object_loose(result.content or "")
        if parsed is None:
            existing_blockers = _active_blocker_texts(stage)
            return False, existing_blockers or ["当前阶段结束判断暂时不稳定，请继续当前对话或稍后重试。"]
        blockers = parsed.get("blockers")
        normalized_blockers = [
            str(item).strip()
            for item in (blockers if isinstance(blockers, list) else [])
            if str(item).strip()
        ][:3]
        can_finalize = bool(parsed.get("can_finalize"))
        if can_finalize:
            return True, []
        if normalized_blockers:
            return False, normalized_blockers
        existing_blockers = _active_blocker_texts(stage)
        return False, existing_blockers or ["当前阶段暂时还不能自动结束，请继续当前对话或稍后重试。"]
    except Exception as exc:
        logger.warning(
            "stage finalization readiness check failed conservatively: workspace=%s stage=%s reason=%s",
            workspace.id,
            stage.stage_key.value,
            exc,
        )
        existing_blockers = _active_blocker_texts(stage)
        return False, existing_blockers or ["当前阶段结束判断失败，请继续当前对话或稍后重试。"]


async def assess_latest_user_message_finalize_intent(
    db: AsyncSession,
    user: User,
    workspace: Workspace,
    stage: WorkspaceStage,
    messages: List[WorkspaceStageMessage],
    *,
    resolve_generation_model: Callable[[AsyncSession, User], Awaitable[tuple[Optional[str], Optional[str], Optional[List[str]]]]],
) -> str:
    latest_user_message = ""
    last_assistant_message = ""
    for item in reversed(messages):
        if not latest_user_message and item.role == "user" and item.content.strip():
            latest_user_message = item.content.strip()
            continue
        if latest_user_message and item.role == "assistant" and item.content.strip():
            last_assistant_message = item.content.strip()
            break

    if not latest_user_message:
        return "unknown"

    model, provider, fallback_chain = await resolve_generation_model(db, user)
    if not model or not provider:
        return "unknown"

    prompt = f"""
你要判断用户这条最新消息，在当前阶段里更接近哪一种意图。

可选分类只有三个：
1. new_information
   含义：用户在补充、修改、纠偏新的产品/方案/规则/实现信息。
   这种情况下，应该先继续正常对话，不要直接整理阶段结论。

2. closure_signal
   含义：用户没有明显补充新信息，主要是在认可当前理解、表达收束、回应推进询问，或者只是轻量回应。
   这种情况下，如果阶段内容本身也已经足够，可以整理阶段结论。

3. explicit_finalize_request
   含义：用户明确要求结束当前阶段、整理当前结论，或进入后续阶段。
   这种情况下，可以直接整理阶段结论。

判断原则：
- 按语义和上下文判断，不按固定词表或字面关键词判断。
- 先看用户这句话有没有新增事实、新限制、新方向或纠正；只要会改变当前阶段理解，就判为 new_information。
- 短回复要结合上一条助手回复理解：如果它是在回应助手的确认、收束或推进询问，且没有新增事实，可以判为 closure_signal；如果它是在要求继续解释或继续讨论，就不是收束。
- 用户明确表达要结束本阶段、整理当前结论、停止追问或进入后续阶段，且没有夹带新事实时，判为 explicit_finalize_request。
- 宁可保守一点，也不要把补充信息误判成可以直接整理结论。
- 这个判断只决定用户意图，不判断内容是否真的足够；内容充分性会由下一步单独判断。

当前阶段：{stage.title}

上一条助手回复：
{last_assistant_message or "无"}

用户最新消息：
{latest_user_message}

只返回 JSON：
{{"intent":"new_information","reason":"一句话原因"}}
""".strip()

    try:
        await llm_router.load_providers(db, user_id=user.id)
        result = await llm_router.call(
            messages=[
                LLMMessage(role="system", content="你是一个判断多轮协作对话意图的助手。"),
                LLMMessage(role="user", content=prompt),
            ],
            model=model,
            provider_name=provider,
            fallback_chain=fallback_chain,
            max_tokens=120,
            temperature=0.0,
            session_id=workspace.id,
            session_type="workspace",
            agent_name="stage-finalize-intent-judge",
        )
        parsed = parse_json_object_loose(result.content or "")
        intent = str((parsed or {}).get("intent") or "").strip()
        if intent in {"new_information", "closure_signal", "explicit_finalize_request"}:
            return intent
        return "unknown"
    except Exception as exc:
        logger.warning(
            "stage finalize intent check failed conservatively: workspace=%s stage=%s reason=%s",
            workspace.id,
            stage.stage_key.value,
            exc,
        )
        return "unknown"


def build_stage_response_recommendation(
    stage: WorkspaceStage,
    *,
    summary: str,
    recommended_action: str,
    focus: List[str],
    ready_to_finalize: bool,
    readiness_blockers: List[str],
    source: str,
) -> Dict[str, Any]:
    recommendation = {
        "source": source,
        "summary": summary,
        "recommended_action": recommended_action,
        "focus": focus,
        "options": [],
        "selected_option": None,
    }
    return apply_stage_runtime_metadata(
        recommendation,
        ready_to_finalize=ready_to_finalize,
        readiness_blockers=readiness_blockers,
        readiness_message_id=None,
    )


async def generate_text_with_auto_continue(
    db: AsyncSession,
    user: User,
    workspace: Workspace,
    stage: WorkspaceStage,
    *,
    system_text: str,
    initial_prompt: str,
    agent_name: str,
    max_tokens: int,
    temperature: float,
    should_finalize: bool,
    allow_continuation: bool,
    resolve_generation_model: Callable[[AsyncSession, User], Awaitable[tuple[Optional[str], Optional[str], Optional[List[str]]]]],
    reasoning_effort: Optional[str] = None,
    seed_content: str = "",
) -> Optional[str]:
    model, provider, fallback_chain = await resolve_generation_model(db, user)
    if not model or not provider:
        return None

    await llm_router.load_providers(db, user_id=user.id)
    built_content = seed_content or ""
    base_messages = [
        LLMMessage(role="system", content=system_text),
        LLMMessage(role="user", content=initial_prompt),
    ]
    messages = list(base_messages)

    if built_content.strip():
        if await is_stage_output_complete(
            db=db,
            user=user,
            workspace=workspace,
            stage=stage,
            content=built_content,
            should_finalize=should_finalize,
            resolve_generation_model=resolve_generation_model,
        ):
            return built_content.strip()
        messages = [
            *base_messages,
            LLMMessage(role="assistant", content=built_content),
            LLMMessage(
                role="user",
                content=build_stage_output_continuation_prompt(
                    stage,
                    should_finalize=should_finalize,
                    content=built_content,
                ),
            ),
        ]

    max_rounds = MAX_STAGE_OUTPUT_CONTINUATION_ROUNDS + 1 if allow_continuation else 1
    for round_index in range(max_rounds):
        result = await llm_router.call(
            messages=messages,
            model=model,
            provider_name=provider,
            fallback_chain=fallback_chain,
            max_tokens=max_tokens,
            temperature=temperature,
            session_id=workspace.id,
            session_type="workspace",
            agent_name=agent_name,
            reasoning_effort=reasoning_effort,
        )
        piece = result.content or ""
        if not piece.strip():
            break
        if should_finalize and piece.strip() == "__COMPLETE__":
            if await is_stage_output_complete(
                db=db,
                user=user,
                workspace=workspace,
                stage=stage,
                content=built_content,
                should_finalize=True,
                resolve_generation_model=resolve_generation_model,
            ):
                return built_content.strip()
            break
        built_content = _merge_stage_output(built_content, piece)
        if await is_stage_output_complete(
            db=db,
            user=user,
            workspace=workspace,
            stage=stage,
            content=built_content,
            should_finalize=should_finalize,
            resolve_generation_model=resolve_generation_model,
        ):
            return built_content.strip()
        if round_index >= max_rounds - 1:
            break
        messages = [
            *base_messages,
            LLMMessage(role="assistant", content=built_content),
            LLMMessage(
                role="user",
                content=build_stage_output_continuation_prompt(
                    stage,
                    should_finalize=should_finalize,
                    content=built_content,
                ),
            ),
        ]

    return None


def build_stage_artifact_inventory(stages: List[WorkspaceStage], current_stage: WorkspaceStage) -> str:
    lines: List[str] = []
    for artifact in collect_stage_artifacts(stages, current_stage):
        label = str(artifact.get("label") or artifact.get("type") or "阶段附件").strip()
        stage_title = str(artifact.get("stage_title") or "").strip()
        url = str(artifact.get("url") or "").strip()
        display = f"{stage_title}：{label}" if stage_title else label
        if url:
            lines.append(f"- [{display}]({url})")
        else:
            lines.append(f"- {display}")
    return "\n".join(lines) if lines else "- 当前没有可用的上游文档附件。"


def build_generic_stage_conclusion_prompt(
    workspace: Workspace,
    stage: WorkspaceStage,
    stages: List[WorkspaceStage],
    messages: List[WorkspaceStageMessage],
    memory_context: str,
    extra_instruction: Optional[str] = None,
    stage_skill_context: str = "",
    external_reference_context: str = "",
) -> str:
    transcript = messages_to_prompt_text(messages)
    contract = stage_responsibility_contract(stage.stage_key)
    approved_context: List[str] = []
    for item in stages:
        if item.id == stage.id or item.status != WorkspaceStageStatus.APPROVED or not item.content:
            continue
        approved_context.append(f"[{item.title}]\n{item.content.strip()[:1800]}")
    approved_block = "\n\n".join(approved_context) or "暂无"
    requirement = (workspace.description or workspace.name or "").strip()
    guidance = stage_document_guidance(stage.stage_key)
    upstream_artifacts = build_stage_artifact_inventory(stages, stage) if stage.stage_key == WorkspaceStageKey.DEPLOYMENT else ""
    upstream_artifacts_block = (
        f"已确认的上游文档附件：\n{upstream_artifacts}\n"
        if upstream_artifacts
        else ""
    )
    auxiliary_context = build_auxiliary_context_block(
        stage_skill_context=stage_skill_context,
        external_reference_context=external_reference_context,
    )
    stage_specific_document_rule = ""
    if stage.stage_key == WorkspaceStageKey.TECHNICAL:
        stage_specific_document_rule = """
开发方案阶段额外要求：
- 不要只给单个技术点，要形成开发能接手的整体实现说明。
- 覆盖实现路径、边界、模块拆分、持久化或接口方式、依赖风险、实施顺序。
- 某些部分如果不需要，可以直接说明原因，不要硬补复杂设计。
- 常规技术决策直接给默认方案和理由，不要写成待确认项。
""".strip()
    prompt = f"""
你现在要把「{stage.title}」阶段的对话整理成正式结论。

要求：
1. 这是正式文档，不是聊天回复。
2. 当前文档身份是：{guidance["identity"]}。
3. 上游阶段只是输入，不是这份文档的主体。
4. 如果需要引用上游结论，只做很短的承接说明。
5. 从正文开始，只写本阶段新增确认的内容，不要重抄上游正文。
6. 这份文档必须回答：
{format_bullets(guidance["must_answer"])}
7. 这份文档禁止出现：
{format_bullets(guidance["avoid"])}
8. 不要出现下面这些会把文档拉回上游阶段的栏目：
{format_bullets(guidance.get("forbidden_sections", []))}
9. 上游承接方式要求：{guidance.get("upstream_rule", "如需承接上游，只能简短带过。")}
10. 表达风格要求：{guidance["style"]}
11. 不要输出“待确认项”清单来表示本阶段还有未完成事项。
12. 如果还有后续阶段要继续展开的内容，只能作为承接说明一笔带过。
13. 输出结构要清楚、可交接，并能支撑后续阶段继续使用。
14. 最后必须明确说明：这些结论是否足以支撑后续阶段。
15. 用 Markdown 输出，不要写成固定模板，标题数量由内容决定，但内容重心必须准确。
16. 各段之间保留清楚空行；如果不是列表，也不要把整篇正文写成一整大段。
17. 当前阶段只负责：{contract.get("chat_focus")}
18. 当前阶段不要展开：{", ".join(contract.get("chat_avoid", [])) or "无"}

项目原始需求：
{requirement}

已确认的上游阶段信息（这些只作为输入前提，不要原样重写进当前阶段正文）：
{approved_block}

当前已确认的结构化记忆：
{memory_context}

{upstream_artifacts_block}

{auxiliary_context}

{stage_specific_document_rule}

当前阶段：
{stage.title}
{stage.description or ""}

对话记录：
{transcript}
""".strip()
    if extra_instruction and extra_instruction.strip():
        prompt = f"{prompt}\n\n{extra_instruction.strip()}"
    return prompt


def build_forced_conclusion_instruction(stage: WorkspaceStage, blockers: List[str]) -> str:
    blocker_lines = format_bullets(blockers[:3]) if blockers else "- 当前没有额外阻塞点。"
    return f"""
额外要求：
用户已经明确要求直接生成「{stage.title}」阶段的当前结论，这一轮禁止继续追问。

请按现有信息直接整理一版当前阶段结论。
如果当前阶段还存在没完全说明清楚的地方，不要伪装成已经彻底完成；只需要在文档最后单列一个很短的小节，说明当前仍需补齐的点，并且只允许列下面这些真正的缺口。

当前仍需补齐的点：
{blocker_lines}
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
    reasoning_effort: Optional[str] = None,
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
        reasoning_effort=reasoning_effort,
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


def bootstrap_stage_instruction(workspace: Workspace, stage: WorkspaceStage) -> str:
    if stage.stage_key == WorkspaceStageKey.REQUIREMENTS:
        return (workspace.description or workspace.name or "").strip()
    return "请基于当前项目背景和前面已确认的内容，直接给出这一阶段的当前判断；只有存在真实阻塞点时，才补 1 个需要用户回应的问题。"


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


async def generate_requirements_chat_reply_with_llm(
    db: AsyncSession,
    user: User,
    workspace: Workspace,
    stage: WorkspaceStage,
    messages: List[WorkspaceStageMessage],
    stages: Optional[List[WorkspaceStage]] = None,
    extra_instruction: Optional[str] = None,
    runtime_options: Optional[Dict[str, Any]] = None,
    *,
    resolve_generation_model: Callable[[AsyncSession, User], Awaitable[tuple[Optional[str], Optional[str], Optional[List[str]]]]],
) -> Optional[str]:
    if stage.stage_key == WorkspaceStageKey.DEPLOYMENT:
        return render_final_delivery_summary(workspace, stages, stage)

    latest_input = requirements_latest_user_message(messages)
    memory_context = await build_workspace_memory_context(
        db,
        workspace.id,
        stage_order=stage.order,
        stages=stages,
        current_input=latest_input,
        current_stage_key=stage.stage_key.value,
    )
    stage_skill_context = build_stage_skill_context(
        stage.stage_key,
        enable_stage_skills=bool(True if runtime_options is None else runtime_options.get("enable_stage_skills", True)),
    )
    external_reference_context = await build_external_reference_context(
        db,
        user,
        workspace,
        stage,
        latest_input,
        enable_web_search=bool(runtime_options and runtime_options.get("enable_web_search")),
    )
    prompt = build_requirements_chat_prompt(
        workspace,
        messages,
        memory_context,
        extra_instruction=extra_instruction,
        stage_skill_context=stage_skill_context,
        external_reference_context=external_reference_context,
    )
    try:
        draft_reply = await generate_text_with_auto_continue(
            db=db,
            user=user,
            workspace=workspace,
            stage=stage,
            system_text="你是一个克制、可靠的助手。",
            initial_prompt=prompt,
            agent_name="requirements-conversation",
            max_tokens=stage_chat_max_tokens(WorkspaceStageKey.REQUIREMENTS),
            temperature=0.35,
            should_finalize=False,
            allow_continuation=True,
            resolve_generation_model=resolve_generation_model,
            reasoning_effort=stage_runtime_reasoning_effort(stage.stage_key, runtime_options),
        )
        if not draft_reply:
            return None
        return await rewrite_stage_chat_reply_if_needed(
            db=db,
            user=user,
            workspace=workspace,
            stage=stage,
            messages=messages,
            latest_input=latest_input,
            memory_context=memory_context,
            draft_reply=draft_reply,
            runtime_options=runtime_options,
            resolve_generation_model=resolve_generation_model,
        )
    except Exception as exc:
        logger.warning(
            "requirements conversation fell back to local heuristics: workspace=%s reason=%s",
            workspace.id,
            exc,
        )
        return None


async def generate_requirements_conclusion_with_llm(
    db: AsyncSession,
    user: User,
    workspace: Workspace,
    stage: WorkspaceStage,
    messages: List[WorkspaceStageMessage],
    stages: Optional[List[WorkspaceStage]] = None,
    extra_instruction: Optional[str] = None,
    runtime_options: Optional[Dict[str, Any]] = None,
    *,
    resolve_generation_model: Callable[[AsyncSession, User], Awaitable[tuple[Optional[str], Optional[str], Optional[List[str]]]]],
) -> Optional[str]:
    latest_input = requirements_latest_user_message(messages)
    memory_context = await build_workspace_memory_context(
        db,
        workspace.id,
        stage_order=stage.order,
        stages=stages,
        current_input=latest_input,
        current_stage_key=stage.stage_key.value,
    )
    stage_skill_context = build_stage_skill_context(
        stage.stage_key,
        enable_stage_skills=bool(True if runtime_options is None else runtime_options.get("enable_stage_skills", True)),
    )
    external_reference_context = await build_external_reference_context(
        db,
        user,
        workspace,
        stage,
        latest_input,
        enable_web_search=bool(runtime_options and runtime_options.get("enable_web_search")),
    )
    prompt = build_requirements_conclusion_prompt(
        workspace,
        messages,
        memory_context,
        extra_instruction=extra_instruction,
        stage_skill_context=stage_skill_context,
        external_reference_context=external_reference_context,
    )
    try:
        draft_reply = await generate_text_with_auto_continue(
            db=db,
            user=user,
            workspace=workspace,
            stage=stage,
            system_text="你负责把当前阶段内容整理成清楚的正式文档。",
            initial_prompt=prompt,
            agent_name="requirements-conclusion",
            max_tokens=stage_conclusion_max_tokens(WorkspaceStageKey.REQUIREMENTS),
            temperature=0.2,
            should_finalize=True,
            allow_continuation=True,
            resolve_generation_model=resolve_generation_model,
            reasoning_effort=stage_runtime_reasoning_effort(stage.stage_key, runtime_options),
        )
        return draft_reply
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
    extra_instruction: Optional[str] = None,
    runtime_options: Optional[Dict[str, Any]] = None,
    *,
    resolve_generation_model: Callable[[AsyncSession, User], Awaitable[tuple[Optional[str], Optional[str], Optional[List[str]]]]],
) -> Optional[str]:
    if stage.stage_key == WorkspaceStageKey.DEPLOYMENT:
        return await generate_generic_stage_conclusion_with_llm(
            db=db,
            user=user,
            workspace=workspace,
            stages=stages,
            stage=stage,
            messages=messages,
            extra_instruction=extra_instruction,
            runtime_options=runtime_options,
            resolve_generation_model=resolve_generation_model,
        )

    latest_input = requirements_latest_user_message(messages)
    memory_context = await build_workspace_memory_context(
        db,
        workspace.id,
        stage_order=stage.order,
        stages=stages,
        current_input=latest_input,
        current_stage_key=stage.stage_key.value,
    )
    stage_skill_context = build_stage_skill_context(
        stage.stage_key,
        enable_stage_skills=bool(True if runtime_options is None else runtime_options.get("enable_stage_skills", True)),
    )
    external_reference_context = await build_external_reference_context(
        db,
        user,
        workspace,
        stage,
        latest_input,
        enable_web_search=bool(runtime_options and runtime_options.get("enable_web_search")),
    )
    prompt = build_generic_stage_chat_prompt(
        workspace,
        stage,
        stages,
        messages,
        memory_context,
        extra_instruction=extra_instruction,
        stage_skill_context=stage_skill_context,
        external_reference_context=external_reference_context,
    )
    try:
        draft_reply = await generate_text_with_auto_continue(
            db=db,
            user=user,
            workspace=workspace,
            stage=stage,
            system_text="你是一个克制、可靠的助手。",
            initial_prompt=prompt,
            agent_name=f"{stage.stage_key.value}-conversation",
            max_tokens=stage_chat_max_tokens(stage.stage_key),
            temperature=0.35,
            should_finalize=False,
            allow_continuation=True,
            resolve_generation_model=resolve_generation_model,
            reasoning_effort=stage_runtime_reasoning_effort(stage.stage_key, runtime_options),
        )
        if not draft_reply:
            return None
        return await rewrite_stage_chat_reply_if_needed(
            db=db,
            user=user,
            workspace=workspace,
            stage=stage,
            messages=messages,
            latest_input=latest_input,
            memory_context=memory_context,
            draft_reply=draft_reply,
            runtime_options=runtime_options,
            resolve_generation_model=resolve_generation_model,
        )
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
    extra_instruction: Optional[str] = None,
    runtime_options: Optional[Dict[str, Any]] = None,
    *,
    resolve_generation_model: Callable[[AsyncSession, User], Awaitable[tuple[Optional[str], Optional[str], Optional[List[str]]]]],
) -> Optional[str]:
    latest_input = requirements_latest_user_message(messages)
    memory_context = await build_workspace_memory_context(
        db,
        workspace.id,
        stage_order=stage.order,
        stages=stages,
        current_input=latest_input,
        current_stage_key=stage.stage_key.value,
    )
    stage_skill_context = build_stage_skill_context(
        stage.stage_key,
        enable_stage_skills=bool(True if runtime_options is None else runtime_options.get("enable_stage_skills", True)),
    )
    external_reference_context = await build_external_reference_context(
        db,
        user,
        workspace,
        stage,
        latest_input,
        enable_web_search=bool(runtime_options and runtime_options.get("enable_web_search")),
    )
    prompt = build_generic_stage_conclusion_prompt(
        workspace,
        stage,
        stages,
        messages,
        memory_context,
        extra_instruction=extra_instruction,
        stage_skill_context=stage_skill_context,
        external_reference_context=external_reference_context,
    )
    try:
        return await generate_text_with_auto_continue(
            db=db,
            user=user,
            workspace=workspace,
            stage=stage,
            system_text="你负责把当前阶段内容整理成清楚的正式文档。",
            initial_prompt=prompt,
            agent_name=f"{stage.stage_key.value}-conclusion",
            max_tokens=stage_conclusion_max_tokens(stage.stage_key),
            temperature=0.2,
            should_finalize=True,
            allow_continuation=True,
            resolve_generation_model=resolve_generation_model,
            reasoning_effort=stage_runtime_reasoning_effort(stage.stage_key, runtime_options),
        )
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
    extra_instruction: Optional[str] = None,
    runtime_options: Optional[Dict[str, Any]] = None,
    *,
    resolve_generation_model: Callable[[AsyncSession, User], Awaitable[tuple[Optional[str], Optional[str], Optional[List[str]]]]],
) -> str:
    if stage.stage_key == WorkspaceStageKey.REQUIREMENTS:
        summary = await generate_requirements_conclusion_with_llm(
            db=db,
            user=user,
            workspace=workspace,
            stage=stage,
            messages=messages,
            stages=stages,
            extra_instruction=extra_instruction,
            runtime_options=runtime_options,
            resolve_generation_model=resolve_generation_model,
        )
        normalized = normalize_stage_output_paragraphs(
            (summary or (stage.content or "").strip() or "当前未生成可用结论，请恢复模型后重试。").strip(),
            should_finalize=True,
        )
        return cleanup_stage_document_markdown(stage, normalized)

    summary = await generate_generic_stage_conclusion_with_llm(
        db=db,
        user=user,
        workspace=workspace,
        stages=stages,
        stage=stage,
        messages=messages,
        extra_instruction=extra_instruction,
        runtime_options=runtime_options,
        resolve_generation_model=resolve_generation_model,
    )
    normalized = normalize_stage_output_paragraphs(
        (summary or (stage.content or "").strip() or "当前阶段已确认。").strip(),
        should_finalize=True,
    )
    return cleanup_stage_document_markdown(stage, normalized)


async def generate_requirements_stage_content(
    db: AsyncSession,
    user: User,
    workspace: Workspace,
    stages: List[WorkspaceStage],
    stage: WorkspaceStage,
    instruction: Optional[str],
    extra_instruction: Optional[str] = None,
    readiness: Optional[tuple[bool, List[str], Optional[str]]] = None,
    runtime_options: Optional[Dict[str, Any]] = None,
    *,
    resolve_generation_model: Callable[[AsyncSession, User], Awaitable[tuple[Optional[str], Optional[str], Optional[List[str]]]]],
    messages: Optional[List[WorkspaceStageMessage]] = None,
) -> tuple[Dict[str, Any], str]:
    _ = instruction
    if messages is not None:
        conversational = await generate_requirements_chat_reply_with_llm(
            db=db,
            user=user,
            workspace=workspace,
            stage=stage,
            messages=messages,
            stages=stages,
            extra_instruction=extra_instruction,
            runtime_options=runtime_options,
            resolve_generation_model=resolve_generation_model,
        )
        content = normalize_stage_output_paragraphs(
            (conversational or "当前没有拿到可用的大模型回复，请检查模型配置后重试。").strip(),
            should_finalize=False,
        )
        ready_to_finalize, readiness_blockers, readiness_message_id = readiness or (False, [], latest_user_message_id(messages or []))
        recommendation = apply_stage_runtime_metadata({
            "source": "requirements_conversation_v1",
            "summary": "当前阶段判断已稳定。"
            if ready_to_finalize
            else "这一阶段会继续根据当前输入推进。",
            "recommended_action": "可继续补充或调整当前阶段内容。"
            if ready_to_finalize
            else "继续补充当前阶段信息；系统会基于新内容更新理解。",
            "focus": ["当前阶段", "已确认内容", "当前回复"],
            "options": [
                {
                    "title": "当前对话回复",
                    "description": "基于当前上下文给出的阶段内回复。",
                    "content": content,
                    "recommended": True,
                }
            ],
            "selected_option": "当前对话回复",
        },
            ready_to_finalize=ready_to_finalize,
            readiness_blockers=readiness_blockers,
            readiness_message_id=readiness_message_id,
            blocker_state=stage_blocker_state(stage),
        )
        return recommendation, content

    content = "\n".join([
        "需求确认阶段现在只走对话式推进。",
        "",
        "这条旧生成入口不再自动产出固定分析稿。",
        "请直接进入当前阶段对话继续补充；系统会基于真实上下文回复，并在阶段成熟后自动整理结论。",
    ])
    recommendation = {
        "source": "requirements_conversation_only",
        "summary": "需求确认阶段已经切到对话式推进。",
        "recommended_action": "请直接在当前阶段对话里补充需求。",
        "focus": ["阶段对话", "真实模型回复", "自动整理结论"],
        "options": [
            {
                "title": "当前说明",
                "description": "旧生成入口已停用，请直接使用阶段对话。",
                "content": content,
                "recommended": True,
            }
        ],
        "selected_option": "当前说明",
    }
    return recommendation, content


async def generate_stage_chat_reply(
    db: AsyncSession,
    user: User,
    workspace: Workspace,
    stages: List[WorkspaceStage],
    stage: WorkspaceStage,
    instruction: str,
    extra_instruction: Optional[str] = None,
    readiness: Optional[tuple[bool, List[str], Optional[str]]] = None,
    runtime_options: Optional[Dict[str, Any]] = None,
    *,
    resolve_generation_model: Callable[[AsyncSession, User], Awaitable[tuple[Optional[str], Optional[str], Optional[List[str]]]]],
) -> tuple[Dict[str, Any], str]:
    if stage.stage_key == WorkspaceStageKey.REQUIREMENTS:
        return await generate_requirements_stage_content(
            db=db,
            user=user,
            workspace=workspace,
            stages=stages,
            stage=stage,
            instruction=instruction,
            extra_instruction=extra_instruction,
            readiness=readiness,
            runtime_options=runtime_options,
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
        extra_instruction=extra_instruction,
        runtime_options=runtime_options,
        resolve_generation_model=resolve_generation_model,
    )
    if chat_reply:
        chat_reply = normalize_stage_output_paragraphs(chat_reply.strip(), should_finalize=False)
        ready_to_finalize, readiness_blockers, readiness_message_id = readiness or (False, [], latest_user_message_id(await get_stage_messages(db, stage.id)))
        if stage.stage_key == WorkspaceStageKey.DEPLOYMENT:
            artifacts = collect_stage_artifacts(stages, stage)
            base_recommendation = {
                "source": "deployment_runtime",
                "summary": f"当前已整理 {len(artifacts)} 份已完成阶段文档，可用于生成最终交付。" if artifacts else "当前还没有可汇总的阶段文档。",
                "recommended_action": "查看最终总结，并按需下载最终交付文档。" if artifacts else "先完成前面阶段文档，再回到这里统一整理最终交付。",
                "focus": ["最终总结", "最终交付文档", "整体交接"],
                "options": [
                    {
                        "title": "当前最终交付",
                        "description": "基于当前已确认文档整理出的最终交付内容。",
                        "content": chat_reply,
                        "recommended": True,
                    }
                ],
                "selected_option": "当前最终交付",
                "artifacts": artifacts,
            }
        else:
            base_recommendation = {
                "source": "stage_conversation_v1",
                "summary": f"{stage.title}阶段当前判断已稳定。"
                if ready_to_finalize
                else f"{stage.title}阶段正在继续处理当前输入。",
                "recommended_action": "可继续补充或调整当前阶段内容。"
                if ready_to_finalize
                else "继续补充当前阶段信息；系统会基于新内容更新理解。",
                "focus": [stage.title, "阶段对话", "当前回复"],
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
        if base_recommendation.get("options"):
            option = base_recommendation["options"][0]
            if isinstance(option, dict) and not option.get("content"):
                option["content"] = chat_reply
        recommendation = apply_stage_runtime_metadata(base_recommendation,
            ready_to_finalize=ready_to_finalize,
            readiness_blockers=readiness_blockers,
            readiness_message_id=readiness_message_id,
            blocker_state=stage_blocker_state(stage),
        )
        return recommendation, chat_reply
    content = "当前没有拿到可用的大模型回复，请检查模型配置后重试。"
    recommendation = {
        "source": "model_unavailable",
        "summary": f"{stage.title}阶段当前没有拿到可用的大模型回复。",
        "recommended_action": "请先检查模型配置；恢复后再继续当前阶段。",
        "focus": ["模型可用性", "恢复真实阶段输出"],
        "options": [
            {
                "title": "当前状态",
                "description": "系统没有拿到可靠模型输出。",
                "content": content,
                "recommended": True,
            }
        ],
        "selected_option": "当前状态",
    }
    return recommendation, content


async def generate_and_append_stage_reply(
    db: AsyncSession,
    user: User,
    workspace: Workspace,
    stages: List[WorkspaceStage],
    stage: WorkspaceStage,
    messages: List[WorkspaceStageMessage],
    extra_instruction: Optional[str] = None,
    runtime_options: Optional[Dict[str, Any]] = None,
    *,
    resolve_generation_model: Callable[[AsyncSession, User], Awaitable[tuple[Optional[str], Optional[str], Optional[List[str]]]]],
    create_workspace_artifact: Callable[..., Awaitable[Artifact]],
    load_recommendation: Callable[[WorkspaceStage], Dict[str, Any]],
    upsert_artifact_reference: Callable[[Dict[str, Any], Artifact, str], Dict[str, Any]],
) -> WorkspaceStageMessage:
    if stage.stage_key == WorkspaceStageKey.DEPLOYMENT:
        latest_user_message = next((item.content.strip() for item in reversed(messages) if item.role == "user"), None)
        stage.user_feedback = latest_user_message
        return await finalize_stage_conclusion(
            db=db,
            user=user,
            workspace=workspace,
            stages=stages,
            stage=stage,
            messages=messages,
            runtime_options=runtime_options,
            resolve_generation_model=resolve_generation_model,
            create_workspace_artifact=create_workspace_artifact,
            load_recommendation=load_recommendation,
            upsert_artifact_reference=upsert_artifact_reference,
        )

    latest_user_message = next((item.content.strip() for item in reversed(messages) if item.role == "user"), None)
    readiness = (False, [])
    if has_stage_user_input(messages):
        readiness = await assess_stage_finalization_readiness(
            db=db,
            user=user,
            workspace=workspace,
            stage=stage,
            messages=messages,
            resolve_generation_model=resolve_generation_model,
        )
    finalize_intent = await assess_latest_user_message_finalize_intent(
        db=db,
        user=user,
        workspace=workspace,
        stage=stage,
        messages=messages,
        resolve_generation_model=resolve_generation_model,
    ) if has_stage_user_input(messages) else "unknown"
    if stage.stage_key == WorkspaceStageKey.TECHNICAL and should_discuss_stage_conclusion(finalize_intent):
        stage.user_feedback = latest_user_message
        return await finalize_stage_conclusion(
            db=db,
            user=user,
            workspace=workspace,
            stages=stages,
            stage=stage,
            messages=messages,
            runtime_options=runtime_options,
            resolve_generation_model=resolve_generation_model,
            create_workspace_artifact=create_workspace_artifact,
            load_recommendation=load_recommendation,
            upsert_artifact_reference=upsert_artifact_reference,
        )
    if readiness[0] and should_discuss_stage_conclusion(finalize_intent):
        stage.user_feedback = latest_user_message
        return await finalize_stage_conclusion(
            db=db,
            user=user,
            workspace=workspace,
            stages=stages,
            stage=stage,
            messages=messages,
            runtime_options=runtime_options,
            resolve_generation_model=resolve_generation_model,
            create_workspace_artifact=create_workspace_artifact,
            load_recommendation=load_recommendation,
            upsert_artifact_reference=upsert_artifact_reference,
        )
    blocker_state = build_blocker_state(stage, messages, readiness[1])
    has_assistant_reply = any(item.role == "assistant" and item.content.strip() for item in messages)
    stage_flow_instruction = (
        build_stage_readiness_instruction(stage, readiness[0], readiness[1])
        if should_discuss_stage_conclusion(finalize_intent)
        else build_new_information_chat_instruction()
        if has_stage_user_input(messages)
        else build_first_turn_chat_instruction(stage)
        if not has_assistant_reply
        else build_regular_stage_chat_instruction()
    )
    blocker_instruction = (
        build_blocker_instruction(blocker_state)
        if should_discuss_stage_conclusion(finalize_intent)
        else None
    )
    chat_ready_to_finalize = readiness[0] if should_discuss_stage_conclusion(finalize_intent) else False
    chat_readiness_blockers = readiness[1] if should_discuss_stage_conclusion(finalize_intent) else []
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
        extra_instruction=merge_stage_instructions(
            stage_flow_instruction,
            blocker_instruction,
            extra_instruction,
        ),
        readiness=(chat_ready_to_finalize, chat_readiness_blockers, latest_user_message_id(messages)),
        runtime_options=runtime_options,
        resolve_generation_model=resolve_generation_model,
    )
    stage.recommendation_json = json.dumps(recommendation, ensure_ascii=False)
    stage.content = content
    stage.user_feedback = latest_user_message
    stage.status = WorkspaceStageStatus.AWAITING_CONFIRMATION
    stage.approved_by = None
    stage.approved_at = None
    workspace.updated_at = datetime.now(timezone.utc)
    preview_message = WorkspaceStageMessage(
        stage_id=stage.id,
        role="assistant",
        kind="chat",
        content=content,
    )
    post_messages = [*messages, preview_message]
    finalize_intent = await assess_latest_user_message_finalize_intent(
        db=db,
        user=user,
        workspace=workspace,
        stage=stage,
        messages=messages,
        resolve_generation_model=resolve_generation_model,
    )
    post_can_finalize = False
    post_blockers = readiness[1]
    blocker_state = build_blocker_state(stage, post_messages, post_blockers)
    if should_discuss_stage_conclusion(finalize_intent):
        post_can_finalize, post_blockers = await assess_stage_finalization_readiness(
            db=db,
            user=user,
            workspace=workspace,
            stage=stage,
            messages=post_messages,
            resolve_generation_model=resolve_generation_model,
        )
        blocker_state = build_blocker_state(stage, post_messages, post_blockers)
        if post_can_finalize:
            stage.user_feedback = latest_user_message
            return await finalize_stage_conclusion(
                db=db,
                user=user,
                workspace=workspace,
                stages=stages,
                stage=stage,
                messages=post_messages,
                runtime_options=runtime_options,
                resolve_generation_model=resolve_generation_model,
                create_workspace_artifact=create_workspace_artifact,
                load_recommendation=load_recommendation,
                upsert_artifact_reference=upsert_artifact_reference,
            )

    assistant_message = await append_stage_message(
        db=db,
        stage=stage,
        role="assistant",
        content=content,
        kind="chat",
    )
    blocker_state = update_blockers_after_assistant_reply(blocker_state, assistant_message)
    recommendation = apply_stage_runtime_metadata(
        recommendation,
        ready_to_finalize=False,
        readiness_blockers=post_blockers,
        readiness_message_id=latest_user_message_id(messages),
        blocker_state=blocker_state,
    )
    stage.recommendation_json = json.dumps(recommendation, ensure_ascii=False)
    return assistant_message


async def bootstrap_stage_messages(
    db: AsyncSession,
    user: User,
    workspace: Workspace,
    stages: List[WorkspaceStage],
    stage: WorkspaceStage,
    *,
    resolve_generation_model: Callable[[AsyncSession, User], Awaitable[tuple[Optional[str], Optional[str], Optional[List[str]]]]],
    create_workspace_artifact: Callable[..., Awaitable[Artifact]],
    load_recommendation: Callable[[WorkspaceStage], Dict[str, Any]],
    upsert_artifact_reference: Callable[[Dict[str, Any], Artifact, str], Dict[str, Any]],
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

    if stage.stage_key == WorkspaceStageKey.DEPLOYMENT:
        assistant_message = await finalize_stage_conclusion(
            db=db,
            user=user,
            workspace=workspace,
            stages=stages,
            stage=stage,
            messages=messages,
            resolve_generation_model=resolve_generation_model,
            create_workspace_artifact=create_workspace_artifact,
            load_recommendation=load_recommendation,
            upsert_artifact_reference=upsert_artifact_reference,
        )
        return [*messages, assistant_message]

    assistant_message = await generate_and_append_stage_reply(
        db=db,
        user=user,
        workspace=workspace,
        stages=stages,
        stage=stage,
        messages=messages,
        resolve_generation_model=resolve_generation_model,
        create_workspace_artifact=create_workspace_artifact,
        load_recommendation=load_recommendation,
        upsert_artifact_reference=upsert_artifact_reference,
    )
    return [*messages, assistant_message]


async def ensure_stage_seed_messages(
    db: AsyncSession,
    workspace: Workspace,
    stage: WorkspaceStage,
    existing_messages: Optional[List[WorkspaceStageMessage]] = None,
) -> List[WorkspaceStageMessage]:
    return existing_messages if existing_messages is not None else await get_stage_messages(db, stage.id)


def render_stage_conclusion_markdown(
    workspace: Workspace,
    stage: WorkspaceStage,
    summary: str,
    stages: List[WorkspaceStage],
) -> str:
    parts = [
        f"# {workspace.name} - {stage.title}",
        "",
        summary.strip(),
    ]
    return "\n".join(parts).strip()


async def finalize_stage_conclusion(
    db: AsyncSession,
    user: User,
    workspace: Workspace,
    stages: List[WorkspaceStage],
    stage: WorkspaceStage,
    messages: List[WorkspaceStageMessage],
    extra_instruction: Optional[str] = None,
    runtime_options: Optional[Dict[str, Any]] = None,
    prepared_summary: Optional[str] = None,
    *,
    resolve_generation_model: Callable[[AsyncSession, User], Awaitable[tuple[Optional[str], Optional[str], Optional[List[str]]]]],
    create_workspace_artifact: Callable[..., Awaitable[Artifact]],
    load_recommendation: Callable[[WorkspaceStage], Dict[str, Any]],
    upsert_artifact_reference: Callable[[Dict[str, Any], Artifact, str], Dict[str, Any]],
) -> WorkspaceStageMessage:
    summary = (prepared_summary or "").strip()
    if not summary:
        summary = await generate_stage_conclusion_summary(
            db=db,
            user=user,
            workspace=workspace,
            stages=stages,
            stage=stage,
            messages=messages,
            extra_instruction=extra_instruction,
            runtime_options=runtime_options,
            resolve_generation_model=resolve_generation_model,
        )
    md_content = render_stage_conclusion_markdown(workspace, stage, summary, stages)
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
    recommendation = apply_stage_runtime_metadata(
        recommendation,
        ready_to_finalize=True,
        readiness_blockers=[],
        readiness_message_id=latest_user_message_id(messages),
        blocker_state=[],
    )
    stage.content = summary
    recommendation = with_stage_snapshot(recommendation, stage)
    stage.recommendation_json = json.dumps(recommendation, ensure_ascii=False)
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
    force_finalize: bool = False,
    runtime_options: Optional[Dict[str, Any]] = None,
    extra_instruction: Optional[str] = None,
):
    async def event_generator():
        built_content = ""
        emitted_content = ""
        built_reasoning = ""
        model: Optional[str] = None
        provider: Optional[str] = None
        try:
            recommendation: Optional[Dict[str, Any]] = None
            readiness_message_id = latest_user_message_id(messages)
            blocker_state: List[Dict[str, Any]] = []
            should_finalize = force_finalize or stage.stage_key == WorkspaceStageKey.DEPLOYMENT
            readiness_can_finalize = False
            readiness_blockers: List[str] = []
            forced_conclusion_instruction: Optional[str] = None
            finalize_intent = "unknown"
            if has_stage_user_input(messages):
                finalize_intent = await assess_latest_user_message_finalize_intent(
                    db=db,
                    user=user,
                    workspace=workspace,
                    stage=stage,
                    messages=messages,
                    resolve_generation_model=resolve_generation_model,
                )
            if stage.stage_key == WorkspaceStageKey.DEPLOYMENT:
                readiness_can_finalize = True
            elif force_finalize and stage_is_ready_to_finalize(stage, messages):
                should_finalize = True
                readiness_can_finalize = True
            elif has_stage_user_input(messages):
                readiness_can_finalize, readiness_blockers = await assess_stage_finalization_readiness(
                    db=db,
                    user=user,
                    workspace=workspace,
                    stage=stage,
                    messages=messages,
                    resolve_generation_model=resolve_generation_model,
                )
            if not should_finalize and readiness_can_finalize:
                should_finalize = should_discuss_stage_conclusion(finalize_intent)
            if (
                not should_finalize
                and stage.stage_key == WorkspaceStageKey.TECHNICAL
                and should_discuss_stage_conclusion(finalize_intent)
            ):
                should_finalize = True
                readiness_can_finalize = True
            blocker_state = build_blocker_state(stage, messages, readiness_blockers)
            if should_finalize and not readiness_can_finalize:
                forced_conclusion_instruction = build_forced_conclusion_instruction(stage, readiness_blockers)
            display_ready_to_finalize = readiness_can_finalize if should_finalize or should_discuss_stage_conclusion(finalize_intent) else False
            has_assistant_reply = any(item.role == "assistant" and item.content.strip() for item in messages)

            if should_finalize:
                latest_input = requirements_latest_user_message(messages)
                memory_context = await build_workspace_memory_context(
                    db,
                    workspace.id,
                    stage_order=stage.order,
                    stages=stages,
                    current_input=latest_input,
                    current_stage_key=stage.stage_key.value,
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
                    latest_input,
                    enable_web_search=bool(runtime_options and runtime_options.get("enable_web_search")),
                )
                prompt = (
                    build_requirements_conclusion_prompt(
                        workspace,
                        messages,
                        memory_context,
                        extra_instruction=merge_stage_instructions(forced_conclusion_instruction, extra_instruction),
                        stage_skill_context=stage_skill_context,
                        external_reference_context=external_reference_context,
                    )
                    if stage.stage_key == WorkspaceStageKey.REQUIREMENTS
                    else build_generic_stage_conclusion_prompt(
                        workspace,
                        stage,
                        stages,
                        messages,
                        memory_context,
                        extra_instruction=merge_stage_instructions(forced_conclusion_instruction, extra_instruction),
                        stage_skill_context=stage_skill_context,
                        external_reference_context=external_reference_context,
                    )
                )
                system_text = (
                    "你负责把当前阶段内容整理成清楚的正式文档。"
                    if stage.stage_key == WorkspaceStageKey.REQUIREMENTS
                    else "你负责把当前阶段内容整理成清楚的正式文档。"
                )
                agent_name = f"{stage.stage_key.value}-conclusion"
            else:
                stage_flow_instruction = (
                    build_stage_readiness_instruction(stage, readiness_can_finalize, readiness_blockers)
                    if should_discuss_stage_conclusion(finalize_intent) or force_finalize
                    else build_new_information_chat_instruction()
                    if has_stage_user_input(messages)
                    else build_first_turn_chat_instruction(stage)
                    if not has_assistant_reply
                    else build_regular_stage_chat_instruction()
                )
                blocker_instruction = (
                    build_blocker_instruction(blocker_state)
                    if should_discuss_stage_conclusion(finalize_intent) or force_finalize
                    else None
                )
                not_ready_instruction = (
                    build_finalize_not_ready_instruction(stage, readiness_blockers)
                    if force_finalize and readiness_blockers
                    else None
                )
                latest_input = requirements_latest_user_message(messages)
                memory_context = await build_workspace_memory_context(
                    db,
                    workspace.id,
                    stage_order=stage.order,
                    stages=stages,
                    current_input=latest_input,
                    current_stage_key=stage.stage_key.value,
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
                    latest_input,
                    enable_web_search=bool(runtime_options and runtime_options.get("enable_web_search")),
                )
                prompt = (
                    build_requirements_chat_prompt(
                        workspace,
                        messages,
                        memory_context,
                        extra_instruction=merge_stage_instructions(
                            stage_flow_instruction,
                            blocker_instruction,
                            not_ready_instruction,
                            extra_instruction,
                        ),
                        stage_skill_context=stage_skill_context,
                        external_reference_context=external_reference_context,
                    )
                    if stage.stage_key == WorkspaceStageKey.REQUIREMENTS
                    else build_generic_stage_chat_prompt(
                        workspace,
                        stage,
                        stages,
                        messages,
                        memory_context,
                        extra_instruction=merge_stage_instructions(
                            stage_flow_instruction,
                            blocker_instruction,
                            not_ready_instruction,
                            extra_instruction,
                        ),
                        stage_skill_context=stage_skill_context,
                        external_reference_context=external_reference_context,
                    )
                )
                system_text = (
                    "你是一个克制、可靠的助手。"
                    if stage.stage_key == WorkspaceStageKey.REQUIREMENTS
                    else "你是一个克制、可靠的助手。"
                )
                agent_name = (
                    "requirements-conversation"
                    if stage.stage_key == WorkspaceStageKey.REQUIREMENTS
                    else f"{stage.stage_key.value}-conversation"
                )
                recommendation = apply_stage_runtime_metadata({
                    "source": "stream_conversation",
                    "summary": f"{stage.title}阶段当前判断已稳定。"
                    if display_ready_to_finalize
                    else f"{stage.title}阶段正在继续处理当前输入。"
                    if not force_finalize
                    else f"{stage.title}阶段还有关键缺口，已继续停留在本阶段。",
                    "recommended_action": "可继续补充或调整当前阶段内容。"
                    if display_ready_to_finalize
                    else "继续在当前阶段补充；系统会按新内容更新判断。"
                    if not force_finalize
                    else "先补齐当前阻塞点。",
                    "focus": [stage.title, "真实模型回复", "阶段推进"] if not force_finalize else [stage.title, "阶段结束判断", "关键缺口"],
                    "options": [],
                    "selected_option": None,
                },
                    ready_to_finalize=display_ready_to_finalize,
                    readiness_blockers=readiness_blockers if display_ready_to_finalize or force_finalize else [],
                    readiness_message_id=readiness_message_id,
                    blocker_state=blocker_state,
                )

            if stage.stage_key == WorkspaceStageKey.DEPLOYMENT and should_finalize:
                built_content = render_final_delivery_summary(workspace, stages, stage).strip()
                delta, emitted_content = _streamable_delta(built_content, emitted_content, force_flush=True)
                for piece in re.findall(r".{1,48}", delta, flags=re.S):
                    yield {"event": "content", "data": piece}
                    await asyncio.sleep(0.01)
            elif stage.stage_key != WorkspaceStageKey.DEPLOYMENT or should_finalize:
                base_messages = [
                    LLMMessage(role="system", content=system_text),
                    LLMMessage(role="user", content=prompt),
                ]
                round_messages = base_messages
                max_rounds = MAX_STAGE_OUTPUT_CONTINUATION_ROUNDS + 1 if should_finalize else 1
                stream_max_tokens = stage_conclusion_max_tokens(stage.stage_key) if should_finalize else stage_chat_max_tokens(stage.stage_key)
                stream_temperature = 0.2 if should_finalize else 0.35
                for round_index in range(max_rounds):
                    stream, model, provider = await stream_llm_text(
                        db=db,
                        user=user,
                        messages=round_messages,
                        session_id=workspace.id,
                        agent_name=agent_name,
                        resolve_generation_model=resolve_generation_model,
                        max_tokens=stream_max_tokens,
                        temperature=stream_temperature,
                        reasoning_effort=stage_runtime_reasoning_effort(stage.stage_key, runtime_options),
                    )
                    if stream is None:
                        yield {"event": "error", "data": "没有可用的大模型配置，无法生成真实回复。"}
                        return

                    stream_interrupted = False
                    stream_saw_done = False
                    stream_finish_reason = ""
                    try:
                        async for item in stream:
                            item_type = str(item.get("type") or "")
                            if item_type == "meta":
                                stream_saw_done = str(item.get("saw_done") or "") == "1"
                                stream_finish_reason = str(item.get("finish_reason") or "").strip()
                                continue
                            piece = str(item.get("content") or "")
                            if not piece:
                                continue
                            if item_type == "reasoning":
                                built_reasoning += piece
                                yield {"event": "reasoning", "data": piece}
                                continue
                            built_content = _merge_stage_output(built_content, piece)
                            delta, emitted_content = _streamable_delta(built_content, emitted_content)
                            if delta:
                                yield {"event": "content", "data": delta}
                    except Exception as stream_exc:
                        stream_interrupted = True
                        logger.warning(
                            "stage stream interrupted, switching to completion fallback: workspace=%s stage=%s reason=%s",
                            workspace.id,
                            stage.stage_key.value,
                            stream_exc,
                        )

                    current_content = built_content.strip()
                    if not current_content:
                        logger.warning(
                            "stage stream produced no content, falling back to non-stream completion: workspace=%s stage=%s reasoning_chars=%s",
                            workspace.id,
                            stage.stage_key.value,
                            len(built_reasoning),
                        )
                        fallback_text = await generate_text_with_auto_continue(
                            db=db,
                            user=user,
                            workspace=workspace,
                            stage=stage,
                            system_text=system_text,
                            initial_prompt=prompt,
                            agent_name=agent_name,
                            max_tokens=stream_max_tokens,
                            temperature=stream_temperature,
                            should_finalize=should_finalize,
                            allow_continuation=False if stage.stage_key == WorkspaceStageKey.DEPLOYMENT and should_finalize else True,
                            resolve_generation_model=resolve_generation_model,
                            reasoning_effort=stage_runtime_reasoning_effort(stage.stage_key, runtime_options),
                        )
                        if not fallback_text:
                            if built_reasoning.strip():
                                yield {
                                    "event": "error",
                                    "data": "模型这次只返回了思考内容，没有返回可展示正文。建议重试，或换一个更稳定的模型。",
                                }
                            else:
                                yield {"event": "error", "data": "模型没有返回正文内容，请检查模型是否支持当前流式接口。"}
                            return
                        built_content = fallback_text.strip()
                        delta, emitted_content = _streamable_delta(built_content, emitted_content, force_flush=True)
                        for piece in re.findall(r".{1,48}", delta, flags=re.S):
                            yield {"event": "content", "data": piece}
                            await asyncio.sleep(0.01)
                        break

                    is_complete = False
                    if not stream_interrupted and stream_saw_done and stream_finish_reason != "length":
                        is_complete = await is_stage_output_complete(
                            db=db,
                            user=user,
                            workspace=workspace,
                            stage=stage,
                            content=built_content,
                            should_finalize=should_finalize,
                            resolve_generation_model=resolve_generation_model,
                        )

                    if is_complete:
                        break

                    if stage.stage_key == WorkspaceStageKey.DEPLOYMENT and should_finalize:
                        fallback_text = await generate_text_with_auto_continue(
                            db=db,
                            user=user,
                            workspace=workspace,
                            stage=stage,
                            system_text=system_text,
                            initial_prompt=prompt,
                            agent_name=agent_name,
                            max_tokens=stream_max_tokens,
                            temperature=stream_temperature,
                            should_finalize=True,
                            allow_continuation=False,
                            resolve_generation_model=resolve_generation_model,
                            reasoning_effort=stage_runtime_reasoning_effort(stage.stage_key, runtime_options),
                        )
                        if not fallback_text:
                            fallback_text = render_final_delivery_fallback(stages, stage)
                    else:
                        fallback_text = await generate_text_with_auto_continue(
                            db=db,
                            user=user,
                            workspace=workspace,
                            stage=stage,
                            system_text=system_text,
                            initial_prompt=prompt,
                            agent_name=agent_name,
                            max_tokens=stream_max_tokens,
                            temperature=stream_temperature,
                            should_finalize=should_finalize,
                            allow_continuation=True,
                            resolve_generation_model=resolve_generation_model,
                            reasoning_effort=stage_runtime_reasoning_effort(stage.stage_key, runtime_options),
                            seed_content=built_content,
                        )

                    if not fallback_text:
                        if built_content.strip():
                            is_complete_without_fallback = await is_stage_output_complete(
                                db=db,
                                user=user,
                                workspace=workspace,
                                stage=stage,
                                content=built_content,
                                should_finalize=should_finalize,
                                resolve_generation_model=resolve_generation_model,
                            )
                            if is_complete_without_fallback:
                                break
                        if round_index >= max_rounds - 1:
                            yield {"event": "error", "data": "当前内容未完整生成，请重试。"}
                        else:
                            yield {"event": "error", "data": "当前内容补全失败，请重试。"}
                        return

                    built_content = fallback_text.strip()
                    delta, emitted_content = _streamable_delta(built_content, emitted_content, force_flush=True)
                    for piece in re.findall(r".{1,48}", delta, flags=re.S):
                        yield {"event": "content", "data": piece}
                        await asyncio.sleep(0.01)
                    break

                delta, emitted_content = _streamable_delta(built_content, emitted_content, force_flush=True)
                for piece in re.findall(r".{1,48}", delta, flags=re.S):
                    yield {"event": "content", "data": piece}
                    await asyncio.sleep(0.01)

            if should_finalize:
                summary = cleanup_stage_document_markdown(
                    stage,
                    normalize_stage_output_paragraphs(built_content.strip(), should_finalize=True),
                )
                md_content = render_stage_conclusion_markdown(workspace, stage, summary, stages)
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
                conclusion_recommendation = apply_stage_runtime_metadata(
                    conclusion_recommendation,
                    ready_to_finalize=True,
                    readiness_blockers=[],
                    readiness_message_id=latest_user_message_id(messages),
                    blocker_state=[],
                )
                stage.content = summary
                conclusion_recommendation = with_stage_snapshot(conclusion_recommendation, stage)
                stage.recommendation_json = json.dumps(conclusion_recommendation, ensure_ascii=False)
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
                final_content = normalize_stage_output_paragraphs(built_content.strip(), should_finalize=False)
                if stage.stage_key != WorkspaceStageKey.DEPLOYMENT:
                    final_content = await rewrite_stage_chat_reply_if_needed(
                        db=db,
                        user=user,
                        workspace=workspace,
                        stage=stage,
                        messages=messages,
                        latest_input=requirements_latest_user_message(messages),
                        memory_context=memory_context,
                        draft_reply=final_content,
                        runtime_options=runtime_options,
                        resolve_generation_model=resolve_generation_model,
                    )
                if recommendation is None:
                    recommendation = {}
                stage.recommendation_json = json.dumps(recommendation or {}, ensure_ascii=False)
                stage.content = final_content
                stage.user_feedback = messages[-1].content if messages else None
                stage.status = WorkspaceStageStatus.AWAITING_CONFIRMATION
                stage.approved_by = None
                stage.approved_at = None
                workspace.updated_at = datetime.now(timezone.utc)
                preview_message = WorkspaceStageMessage(
                    stage_id=stage.id,
                    role="assistant",
                    kind="chat",
                    content=final_content,
                )
                post_messages = [*messages, preview_message]
                finalize_intent = await assess_latest_user_message_finalize_intent(
                    db=db,
                    user=user,
                    workspace=workspace,
                    stage=stage,
                    messages=messages,
                    resolve_generation_model=resolve_generation_model,
                )
                if should_discuss_stage_conclusion(finalize_intent):
                    post_can_finalize, post_blockers = await assess_stage_finalization_readiness(
                        db=db,
                        user=user,
                        workspace=workspace,
                        stage=stage,
                        messages=post_messages,
                        resolve_generation_model=resolve_generation_model,
                    )
                    blocker_state = build_blocker_state(stage, post_messages, post_blockers)
                    if post_can_finalize:
                        stage.user_feedback = messages[-1].content if messages else None
                        assistant_message = await finalize_stage_conclusion(
                            db=db,
                            user=user,
                            workspace=workspace,
                            stages=stages,
                            stage=stage,
                            messages=post_messages,
                            runtime_options=runtime_options,
                            resolve_generation_model=resolve_generation_model,
                            create_workspace_artifact=create_workspace_artifact,
                            load_recommendation=load_recommendation,
                            upsert_artifact_reference=upsert_artifact_reference,
                        )
                    else:
                        assistant_message = await append_stage_message(
                            db=db,
                            stage=stage,
                            role="assistant",
                            content=final_content,
                            kind="chat",
                        )
                        blocker_state = update_blockers_after_assistant_reply(blocker_state, assistant_message)
                        recommendation = apply_stage_runtime_metadata(
                            recommendation,
                            ready_to_finalize=False,
                            readiness_blockers=post_blockers,
                            readiness_message_id=latest_user_message_id(messages),
                            blocker_state=blocker_state,
                        )
                        stage.recommendation_json = json.dumps(recommendation or {}, ensure_ascii=False)
                else:
                    assistant_message = await append_stage_message(
                        db=db,
                        stage=stage,
                        role="assistant",
                        content=final_content,
                        kind="chat",
                    )
                    blocker_state = build_blocker_state(stage, post_messages, readiness_blockers)
                    blocker_state = update_blockers_after_assistant_reply(blocker_state, assistant_message)
                    recommendation = apply_stage_runtime_metadata(
                        recommendation,
                        ready_to_finalize=False,
                        readiness_blockers=readiness_blockers,
                        readiness_message_id=latest_user_message_id(messages),
                        blocker_state=blocker_state,
                    )
                    stage.recommendation_json = json.dumps(recommendation or {}, ensure_ascii=False)

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
    runtime_options: Optional[Dict[str, Any]] = None,
    *,
    resolve_generation_model: Callable[[AsyncSession, User], Awaitable[tuple[Optional[str], Optional[str], Optional[List[str]]]]],
    generate_requirements_stage_content: Callable[..., Awaitable[tuple[Dict[str, Any], str]]],
    serialize_stage_response: Callable[[WorkspaceStage], Dict[str, Any]],
):
    model, provider, _fallback_chain = await resolve_generation_model(db, user)
    if not model or not provider:
        content = "\n".join([
            "需求确认阶段当前没有拿到可用的大模型回复。",
            "",
            "这个入口不会再自动产出旧式固定分析稿。",
            "请先检查模型配置；恢复后再回到当前阶段对话继续。",
        ])
        recommendation = {
            "source": "model_unavailable",
            "summary": "需求确认阶段当前没有拿到可用的大模型回复。",
            "recommended_action": "请先检查模型配置；恢复后再回到阶段对话继续。",
            "focus": ["模型可用性", "阶段对话"],
            "options": [
                {
                    "title": "当前状态",
                    "description": "系统没有拿到可靠模型输出。",
                    "content": content,
                    "recommended": True,
                }
            ],
            "selected_option": "当前状态",
        }

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
                runtime_options=runtime_options,
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
                runtime_options=runtime_options,
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
