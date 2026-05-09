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
from app.services.flows.stage_generation import build_external_reference_context, build_stage_skill_context

logger = logging.getLogger(__name__)
MAX_STAGE_OUTPUT_CONTINUATION_ROUNDS = 2


def stage_runtime_metadata(stage: WorkspaceStage) -> Dict[str, Any]:
    if not stage.recommendation_json:
        return {}
    try:
        recommendation = json.loads(stage.recommendation_json)
    except json.JSONDecodeError:
        return {}
    if not isinstance(recommendation, dict):
        return {}
    metadata = recommendation.get("stage_runtime")
    return metadata if isinstance(metadata, dict) else {}


def apply_stage_runtime_metadata(
    recommendation: Dict[str, Any],
    *,
    ready_to_finalize: bool,
    readiness_blockers: List[str],
    readiness_message_id: Optional[str],
) -> Dict[str, Any]:
    recommendation["stage_runtime"] = {
        "ready_to_finalize": ready_to_finalize,
        "readiness_blockers": readiness_blockers[:3],
        "readiness_message_id": readiness_message_id,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
    }
    return recommendation


def stage_is_ready_to_finalize(stage: WorkspaceStage, messages: List[WorkspaceStageMessage]) -> bool:
    metadata = stage_runtime_metadata(stage)
    if not metadata.get("ready_to_finalize"):
        return False
    readiness_message_id = str(metadata.get("readiness_message_id") or "").strip()
    return bool(readiness_message_id) and readiness_message_id == latest_user_message_id(messages)


def latest_user_message_id(messages: List[WorkspaceStageMessage]) -> Optional[str]:
    for item in reversed(messages):
        if item.role == "user" and item.content.strip():
            return item.id
    return None


def is_acknowledgement_message(content: str) -> bool:
    normalized = re.sub(r"\s+", "", content).lower()
    return normalized in {
        "嗯",
        "嗯嗯",
        "好",
        "好的",
        "可以",
        "ok",
        "okay",
        "继续",
        "开始",
        "收到",
        "对",
        "是的",
        "没问题",
    }


def extract_user_memory(messages: List[WorkspaceStageMessage], limit: int = 8) -> List[str]:
    memory: List[str] = []
    seen: set[str] = set()
    for item in messages:
        if item.role != "user":
            continue
        content = item.content.strip()
        if not content or is_acknowledgement_message(content):
            continue
        normalized = re.sub(r"\s+", "", content)
        if normalized in seen:
            continue
        seen.add(normalized)
        memory.append(content)
    return memory[-limit:]


def stage_completion_requirements(stage_key: WorkspaceStageKey) -> List[str]:
    requirement_map: Dict[WorkspaceStageKey, List[str]] = {
        WorkspaceStageKey.REQUIREMENTS: [
            "产品定义已经清楚，而不只是产品名字或一句很泛的描述",
            "主要使用者和关键处理角色已经对齐",
            "主流程已经清楚，至少知道谁发起、谁处理、结果怎么成立",
            "会影响后续方案骨架的关键约束已经明确，例如确认方式、分配方式、范围限制或权限边界",
        ],
        WorkspaceStageKey.PRODUCT: [
            "当前阶段已经形成方案层新增确认，而不只是把需求确认阶段换种说法重写一遍",
            "主要功能模块已经成体系，不只是零散功能点",
            "模块之间的关系已经说清楚",
            "页面结构已经能承接模块设计",
            "主要流程已经闭环，不会一进入细节确认就发现骨架缺失",
        ],
        WorkspaceStageKey.UI_DIRECTION: [
            "当前阶段已经形成规则层新增确认，而不只是重复方案骨架",
            "角色权限已经清楚",
            "关键状态流转已经清楚",
            "异常处理和边界规则已经覆盖主要场景",
            "不会一进入开发方案就因为规则口径不清被打回来",
        ],
        WorkspaceStageKey.TECHNICAL: [
            "当前阶段已经形成开发可接手的实现方案，而不只是重复业务方案",
            "技术落地方式已经明确",
            "模块拆分和接口/数据组织已经清楚",
            "依赖、风险和实施顺序已经说明",
            "不会在交接给开发时还停留在泛泛建议",
        ],
        WorkspaceStageKey.DEPLOYMENT: [
            "当前阶段主体已经是交付清单和文档索引，而不是再写一篇新的阶段总结",
            "文档集合已经齐全",
            "每份文档的作用已经说明",
            "单独下载和整体打包方式已经说明",
        ],
    }
    return requirement_map.get(stage_key, ["当前阶段的核心信息已经收完整。"])


def stage_chat_max_tokens(stage_key: WorkspaceStageKey) -> int:
    if stage_key == WorkspaceStageKey.PRODUCT:
        return 520
    if stage_key in {WorkspaceStageKey.UI_DIRECTION, WorkspaceStageKey.TECHNICAL}:
        return 460
    return 320


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


def build_auxiliary_context_block(
    *,
    stage_skill_context: str = "",
    external_reference_context: str = "",
) -> str:
    sections: List[str] = []
    if stage_skill_context.strip():
        sections.append(
            "\n".join(
                [
                    "当前阶段辅助能力（只作为你的工作方式约束，不要原样复述给用户）：",
                    stage_skill_context.strip(),
                ]
            )
        )
    if external_reference_context.strip():
        sections.append(
            "\n".join(
                [
                    "外部成熟案例参考（仅作参考，不要生搬硬套，也不要逐条引用给用户）：",
                    external_reference_context.strip(),
                ]
            )
        )
    return "\n\n".join(sections).strip()


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


def looks_like_truncated_output(content: str) -> bool:
    stripped = content.rstrip()
    if not stripped:
        return True
    if "�" in stripped:
        return True
    if stripped.endswith(("：", ":", "（", "(", "、", "，", ",", "-", "*", "1.", "2.", "3.")):
        return True
    if re.search(r"(?:^|\n)\s*\d+\.\s*$", stripped):
        return True
    if re.search(r"(?:^|\n)\s*[-*]\s*$", stripped):
        return True
    if re.search(r"[A-Za-z0-9\u4e00-\u9fff]\s*�$", stripped):
        return True
    return False


def looks_like_incomplete_stage_chat_reply(stage: WorkspaceStage, content: str) -> bool:
    stripped = content.strip()
    if not stripped:
        return True

    has_question = "？" in stripped or "?" in stripped
    has_closure_signal = any(
        phrase in stripped
        for phrase in (
            "可以进入下一阶段",
            "可以进入下一步",
            "可以继续下一阶段",
            "可以先进入下一阶段",
            "可以往下走",
            "可以收口",
            "可以结束这一阶段",
            "可以结束当前阶段",
            "当前信息已经够了",
            "当前信息已经足够",
            "这一阶段已经可以结束",
            "这一阶段可以结束",
        )
    )
    if has_question or has_closure_signal:
        return False

    sentence_count = len(re.findall(r"[。！？!?]", stripped))
    normalized = re.sub(r"\s+", "", stripped)

    if stage.stage_key == WorkspaceStageKey.REQUIREMENTS:
        if sentence_count <= 1:
            return True
        if any(
            signal in normalized
            for signal in (
                "明白这是一个",
                "我理解这是一个",
                "我现在理解",
                "核心是",
                "产品定位是",
            )
        ):
            return True

    return sentence_count == 0


def _split_cn_sentences(content: str) -> List[str]:
    text = re.sub(r"[ \t]+", " ", (content or "").strip())
    if not text:
        return []
    parts = re.findall(r'[^。！？!?]+[。！？!?]?|.+$', text)
    return [part.strip() for part in parts if part and part.strip()]


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

    normalized = "\n\n".join(part for part in paragraphs if part)
    return normalized or text


def build_stage_output_completion_prompt(
    stage: WorkspaceStage,
    *,
    should_finalize: bool,
    content: str,
) -> str:
    guidance = stage_document_guidance(stage.stage_key)
    return f"""
当前阶段：{stage.title}
输出类型：{"阶段结论文档" if should_finalize else "阶段对话回复"}

请判断下面这段输出是否已经完整。

判断原则：
1. 如果内容明显像被截断、半句话、半个问题、编号没写完、列点没写完，返回 false。
2. 如果内容语义上已经完整收住，返回 true。
3. 如果是阶段结论文档，除是否截断外，还要判断它是否已经基本覆盖当前阶段该有的内容重心。
4. 如果只是还能写得更长，但当前已经完整，不要因为“还能再补充”就返回 false。

当前阶段重心：
- 文档身份：{guidance["identity"]}
- 需要回答：{", ".join(guidance["must_answer"])}

当前输出：
{content[-5000:]}

只返回 JSON：
{{"is_complete": true, "reason": "一句话原因"}}
""".strip()


def build_stage_output_continuation_prompt(
    stage: WorkspaceStage,
    *,
    should_finalize: bool,
    content: str,
) -> str:
    return f"""
你刚才那段{"文档" if should_finalize else "回复"}还没写完整。

请继续刚才的内容，只补尚未说完的部分。

要求：
1. 不要重复前文，不要从头再写。
2. 直接顺着刚才中断的地方继续。
3. 保持同样的语气和结构。
4. 如果前文已经完整，就不要另起一段泛泛补充。

已生成内容：
{content[-4000:]}
""".strip()


def build_stage_finalization_readiness_prompt(
    workspace: Workspace,
    stage: WorkspaceStage,
    messages: List[WorkspaceStageMessage],
) -> str:
    transcript = messages_to_prompt_text(messages)
    requirement = (workspace.description or workspace.name or "").strip()
    guidance = stage_document_guidance(stage.stage_key)
    completion_requirements = stage_completion_requirements(stage.stage_key)
    checklist_instruction = ""
    if stage.stage_key == WorkspaceStageKey.REQUIREMENTS:
        checklist_instruction = """
对需求确认阶段，再额外按下面 4 项逐条判断：
1. product_definition_clear：我们已经知道这到底是什么产品，而不只是一个模糊方向。
2. user_and_role_clear：主要使用者，以及关键处理角色/协作角色，已经清楚。
3. core_flow_clear：主流程已经清楚，至少知道谁发起、谁处理、结果怎么成立。
4. structural_constraints_clear：会影响后续方案骨架的关键约束已经清楚，例如确认方式、分配方式、范围限制、权限边界这类内容。

只有这 4 项都为 true，can_finalize 才能为 true。
""".strip()
    return f"""
当前阶段：{stage.title}
项目原始需求：{requirement}

请判断：基于当前阶段对话，是否已经可以结束这一阶段，并整理出一份合格的阶段结论文档。

判断标准：
1. 只有在当前信息已经足够支撑本阶段正式收口，并且不会一进入下一阶段就发现明显缺口时，才返回 true。
2. 用户说“可以结束”不等于真的可以结束，你要按内容是否足够来判断。
3. 如果只是还能补充更多细节，但不影响下一阶段推进，可以返回 true。
4. 如果还缺少会影响下一阶段结构或当前阶段文档质量的关键点，返回 false。
5. 如果当前内容大部分只是复述上游阶段，而本阶段新增确认还没有形成，必须返回 false。
6. blockers 只保留 1 到 3 个，必须是真正阻塞当前阶段收口的问题。

当前阶段文档要回答：
- 文档身份：{guidance["identity"]}
- 需要回答：{", ".join(guidance["must_answer"])}

当前阶段至少要满足：
{_format_bullets(completion_requirements)}

{checklist_instruction}

当前阶段对话：
{transcript}

只返回 JSON：
{{"can_finalize": true, "reason": "一句话原因", "blockers": [], "checklist": {{"product_definition_clear": true, "user_and_role_clear": true, "core_flow_clear": true, "structural_constraints_clear": true}}}}
""".strip()


def build_finalize_not_ready_instruction(stage: WorkspaceStage, blockers: List[str]) -> str:
    blocker_lines = _format_bullets(blockers[:3]) if blockers else "- 当前还有关键缺口，不能直接收口。"
    return f"""
额外要求：
用户刚刚是在尝试结束「{stage.title}」阶段，但按当前信息，这一阶段还不能收口。

你这次不要整理结论文档，也不要说可以进入下一阶段。
请直接告诉用户：为什么现在还不能结束，以及还差哪 1-3 个真正阻塞当前阶段收口的关键点。

重点缺口：
{blocker_lines}
""".strip()


def build_stage_readiness_instruction(stage: WorkspaceStage, can_finalize: bool, blockers: List[str]) -> str:
    if can_finalize:
        return f"""
当前判断：
「{stage.title}」阶段按现有信息已经可以收口。

要求：
1. 如果用户没有新的补充，你可以明确告诉用户：这一阶段可以结束了。
2. 不要因为谨慎而继续追问，也不要重新打开已经确认过的问题。
3. 这一轮只需要短回复收口，不要顺手展开下一阶段内容。
""".strip()

    blocker_lines = _format_bullets(blockers[:3]) if blockers else "- 当前还有关键缺口。"
    return f"""
当前判断：
「{stage.title}」阶段按现有信息还不能收口。

要求：
1. 这一轮不要说“可以进入下一阶段”。
2. 只围绕下面这些阻塞点推进，不要换一套新问题，也不要把整份方案提前讲完。
3. 如果用户已经明确说过某个点，不要重复追问。

当前阻塞点：
{blocker_lines}
""".strip()


def merge_stage_instructions(*parts: Optional[str]) -> Optional[str]:
    normalized = [part.strip() for part in parts if part and part.strip()]
    if not normalized:
        return None
    return "\n\n".join(normalized)


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
    if not should_finalize and looks_like_incomplete_stage_chat_reply(stage, content):
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
            return not looks_like_truncated_output(content) and not looks_like_incomplete_stage_chat_reply(stage, content)
        return bool(parsed.get("is_complete")) and not looks_like_incomplete_stage_chat_reply(stage, content)
    except Exception as exc:
        logger.warning(
            "stage output completeness check fell back to heuristics: workspace=%s stage=%s reason=%s",
            workspace.id,
            stage.stage_key.value,
            exc,
        )
        return not looks_like_truncated_output(content) and not looks_like_incomplete_stage_chat_reply(stage, content)


async def assess_stage_finalization_readiness(
    db: AsyncSession,
    user: User,
    workspace: Workspace,
    stage: WorkspaceStage,
    messages: List[WorkspaceStageMessage],
    *,
    resolve_generation_model: Callable[[AsyncSession, User], Awaitable[tuple[Optional[str], Optional[str], Optional[List[str]]]]],
) -> tuple[bool, List[str]]:
    if len(requirements_user_messages(messages)) < 2:
        return False, ["当前阶段对话还太少，信息还不足以稳定收口。"]

    model, provider, fallback_chain = await resolve_generation_model(db, user)
    if not model or not provider:
        return False, ["当前无法可靠判断阶段是否已经收完整，暂不建议直接生成结论。"]

    prompt = build_stage_finalization_readiness_prompt(workspace, stage, messages)
    try:
        await llm_router.load_providers(db, user_id=user.id)
        result = await llm_router.call(
            messages=[
                LLMMessage(role="system", content="你是一个负责判断阶段是否可以正式收口的助手。"),
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
            return False, ["当前还不能稳定判断阶段是否已收完整，建议先补充关键缺口。"]
        blockers = parsed.get("blockers")
        normalized_blockers = [
            str(item).strip()
            for item in (blockers if isinstance(blockers, list) else [])
            if str(item).strip()
        ][:3]
        can_finalize = bool(parsed.get("can_finalize"))
        if stage.stage_key == WorkspaceStageKey.REQUIREMENTS:
            checklist = parsed.get("checklist")
            checklist_values = checklist if isinstance(checklist, dict) else {}
            required_keys = (
                "product_definition_clear",
                "user_and_role_clear",
                "core_flow_clear",
                "structural_constraints_clear",
            )
            missing_dimensions = [key for key in required_keys if checklist_values.get(key) is not True]
            if missing_dimensions:
                can_finalize = False
                if not normalized_blockers:
                    blocker_map = {
                        "product_definition_clear": "产品定义还不够实，当前更像方向描述，还不能稳定进入下一阶段。",
                        "user_and_role_clear": "主要使用者和关键处理角色还不够清楚。",
                        "core_flow_clear": "主流程还不够清楚，至少要明确谁发起、谁处理、结果怎么成立。",
                        "structural_constraints_clear": "还缺少会影响方案骨架的关键约束，例如确认方式、分配方式、范围限制或权限边界。",
                    }
                    normalized_blockers = [blocker_map[key] for key in missing_dimensions[:3]]
        return can_finalize, normalized_blockers
    except Exception as exc:
        logger.warning(
            "stage finalization readiness check failed conservatively: workspace=%s stage=%s reason=%s",
            workspace.id,
            stage.stage_key.value,
            exc,
        )
        return False, ["当前还不能稳定判断阶段是否已收完整，建议先补充关键缺口。"]


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
) -> Optional[str]:
    model, provider, fallback_chain = await resolve_generation_model(db, user)
    if not model or not provider:
        return None

    await llm_router.load_providers(db, user_id=user.id)
    built_content = ""
    messages = [
        LLMMessage(role="system", content=system_text),
        LLMMessage(role="user", content=initial_prompt),
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
        built_content += piece
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
            LLMMessage(role="system", content=system_text),
            LLMMessage(role="user", content=initial_prompt),
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


def build_requirements_chat_prompt(
    workspace: Workspace,
    messages: List[WorkspaceStageMessage],
    memory_context: str,
    extra_instruction: Optional[str] = None,
    stage_skill_context: str = "",
    external_reference_context: str = "",
) -> str:
    transcript = messages_to_prompt_text(messages)
    latest = requirements_latest_user_message(messages)
    user_memory = extract_user_memory(messages)
    requirement = (workspace.description or workspace.name or "").strip()
    auxiliary_context = build_auxiliary_context_block(
        stage_skill_context=stage_skill_context,
        external_reference_context=external_reference_context,
    )
    prompt = f"""
你现在扮演的是一个会推进项目的高级产品经理同事，不是在写方案稿或需求分析报告。

你要做的是：基于当前对话，直接回复用户下一句该说的话。

对话要求：
1. 这是对话，不是文档，不要重复整段已有内容。
2. 当前阶段只做“需求确认”，目标是先对齐：这到底是什么产品、谁在用、主流程怎么成立、有哪些会改变后续方案骨架的关键约束。
3. 这一阶段不是固定问卷。不要机械列清单，但也不要太快说“够了”。
4. 这一轮回复必须完成下面三件事里的一个：
   - 纠正你的当前理解；
   - 提一个真正阻塞后续方案的关键问题；
   - 明确说明当前信息已经够了，并点出为什么够了。
5. 能高置信推断的就先直接说出理解，不要把显而易见的内容反问回去。
6. 如果当前信息还不够，结尾必须落到一个具体问题上，不能只停在“我理解这是个什么产品”。
7. 关键问题只碰结构性前提，例如谁发起、谁处理、结果怎么成立、时间或资源怎么确定、是否需要审核、范围或权限怎么限制。
8. 不要提前展开实现细节，不要顺手把下一阶段的方案讲出来。
9. 如果当前已经够进入下一阶段，直接明确说可以收口；如果还不够，就不要提前宣布可以结束。
10. 用自然中文，像正常同事在微信里推进事情，控制在 3 到 6 行内。
11. 回复必须分成 2 到 4 个短段落，段落之间空一行；不要把全部内容挤成一大段。
12. 如果最后要问问题，让问题单独成一小段。
13. 每次最多推进 1 个关键问题。

原始需求：
{requirement}

本轮用户最新补充：
{latest}

用户已经明确说过的信息（这些不要重复追问）：
{_format_bullets(user_memory)}

当前已确认的结构化记忆：
{memory_context}

{auxiliary_context}

到目前为止的对话：
{transcript}
""".strip()
    if extra_instruction and extra_instruction.strip():
        prompt = f"{prompt}\n\n{extra_instruction.strip()}"
    return prompt


def build_generic_stage_chat_prompt(
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
    latest = requirements_latest_user_message(messages)
    user_memory = extract_user_memory(messages)
    approved_context: List[str] = []
    for item in stages:
        if item.id == stage.id or item.status != WorkspaceStageStatus.APPROVED or not item.content:
            continue
        approved_context.append(f"[{item.title}]\n{item.content.strip()[:1800]}")
    approved_block = "\n\n".join(approved_context) or "暂无"
    requirement = (workspace.description or workspace.name or "").strip()
    stage_focus_map = {
        WorkspaceStageKey.PRODUCT: "这一阶段的目标是把产品方案骨架收清楚：模块怎么分、结构怎么组织、主流程怎么成立。不要回头重写需求背景。",
        WorkspaceStageKey.UI_DIRECTION: "这一阶段的目标是把规则和边界收清楚：权限、状态、异常、口径这些会影响真实业务落地的内容。",
        WorkspaceStageKey.TECHNICAL: "这一阶段的目标是把开发可接手的实现方案收清楚：怎么落、怎么拆、有什么依赖和风险。",
        WorkspaceStageKey.DEPLOYMENT: "这一阶段的目标是把交付物和交接方式收清楚，不要再写一篇新的总结。",
    }
    stage_focus = stage_focus_map.get(stage.stage_key, "重点收敛这一阶段真正新增确认的内容。")
    stage_completion_map = {
        WorkspaceStageKey.PRODUCT: "只有在当前方案已经足够支撑后续细节确认，而不会因为模块、页面或主流程结构太空再打回来，才可以提示结束。",
        WorkspaceStageKey.UI_DIRECTION: "只有在当前规则已经足够支撑开发方案，不会因为权限、状态或边界缺口再打回来，才可以提示结束。",
        WorkspaceStageKey.TECHNICAL: "只有在当前方案已经足够支持开发交接，不会因为实现路径、拆分方式或依赖风险不清再打回来，才可以提示结束。",
        WorkspaceStageKey.DEPLOYMENT: "只有在文档集合和交接方式已经足够完整，不会因为缺附件或缺说明再打回来，才可以提示结束。",
    }
    stage_completion = stage_completion_map.get(stage.stage_key, "只有在当前阶段核心信息已经收完整后，才可以提示用户结束这一阶段。")
    stage_dialogue_rule_map = {
        WorkspaceStageKey.PRODUCT: "优先先给出一版可讨论的模块/结构/流程骨架，再根据用户反馈微调。不要把常见方案骨架先反过来问给用户。",
        WorkspaceStageKey.UI_DIRECTION: "优先补关键业务规则、状态和边界。像常规的通过/拒绝、结果通知、基础状态记录这类成熟规则，先给出第一版，不要全部问回用户。",
        WorkspaceStageKey.TECHNICAL: "你是在替开发整理可接手方案，不是在做技术访谈。像接口组织、数据结构、通知接法、服务建模、后台拆分这类通用工程设计，先给成熟默认方案和理由，不要让非技术用户来决定。只有缺口真的会改写整体实现路径时，才追问 1 个阻塞问题。",
        WorkspaceStageKey.DEPLOYMENT: "这一阶段不要继续追问，直接整理交付物列表、用途说明和下载方式。",
    }
    stage_dialogue_rule = stage_dialogue_rule_map.get(stage.stage_key, "围绕当前阶段新增确认推进，不要把成熟默认方案都重新问给用户。")
    auxiliary_context = build_auxiliary_context_block(
        stage_skill_context=stage_skill_context,
        external_reference_context=external_reference_context,
    )
    prompt = f"""
你现在处于一个多阶段产品流程里，当前阶段是「{stage.title}」。

你的任务不是直接产出最终文档，而是像同事一样继续和用户对话，把这一阶段逐步收敛清楚。

回复要求：
1. 只回应这次用户新补充带来的变化，不要把前文大段重写一遍。
2. 这是对话，不是报告，不要使用固定小标题和模板化段落。
3. 如果需要承接上游，只能用 1 到 2 句带过“当前是在什么前提上继续往下”；不要把上游正文重讲一遍。
4. 先说你基于这次补充更新了什么，再说这一阶段接下来怎么收。
5. 当前阶段正文只能推进本阶段新增确认，不要把上游已确认内容重新改写成当前阶段成果。
6. 不要把这一阶段做成固定问卷。你要根据当前产品类型、上游已确认内容和这一阶段的目标，自己判断还差哪 1-3 个真正阻塞的问题。
7. 如果这一阶段信息已经够了，就直接告诉用户：如果没有别的补充，可以结束这一阶段，你会整理结论。
8. 不要脱离当前阶段职责。不要提前跳去实现代码，也不要机械补全页面模板。
9. 用自然中文，像微信里协作推进，控制在 3 到 7 行内。
10. 不要把“很粗的提纲”误判成“这一阶段已经完成”，也不要把“复述上游”误判成“当前阶段已经完成”。
11. 固定的是阶段目标，不是具体问题清单。
12. 这是逐轮推进，不是一次性把当前阶段整份方案全部讲完。
13. 每次最多推进 1 个关键问题；如果已经够了，就只说这一阶段可以收口，不要继续把后面内容也展开。
14. 用户已经明确说过的信息，不要重复追问。
15. 回复必须分成 2 到 4 个短段落，段落之间空一行；不要把全部内容挤成一大段。
16. 如果最后要问问题，让问题单独成一小段。
17. 能先给出一版默认方案时，就先给方案；不要把成熟通用做法一上来全变成反问。
18. 如果问题只是实现层常规选项，不足以改变当前阶段主结论，就不要把它当成阻塞点。

项目原始需求：
{requirement}

当前阶段说明：
{stage.description or stage.title}

本阶段收敛重点：
{stage_focus}

本阶段结束前至少要满足：
{stage_completion}

当前阶段额外对话原则：
{stage_dialogue_rule}

已确认的上游阶段信息（这些只作为输入前提，不要原样重写进当前阶段正文）：
{approved_block}

用户本轮最新补充：
{latest}

用户已经明确说过的信息（这些不要重复追问）：
{_format_bullets(user_memory)}

当前已确认的结构化记忆：
{memory_context}

{auxiliary_context}

当前阶段到目前为止的对话：
{transcript}
""".strip()
    if extra_instruction and extra_instruction.strip():
        prompt = f"{prompt}\n\n{extra_instruction.strip()}"
    return prompt


def build_requirements_conclusion_prompt(
    workspace: Workspace,
    messages: List[WorkspaceStageMessage],
    memory_context: str,
    stage_skill_context: str = "",
    external_reference_context: str = "",
) -> str:
    transcript = messages_to_prompt_text(messages)
    requirement = (workspace.description or workspace.name or "").strip()
    auxiliary_context = build_auxiliary_context_block(
        stage_skill_context=stage_skill_context,
        external_reference_context=external_reference_context,
    )
    return f"""
你现在要把一段需求确认对话整理成正式文档。

要求：
1. 这不是对话回复，而是需求文档。
2. 这份文档的任务，是把“我们确认这是什么产品”整理清楚，不是开始写方案设计。
3. 文档重点不是功能罗列，而是产品理解对齐。至少要说清楚：
   - 这是一个什么产品
   - 主要给谁用
   - 主要解决什么事
   - 当前已经明确的结构性前提
   - 当前为什么已经可以确认这一阶段完成
4. 如果对话里没有明确的信息，不要硬编；可以直接写“当前未明确”。
5. 可以补充项目目标，但不要提前展开模块方案、页面结构、权限模型、技术方案。
6. 不要复述“我收到你的补充了”这类过程话术。
7. 不要把整段对话原文再抄一遍。
8. 能整理这份阶段文档，前提就是当前阶段已经闭环，所以不要输出“待确认项”或“关键待确认项”板块。
9. 第一阶段可以写“结构性前提”，但不要展开“实现性细节”。
10. 不要把后续阶段才该讨论的问题提前写进来，例如：
   - 具体功能细项
   - 完整业务规则细则
   - 节假日/通知/异常处理细则
   - 盈利模式
   - 技术实现细节
11. 如果产品方向已经对齐，就直接明确写“当前阶段已完成产品理解确认，可进入下一阶段”。
12. 用清楚、可确认、可交接的表达整理结果，让后续阶段能直接接着往下做。
13. 结尾要明确说明是否已经可以进入下一阶段。
14. 只有在不会一进入下一阶段就发现主流程结构还悬空时，才允许得出“可进入下一阶段”的结论。
15. 正文要有清楚段落，段落之间空一行；不要写成没有换行的一整大段。

原始需求：
{requirement}

当前已确认的结构化记忆：
{memory_context}

{auxiliary_context}

对话记录：
{transcript}
""".strip()


def stage_document_guidance(stage_key: WorkspaceStageKey) -> Dict[str, Any]:
    guidance_map: Dict[WorkspaceStageKey, Dict[str, Any]] = {
        WorkspaceStageKey.PRODUCT: {
            "identity": "结构化方案稿",
            "must_answer": [
                "主要功能模块有哪些",
                "模块之间如何协作",
                "页面结构如何组织",
                "主要流程如何流转",
            ],
            "avoid": [
                "不要重写需求背景和项目目标",
                "不要重新展开上游已经确认的约束",
                "不要深入权限、状态流转、异常规则等细节",
            ],
            "upstream_rule": "可以用 1 个很短的“承接前提”小节带过上游输入，控制在 1 到 3 行；正文从功能模块开始，只写本阶段新增方案。",
            "forbidden_sections": ["产品定义", "目标用户", "核心价值", "项目目标", "结构性前提"],
            "style": "优先采用结构化表达，以模块、关系、结构、流程为主。",
        },
        WorkspaceStageKey.UI_DIRECTION: {
            "identity": "规则文档",
            "must_answer": [
                "角色和权限怎么定义",
                "状态如何流转",
                "异常情况如何处理",
                "数据口径如何统一",
                "关键边界条件是什么",
            ],
            "avoid": [
                "不要重新讲模块结构或整体方案",
                "不要写技术架构或实现方案",
                "不要把它写成页面设计说明",
            ],
            "upstream_rule": "可以先用 1 到 3 行交代这份规则文档是基于哪版方案继续细化；从正文开始，只写规则、边界、状态和口径。",
            "forbidden_sections": ["产品定义", "目标用户", "功能模块", "页面结构", "主要流程"],
            "style": "优先用规则式表达，突出状态、条件、边界和口径。",
        },
        WorkspaceStageKey.TECHNICAL: {
            "identity": "技术方案文档",
            "must_answer": [
                "技术落地方式是什么",
                "模块怎么拆",
                "接口怎么组织",
                "数据结构怎么定义",
                "依赖和风险是什么",
                "建议的开发顺序是什么",
            ],
            "avoid": [
                "不要重复产品背景和整体需求说明",
                "不要只停留在技术栈口号",
                "不要伪装成代码已经完成",
            ],
            "upstream_rule": "可以先用 1 到 3 行说明本方案承接的是哪版产品方案和规则前提；正文只写实现与交接层内容。",
            "forbidden_sections": ["产品定义", "目标用户", "核心价值", "功能模块详解", "规则总览"],
            "style": "更工程化、更偏实施层，要像开发可接手的方案。",
        },
        WorkspaceStageKey.DEPLOYMENT: {
            "identity": "交付索引文档",
            "must_answer": [
                "一共有哪些文档",
                "每份文档属于哪个阶段",
                "每份文档解决什么问题",
                "如何单独下载",
                "如何整体打包下载",
            ],
            "avoid": [
                "不要再大段复述前 4 个阶段的正文",
                "不要重新生成一篇项目总结文",
                "不要在这个阶段新增核心方案内容",
            ],
            "upstream_rule": "这里只需要一小段最终说明；主体必须是文档集合、附件列表和下载/交接说明。",
            "forbidden_sections": ["产品定义", "目标用户", "功能模块", "页面结构", "技术方案正文"],
            "style": "以列表化、索引化、附件说明为主，只保留一段很短的最终总结。",
        },
    }
    return guidance_map.get(
        stage_key,
        {
            "identity": "阶段文档",
            "must_answer": ["回答当前阶段最核心的问题"],
            "avoid": ["不要重复上游文档内容"],
            "upstream_rule": "如需承接上游，只能简短带过。",
            "forbidden_sections": [],
            "style": "只保留本阶段新增确认。",
        },
    )


def _format_bullets(items: List[str]) -> str:
    return "\n".join(f"- {item}" for item in items) if items else "- 无"


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


def collect_stage_artifacts(stages: List[WorkspaceStage], current_stage: WorkspaceStage) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for item in stages:
        if item.id == current_stage.id or not item.recommendation_json:
            continue
        try:
            recommendation = json.loads(item.recommendation_json)
        except json.JSONDecodeError:
            continue
        artifacts = recommendation.get("artifacts")
        if not isinstance(artifacts, list):
            continue
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                continue
            artifact_id = str(artifact.get("artifact_id") or "").strip()
            url = str(artifact.get("url") or "").strip()
            if not artifact_id and not url:
                continue
            dedupe_key = artifact_id or url
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            entry = dict(artifact)
            entry["stage_key"] = item.stage_key.value
            entry["stage_title"] = item.title
            if not entry.get("label"):
                entry["label"] = f"{item.title}文档"
            entries.append(entry)
    return entries


def build_deployment_stage_fallback_content(
    workspace: Workspace,
    stages: List[WorkspaceStage],
    current_stage: WorkspaceStage,
) -> str:
    artifacts = collect_stage_artifacts(stages, current_stage)
    if not artifacts:
        return "\n".join([
            "当前还没有可整理的交付文档。",
            "",
            "等前面阶段至少生成一份可交付文档后，这里再统一汇总成清单。",
        ]).strip()

    lines: List[str] = []
    for index, artifact in enumerate(artifacts, start=1):
        label = str(artifact.get("label") or artifact.get("type") or "阶段附件").strip()
        stage_title = str(artifact.get("stage_title") or "").strip()
        desc = f"{stage_title}阶段文档" if stage_title else "阶段文档"
        lines.append(f"{index}. {label}：{desc}")

    return "\n".join([
        "当前流程的主要交付物已经整理好了。",
        "",
        "下面这些文档可以直接单独下载；如果需要统一交接，可以再打包下载全部文档。",
        "",
        *lines,
    ]).strip()


def build_deployment_stage_fallback_summary(
    workspace: Workspace,
    stages: List[WorkspaceStage],
    current_stage: WorkspaceStage,
) -> str:
    artifacts = collect_stage_artifacts(stages, current_stage)
    if not artifacts:
        return "当前还没有足够的阶段文档可汇总成完整交付清单。"

    stage_titles: List[str] = []
    seen_titles: set[str] = set()
    for artifact in artifacts:
        title = str(artifact.get("stage_title") or "").strip()
        if title and title not in seen_titles:
            seen_titles.add(title)
            stage_titles.append(title)

    titles_text = "、".join(stage_titles) if stage_titles else "各阶段"
    return "\n".join([
        "当前流程的主要交付物已经整理完成。",
        "",
        f"本次已汇总 {titles_text} 等阶段文档，当前阶段以文档清单、单独下载和整体打包交接为主。",
    ]).strip()


def build_deployment_stage_recommendation(
    stage: WorkspaceStage,
    stages: List[WorkspaceStage],
) -> Dict[str, Any]:
    return {
        "source": "deployment_fallback",
        "summary": "交付清单阶段已按现有文档自动整理完成。",
        "recommended_action": "直接查看交付物列表；可以单独下载，也可以整体打包下载。",
        "focus": ["交付物列表", "单独下载", "整体打包"],
        "options": [
            {
                "title": "当前交付清单",
                "description": "基于已确认阶段文档自动整理的交付物列表。",
                "content": "",
                "recommended": True,
            }
        ],
        "selected_option": "当前交付清单",
        "artifacts": collect_stage_artifacts(stages, stage),
    }


def build_generic_stage_conclusion_prompt(
    workspace: Workspace,
    stage: WorkspaceStage,
    stages: List[WorkspaceStage],
    messages: List[WorkspaceStageMessage],
    memory_context: str,
    stage_skill_context: str = "",
    external_reference_context: str = "",
) -> str:
    transcript = messages_to_prompt_text(messages)
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
    return f"""
你现在要把「{stage.title}」阶段的对话整理成正式结论。

要求：
1. 这是正式文档，不是聊天回复。
2. 当前文档身份是：{guidance["identity"]}。
3. 上游阶段只是这份文档的输入，不是这份文档的主体。
4. 如果需要引用上游结论，只能做一个很短的“承接前提/输入前提”说明，控制在 1 个小节、1 到 3 行。
5. 从正文开始，只写本阶段新增确认的内容，不要重抄上游已经确认的正文。
6. 这份文档必须回答：
{_format_bullets(guidance["must_answer"])}
7. 这份文档禁止出现：
{_format_bullets(guidance["avoid"])}
8. 不要出现下面这些会把文档拉回上游阶段的栏目：
{_format_bullets(guidance.get("forbidden_sections", []))}
9. 上游承接方式要求：{guidance.get("upstream_rule", "如需承接上游，只能简短带过。")}
10. 表达风格要求：{guidance["style"]}
11. 能整理这份阶段结论，前提就是当前阶段已经闭环，所以不要输出“待确认项”清单来表示本阶段还有没收完的事。
12. 如果还有后续阶段要继续展开的内容，只能作为下一阶段的承接说明一笔带过，不能写成当前阶段未完成。
13. 输出结构要清楚、可交接、可继续进入下一阶段。
14. 最后必须明确说明：当前是否可以进入下一阶段。
15. 用 Markdown 输出，不要写成固定模板，不强制固定标题数量，但内容重心必须准确。
16. 各段之间保留清楚空行；如果不是列表，也不要把整篇正文写成一整大段。

项目原始需求：
{requirement}

已确认的上游阶段信息（这些只作为输入前提，不要原样重写进当前阶段正文）：
{approved_block}

当前已确认的结构化记忆：
{memory_context}

{upstream_artifacts_block}

{auxiliary_context}

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
    stage: WorkspaceStage,
    messages: List[WorkspaceStageMessage],
    stages: Optional[List[WorkspaceStage]] = None,
    extra_instruction: Optional[str] = None,
    runtime_options: Optional[Dict[str, Any]] = None,
    *,
    resolve_generation_model: Callable[[AsyncSession, User], Awaitable[tuple[Optional[str], Optional[str], Optional[List[str]]]]],
) -> Optional[str]:
    memory_context = await build_workspace_memory_context(
        db,
        workspace.id,
        stage_order=stage.order,
        stages=stages,
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
        requirements_latest_user_message(messages),
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
        return await generate_text_with_auto_continue(
            db=db,
            user=user,
            workspace=workspace,
            stage=stage,
            system_text="你是一个擅长需求确认和项目推进的产品负责人。",
            initial_prompt=prompt,
            agent_name="requirements-conversation",
            max_tokens=stage_chat_max_tokens(WorkspaceStageKey.REQUIREMENTS),
            temperature=0.35,
            should_finalize=False,
            allow_continuation=False,
            resolve_generation_model=resolve_generation_model,
            reasoning_effort=runtime_reasoning_effort(runtime_options),
        )
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
        "## 这是什么产品",
        f"- {requirement or '当前需求尚未被完整确认。'}",
        "",
        "## 主要给谁用",
        "- 当前无法可靠自动提炼，请结合对话人工确认。",
        "",
        "## 主要解决什么事",
        "- 当前无法可靠自动提炼，请基于对话人工确认。",
        "",
        "## 当前边界和前提",
        *captured,
        "",
        "## 阶段结论",
        "- 当前不建议自动进入下一阶段，需要先恢复模型能力或人工复核。",
    ]).strip()


async def generate_requirements_conclusion_with_llm(
    db: AsyncSession,
    user: User,
    workspace: Workspace,
    stage: WorkspaceStage,
    messages: List[WorkspaceStageMessage],
    stages: Optional[List[WorkspaceStage]] = None,
    runtime_options: Optional[Dict[str, Any]] = None,
    *,
    resolve_generation_model: Callable[[AsyncSession, User], Awaitable[tuple[Optional[str], Optional[str], Optional[List[str]]]]],
) -> Optional[str]:
    memory_context = await build_workspace_memory_context(
        db,
        workspace.id,
        stage_order=stage.order,
        stages=stages,
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
        requirements_latest_user_message(messages),
        enable_web_search=bool(runtime_options and runtime_options.get("enable_web_search")),
    )
    prompt = build_requirements_conclusion_prompt(
        workspace,
        messages,
        memory_context,
        stage_skill_context=stage_skill_context,
        external_reference_context=external_reference_context,
    )
    try:
        return await generate_text_with_auto_continue(
            db=db,
            user=user,
            workspace=workspace,
            stage=stage,
            system_text="你是一个擅长整理项目需求结论的产品负责人。",
            initial_prompt=prompt,
            agent_name="requirements-conclusion",
            max_tokens=stage_conclusion_max_tokens(WorkspaceStageKey.REQUIREMENTS),
            temperature=0.2,
            should_finalize=True,
            allow_continuation=True,
            resolve_generation_model=resolve_generation_model,
            reasoning_effort=runtime_reasoning_effort(runtime_options),
        )
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
        return build_deployment_stage_fallback_content(workspace, stages, stage)

    memory_context = await build_workspace_memory_context(
        db,
        workspace.id,
        stage_order=stage.order,
        stages=stages,
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
        requirements_latest_user_message(messages),
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
        return await generate_text_with_auto_continue(
            db=db,
            user=user,
            workspace=workspace,
            stage=stage,
            system_text=f"你是负责推进「{stage.title}」阶段的产品协作助手。",
            initial_prompt=prompt,
            agent_name=f"{stage.stage_key.value}-conversation",
            max_tokens=stage_chat_max_tokens(stage.stage_key),
            temperature=0.35,
            should_finalize=False,
            allow_continuation=False,
            resolve_generation_model=resolve_generation_model,
            reasoning_effort=runtime_reasoning_effort(runtime_options),
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
    runtime_options: Optional[Dict[str, Any]] = None,
    *,
    resolve_generation_model: Callable[[AsyncSession, User], Awaitable[tuple[Optional[str], Optional[str], Optional[List[str]]]]],
) -> Optional[str]:
    if stage.stage_key == WorkspaceStageKey.DEPLOYMENT:
        return build_deployment_stage_fallback_summary(workspace, stages, stage)

    memory_context = await build_workspace_memory_context(
        db,
        workspace.id,
        stage_order=stage.order,
        stages=stages,
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
        requirements_latest_user_message(messages),
        enable_web_search=bool(runtime_options and runtime_options.get("enable_web_search")),
    )
    prompt = build_generic_stage_conclusion_prompt(
        workspace,
        stage,
        stages,
        messages,
        memory_context,
        stage_skill_context=stage_skill_context,
        external_reference_context=external_reference_context,
    )
    try:
        return await generate_text_with_auto_continue(
            db=db,
            user=user,
            workspace=workspace,
            stage=stage,
            system_text=f"你是负责整理「{stage.title}」阶段结论的产品协作助手。",
            initial_prompt=prompt,
            agent_name=f"{stage.stage_key.value}-conclusion",
            max_tokens=stage_conclusion_max_tokens(stage.stage_key),
            temperature=0.2,
            should_finalize=True,
            allow_continuation=True,
            resolve_generation_model=resolve_generation_model,
            reasoning_effort=runtime_reasoning_effort(runtime_options),
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
            runtime_options=runtime_options,
            resolve_generation_model=resolve_generation_model,
        )
        return normalize_stage_output_paragraphs(
            (summary or fallback_requirements_conclusion(workspace, messages)).strip(),
            should_finalize=True,
        )

    summary = await generate_generic_stage_conclusion_with_llm(
        db=db,
        user=user,
        workspace=workspace,
        stages=stages,
        stage=stage,
        messages=messages,
        runtime_options=runtime_options,
        resolve_generation_model=resolve_generation_model,
    )
    return normalize_stage_output_paragraphs(
        (summary or (stage.content or "").strip() or "当前阶段已确认。").strip(),
        should_finalize=True,
    )


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
            (conversational or build_requirements_chat_fallback(workspace, messages)).strip(),
            should_finalize=False,
        )
        ready_to_finalize, readiness_blockers, readiness_message_id = readiness or (False, [], latest_user_message_id(messages or []))
        recommendation = apply_stage_runtime_metadata({
            "source": "requirements_conversation_v1",
            "summary": "当前阶段已经可以收口，等你确认后整理阶段结论。"
            if ready_to_finalize
            else "这一阶段按对话方式持续推进，直到关键信息补齐。",
            "recommended_action": "如果没有新的补充，就可以直接进入阶段结论。"
            if ready_to_finalize
            else "继续补充；如果这一阶段已经完整，就明确表达可以收尾。",
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
        },
            ready_to_finalize=ready_to_finalize,
            readiness_blockers=readiness_blockers,
            readiness_message_id=readiness_message_id,
        )
        return recommendation, content

    return build_model_unavailable_stage_output(
        stage,
        summary="需求确认阶段已经改成对话推进，不再生成固定分析稿。",
        recommended_action="请直接在当前阶段对话里补充需求，不再使用旧的生成入口。",
        focus=["阶段对话", "真实模型回复", "去模板化"],
        content="\n".join([
            "需求确认阶段现在只走对话式推进。",
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
    extra_instruction: Optional[str] = None,
    readiness: Optional[tuple[bool, List[str], Optional[str]]] = None,
    runtime_options: Optional[Dict[str, Any]] = None,
    *,
    resolve_generation_model: Callable[[AsyncSession, User], Awaitable[tuple[Optional[str], Optional[str], Optional[List[str]]]]],
    generate_stage_artifact_with_llm: Callable[..., Awaitable[Optional[tuple[Dict[str, Any], str]]]],
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
        base_recommendation = (
            build_deployment_stage_recommendation(stage, stages)
            if stage.stage_key == WorkspaceStageKey.DEPLOYMENT
            else {
                "source": "stage_conversation_v1",
                "summary": f"{stage.title}阶段已经可以收口，等你确认后整理阶段结论。"
                if ready_to_finalize
                else f"{stage.title}阶段正在通过真实对话逐步收敛。",
                "recommended_action": "如果没有新的补充，就可以直接进入阶段结论。"
                if ready_to_finalize
                else "继续补充；如果这一阶段已经完整，就明确表达可以收尾。",
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
        )
        if base_recommendation.get("options"):
            option = base_recommendation["options"][0]
            if isinstance(option, dict) and not option.get("content"):
                option["content"] = chat_reply
        recommendation = apply_stage_runtime_metadata(base_recommendation,
            ready_to_finalize=ready_to_finalize,
            readiness_blockers=readiness_blockers,
            readiness_message_id=readiness_message_id,
        )
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
    extra_instruction: Optional[str] = None,
    runtime_options: Optional[Dict[str, Any]] = None,
    *,
    resolve_generation_model: Callable[[AsyncSession, User], Awaitable[tuple[Optional[str], Optional[str], Optional[List[str]]]]],
    generate_stage_artifact_with_llm: Callable[..., Awaitable[Optional[tuple[Dict[str, Any], str]]]],
) -> WorkspaceStageMessage:
    latest_user_message = next((item.content.strip() for item in reversed(messages) if item.role == "user"), None)
    readiness = await assess_stage_finalization_readiness(
        db=db,
        user=user,
        workspace=workspace,
        stage=stage,
        messages=messages,
        resolve_generation_model=resolve_generation_model,
    )
    readiness_instruction = build_stage_readiness_instruction(stage, readiness[0], readiness[1])
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
        extra_instruction=merge_stage_instructions(readiness_instruction, extra_instruction),
        readiness=(readiness[0], readiness[1], latest_user_message_id(messages)),
        runtime_options=runtime_options,
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
    if stage.stage_key == WorkspaceStageKey.DEPLOYMENT:
        inventory = build_stage_artifact_inventory(stages, stage)
        parts.extend([
            "",
            "## 附件列表",
            "",
            inventory,
            "",
            "## 下载说明",
            "",
            "- 可在交付清单中单独打开或下载任一阶段文档。",
            "- 如需整体交接，应提供全部文档的打包下载入口。",
        ])
    return "\n".join(parts).strip()


async def finalize_stage_conclusion(
    db: AsyncSession,
    user: User,
    workspace: Workspace,
    stages: List[WorkspaceStage],
    stage: WorkspaceStage,
    messages: List[WorkspaceStageMessage],
    runtime_options: Optional[Dict[str, Any]] = None,
    *,
    resolve_generation_model: Callable[[AsyncSession, User], Awaitable[tuple[Optional[str], Optional[str], Optional[List[str]]]]],
    create_workspace_artifact: Callable[..., Awaitable[Artifact]],
    load_recommendation: Callable[[WorkspaceStage], Dict[str, Any]],
    upsert_artifact_reference: Callable[[Dict[str, Any], Artifact, str], Dict[str, Any]],
) -> WorkspaceStageMessage:
    summary = await generate_stage_conclusion_summary(
        db=db,
        user=user,
        workspace=workspace,
        stages=stages,
        stage=stage,
        messages=messages,
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
    runtime_options: Optional[Dict[str, Any]] = None,
):
    async def event_generator():
        built_content = ""
        built_reasoning = ""
        model: Optional[str] = None
        provider: Optional[str] = None
        try:
            recommendation: Optional[Dict[str, Any]] = None
            readiness_message_id = latest_user_message_id(messages)
            finalize_requested = force_finalize or await should_finalize_stage_message(
                db=db,
                user=user,
                workspace=workspace,
                stage=stage,
                messages=messages,
                resolve_generation_model=resolve_generation_model,
                parse_json_object=parse_json_object,
            )
            should_finalize = finalize_requested
            readiness_can_finalize = False
            readiness_blockers: List[str] = []
            if force_finalize and stage_is_ready_to_finalize(stage, messages):
                should_finalize = True
                readiness_can_finalize = True
            else:
                readiness_can_finalize, readiness_blockers = await assess_stage_finalization_readiness(
                    db=db,
                    user=user,
                    workspace=workspace,
                    stage=stage,
                    messages=messages,
                    resolve_generation_model=resolve_generation_model,
                )
                if finalize_requested:
                    should_finalize = readiness_can_finalize

            if stage.stage_key == WorkspaceStageKey.DEPLOYMENT:
                if should_finalize:
                    built_content = build_deployment_stage_fallback_summary(workspace, stages, stage)
                else:
                    recommendation = apply_stage_runtime_metadata(
                        build_deployment_stage_recommendation(stage, stages),
                        ready_to_finalize=readiness_can_finalize,
                        readiness_blockers=readiness_blockers,
                        readiness_message_id=readiness_message_id,
                    )
                    built_content = build_deployment_stage_fallback_content(workspace, stages, stage)

                built_content = normalize_stage_output_paragraphs(
                    built_content.strip(),
                    should_finalize=should_finalize,
                )
                for piece in re.findall(r".{1,48}", built_content, flags=re.S):
                    yield {"event": "content", "data": piece}
                    await asyncio.sleep(0.01)
            elif should_finalize:
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
                    requirements_latest_user_message(messages),
                    enable_web_search=bool(runtime_options and runtime_options.get("enable_web_search")),
                )
                prompt = (
                    build_requirements_conclusion_prompt(
                        workspace,
                        messages,
                        memory_context,
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
                        stage_skill_context=stage_skill_context,
                        external_reference_context=external_reference_context,
                    )
                )
                system_text = (
                    "你是一个擅长整理项目需求结论的产品负责人。"
                    if stage.stage_key == WorkspaceStageKey.REQUIREMENTS
                    else f"你是负责整理「{stage.title}」阶段结论的产品协作助手。"
                )
                agent_name = f"{stage.stage_key.value}-conclusion"
            else:
                readiness_instruction = build_stage_readiness_instruction(stage, readiness_can_finalize, readiness_blockers)
                not_ready_instruction = (
                    build_finalize_not_ready_instruction(stage, readiness_blockers)
                    if finalize_requested and readiness_blockers
                    else None
                )
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
                    requirements_latest_user_message(messages),
                    enable_web_search=bool(runtime_options and runtime_options.get("enable_web_search")),
                )
                prompt = (
                    build_requirements_chat_prompt(
                        workspace,
                        messages,
                        memory_context,
                        extra_instruction=merge_stage_instructions(readiness_instruction, not_ready_instruction),
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
                        extra_instruction=merge_stage_instructions(readiness_instruction, not_ready_instruction),
                        stage_skill_context=stage_skill_context,
                        external_reference_context=external_reference_context,
                    )
                )
                system_text = (
                    "你是一个擅长需求确认和项目推进的产品负责人。"
                    if stage.stage_key == WorkspaceStageKey.REQUIREMENTS
                    else f"你是负责推进「{stage.title}」阶段的产品协作助手。"
                )
                agent_name = (
                    "requirements-conversation"
                    if stage.stage_key == WorkspaceStageKey.REQUIREMENTS
                    else f"{stage.stage_key.value}-conversation"
                )
                recommendation = apply_stage_runtime_metadata({
                    "source": "stream_conversation",
                    "summary": f"{stage.title}阶段已经可以收口，等你确认后整理阶段结论。"
                    if readiness_can_finalize
                    else f"{stage.title}阶段正在通过真实模型对话推进。"
                    if not finalize_requested
                    else f"{stage.title}阶段暂时还不能收口，系统已回到补充确认。",
                    "recommended_action": "如果没有新的补充，就可以直接进入阶段结论。"
                    if readiness_can_finalize
                    else "继续在当前阶段补充；确认足够后再让系统整理结论。"
                    if not finalize_requested
                    else "先补齐当前阻塞点，再生成阶段结论。",
                    "focus": [stage.title, "真实模型回复", "阶段收敛"] if not finalize_requested else [stage.title, "阶段收口判断", "关键缺口"],
                    "options": [],
                    "selected_option": None,
                },
                    ready_to_finalize=readiness_can_finalize,
                    readiness_blockers=readiness_blockers,
                    readiness_message_id=readiness_message_id,
                )

            if stage.stage_key != WorkspaceStageKey.DEPLOYMENT:
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
                        reasoning_effort=runtime_reasoning_effort(runtime_options),
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
                            allow_continuation=should_finalize,
                            resolve_generation_model=resolve_generation_model,
                            reasoning_effort=runtime_reasoning_effort(runtime_options),
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
                        for piece in re.findall(r".{1,48}", built_content, flags=re.S):
                            yield {"event": "content", "data": piece}
                            await asyncio.sleep(0.01)
                        break

                    if not should_finalize and not looks_like_truncated_output(built_content):
                        break

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
                    if round_index >= max_rounds - 1:
                        yield {"event": "error", "data": "当前内容未完整生成，请重试。"}
                        return
                    round_messages = [
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

            if should_finalize:
                summary = normalize_stage_output_paragraphs(built_content.strip(), should_finalize=True)
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
                final_content = normalize_stage_output_paragraphs(built_content.strip(), should_finalize=False)
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
    runtime_options: Optional[Dict[str, Any]] = None,
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
            summary="需求确认阶段当前没有拿到可用的大模型回复。",
            recommended_action="请先检查模型配置；恢复后再回到阶段对话继续。",
            focus=["模型可用性", "阶段对话", "避免旧生成稿"],
            content="\n".join([
                "需求确认阶段当前没有拿到可用的大模型回复。",
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
