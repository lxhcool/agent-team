"""Expert-group reviews for staged workspace flow outputs."""

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.router import LLMMessage, llm_router
from app.models.models import User, Workspace, WorkspaceStage, WorkspaceStageKey, WorkspaceStageMessage, WorkspaceStageReview
from app.services.flows.expert_assets import STAGE_REVIEW_CONFIGS

logger = logging.getLogger(__name__)


class StageReviewError(Exception):
    """Raised when a stage review cannot be created or generated."""


EXPERT_REVIEW_STAGES = {
    WorkspaceStageKey.PRODUCT,
    WorkspaceStageKey.UI_DIRECTION,
    WorkspaceStageKey.TECHNICAL,
}
REVIEW_STATUS_QUEUED = "queued"
REVIEW_STATUS_RUNNING = "running"
REVIEW_STATUS_PARTIAL = "partial"
REVIEW_STATUS_COMPLETED = "completed"
REVIEW_STATUS_FAILED = "failed"

def _stage_review_config(stage_key: WorkspaceStageKey) -> Dict[str, Any]:
    config = STAGE_REVIEW_CONFIGS.get(stage_key)
    if not config:
        raise StageReviewError("当前阶段没有专业视角检查。")
    return config


def _parse_json_object_loose(content: str) -> Optional[Dict[str, Any]]:
    text = (content or "").strip()
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


def _normalize_string_list(value: Any, limit: int = 5) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()][:limit]


def _normalize_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "1", "是"}:
            return True
        if normalized in {"false", "no", "0", "否"}:
            return False
    return bool(value)


def _fallback_expert_conclusion(parsed: Dict[str, Any], content: str) -> str:
    conclusion = str(parsed.get("conclusion") or "").strip()
    vague_conclusions = {"未形成明确结论", "暂无明确结论", "无明确结论", "需要进一步明确"}
    if conclusion and conclusion not in vague_conclusions:
        return conclusion

    issues = _normalize_string_list(parsed.get("issues"), 3)
    suggestions = _normalize_string_list(parsed.get("suggestions"), 3)
    if issues:
        return f"发现 {len(issues)} 个需要关注的问题：{issues[0]}"
    if suggestions:
        return f"建议补充 {len(suggestions)} 个方向：{suggestions[0]}"

    raw = content.strip()
    if raw and not raw.startswith("{"):
        return raw[:220].strip()
    return "这个视角没有发现明确阻塞点。"


def _compact_text(value: str, limit: int) -> str:
    text = re.sub(r"\n{3,}", "\n\n", (value or "").strip())
    if len(text) <= limit:
        return text
    head_limit = max(0, int(limit * 0.72))
    tail_limit = max(0, limit - head_limit - 24)
    return f"{text[:head_limit].rstrip()}\n\n...[中间内容已压缩]...\n\n{text[-tail_limit:].lstrip()}"


def _latest_stage_draft(
    stage: WorkspaceStage,
    messages: Sequence[WorkspaceStageMessage],
) -> tuple[str, Optional[str]]:
    for message in reversed(messages):
        if message.role == "assistant" and message.kind == "conclusion" and message.content.strip():
            return message.content.strip(), message.id
    for message in reversed(messages):
        if message.role == "assistant" and message.content.strip():
            return message.content.strip(), message.id
    if stage.content and stage.content.strip():
        return stage.content.strip(), None
    if stage.recommendation_json:
        try:
            recommendation = json.loads(stage.recommendation_json)
            summary = str(recommendation.get("summary") or "").strip() if isinstance(recommendation, dict) else ""
            if summary:
                return summary, None
        except json.JSONDecodeError:
            pass
    return "", None


def _confirmed_stage_context(stages: Sequence[WorkspaceStage], current_stage: WorkspaceStage) -> str:
    lines: List[str] = []
    remaining = 800
    for item in sorted(stages, key=lambda stage: stage.order):
        if item.order >= current_stage.order or remaining <= 0:
            continue
        content = (item.content or "").strip()
        if not content and item.recommendation_json:
            try:
                recommendation = json.loads(item.recommendation_json)
                content = str(recommendation.get("summary") or "").strip() if isinstance(recommendation, dict) else ""
            except json.JSONDecodeError:
                content = ""
        if not content:
            continue
        piece = _compact_text(content, min(remaining, 500))
        lines.append(f"## {item.title}\n{piece}")
        remaining -= len(piece)
    return "\n\n".join(lines).strip() or "暂无已确认上游文档。"


def _recent_user_context(messages: Sequence[WorkspaceStageMessage]) -> str:
    user_messages = [item.content.strip() for item in messages if item.role == "user" and item.content.strip()]
    if not user_messages:
        return "暂无"
    return _compact_text("\n".join(f"- {item}" for item in user_messages[-4:]), 600)


def _build_review_context(
    *,
    workspace: Workspace,
    stage: WorkspaceStage,
    stages: Sequence[WorkspaceStage],
    messages: Sequence[WorkspaceStageMessage],
    draft: str,
) -> Dict[str, str]:
    config = _stage_review_config(stage.stage_key)
    return {
        "project": _compact_text(f"{workspace.name}\n{workspace.description or ''}", 360),
        "stage_name": str(config["stage_name"]),
        "review_target": str(config["review_target"]),
        "stage_goal": str(config["stage_goal"]),
        "upstream": _confirmed_stage_context(stages, stage),
        "draft": _compact_text(draft, 3200),
        "recent_user_context": _recent_user_context(messages),
        "boundary": str(config["boundary"]),
    }


def _expert_prompt(*, expert: Dict[str, str], context: Dict[str, str]) -> str:
    expert_instruction = _compact_text(str(expert.get("system_prompt") or expert.get("focus") or ""), 1800)
    return f"""
你是「{expert["agent_name"]}」。你的专长：{expert["expertise"]}。

只从你的专业视角检查「{context["review_target"]}」草稿，不要重写草稿，不要替用户做新方案。

你的工作指令：
{expert_instruction}

审查上下文：
项目一句话：
{context["project"]}

当前阶段目标：
{context["stage_goal"]}

上游已确认约束：
{context["upstream"]}

最近用户关键补充：
{context["recent_user_context"]}

待检查草稿：
{context["draft"]}

边界：
{context["boundary"]}

输出规则：
1. 只返回 JSON，不要 Markdown，不要代码块。
2. 每个数组最多 3 条。
3. 如果没有真实阻塞，不要硬写 blocking=true。
4. 问题必须具体说明为什么会影响后续确认。
5. 如果只有建议没有阻塞，conclusion 要明确写“可继续推进”或“可推进但建议补充”，不要写“未形成明确结论”。

JSON：
{{
  "conclusion": "一句话结论",
  "issues": ["主要问题，最多3条"],
  "suggestions": ["建议补充，最多3条"],
  "blocking": false,
  "user_confirmations": ["需要用户确认的问题，最多2条"],
  "confidence": "high|medium|low"
}}
""".strip()


async def _call_expert(
    *,
    user: User,
    workspace: Workspace,
    model: str,
    provider: str,
    fallback_chain: Optional[List[str]],
    expert: Dict[str, str],
    context: Dict[str, str],
    reasoning_effort: Optional[str],
) -> Dict[str, Any]:
    prompt = _expert_prompt(expert=expert, context=context)
    try:
        result = await llm_router.call(
            messages=[
                LLMMessage(role="system", content="你是严谨的阶段审查专家，只输出可解析 JSON。"),
                LLMMessage(role="user", content=prompt),
            ],
            model=model,
            provider_name=provider,
            fallback_chain=fallback_chain,
            max_tokens=760,
            temperature=0.15,
            session_id=workspace.id,
            session_type="workspace",
            agent_name=expert["agent_id"],
            reasoning_effort=reasoning_effort or "low",
        )
        return _normalize_expert_finding(expert, result.content or "")
    except Exception as exc:
        logger.warning(
            "stage expert review failed: workspace=%s expert=%s reason=%s",
            workspace.id,
            expert["agent_id"],
            exc,
        )
        return _normalize_expert_finding(expert, f"该专家视角生成失败：{exc}")


def _normalize_expert_finding(expert: Dict[str, str], content: str) -> Dict[str, Any]:
    parsed = _parse_json_object_loose(content) or {}
    failed = content.strip().startswith("该专家视角生成失败")
    return {
        "agent_id": expert["agent_id"],
        "agent_name": expert["agent_name"],
        "expertise": expert["expertise"],
        "conclusion": _fallback_expert_conclusion(parsed, content),
        "issues": _normalize_string_list(parsed.get("issues"), 3) or ([content.strip()] if failed else []),
        "suggestions": _normalize_string_list(parsed.get("suggestions"), 3),
        "blocking": _normalize_bool(parsed.get("blocking")),
        "user_confirmations": _normalize_string_list(parsed.get("user_confirmations"), 2),
        "confidence": str(parsed.get("confidence") or ("low" if failed else "medium")).strip(),
        "generation_error": failed,
    }


def _dedupe(items: Sequence[str], limit: int) -> List[str]:
    result: List[str] = []
    seen: set[str] = set()
    for item in items:
        normalized = re.sub(r"\s+", "", item).lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(item)
        if len(result) >= limit:
            break
    return result


def _build_summary(findings: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    failed_findings = [item for item in findings if item.get("generation_error")]
    valid_findings = [item for item in findings if not item.get("generation_error")]
    blocking_findings = [item for item in valid_findings if item.get("blocking")]

    risks = _dedupe(
        [
            *[issue for item in blocking_findings for issue in _normalize_string_list(item.get("issues"), 3)],
            *[issue for item in valid_findings for issue in _normalize_string_list(item.get("issues"), 3)],
        ],
        5,
    )
    supplements = _dedupe(
        [suggestion for item in valid_findings for suggestion in _normalize_string_list(item.get("suggestions"), 3)],
        5,
    )
    confirmations = _dedupe(
        [question for item in valid_findings for question in _normalize_string_list(item.get("user_confirmations"), 2)],
        4,
    )

    if len(failed_findings) == len(findings):
        return {
            "overall_judgment": "专业检查生成失败",
            "why": "所有专家视角都没有成功返回结果，不能把这次检查当作有效判断。",
            "main_risks": _dedupe([issue for item in failed_findings for issue in _normalize_string_list(item.get("issues"), 2)], 5),
            "expert_conflicts": [],
            "suggested_supplements": ["请稍后重新生成专业视角检查，或检查模型配置。"],
            "focus_for_user": ["这次检查不可作为进入下一阶段的依据。"],
            "can_enter_next_stage": False,
            "user_confirmation_questions": [],
        }

    conflicts: List[str] = []
    if blocking_findings and len(blocking_findings) < len(valid_findings):
        blocking_names = "、".join(str(item.get("agent_name")) for item in blocking_findings)
        conflicts.append(f"{blocking_names}认为存在阻塞点，其他专家未标记为阻塞。")
    if failed_findings:
        conflicts.append("部分专家未成功返回，本次检查结果不完整。")

    can_enter_next_stage = not blocking_findings and not failed_findings
    if blocking_findings:
        overall = "建议先补充后再推进"
        why = "至少一个专家视角发现了会影响后续阶段判断的缺口。"
    elif failed_findings:
        overall = "可以参考，但不建议单独作为推进依据"
        why = "已有专家没有发现阻塞性问题，但本次检查结果不完整。"
    elif risks or supplements or confirmations:
        overall = "可以推进，但建议补充"
        why = "专家组没有发现阻塞性问题，但仍有少量信息可以补强。"
    else:
        overall = "可以继续推进"
        why = "专家组没有发现阻塞性问题。"

    focus = confirmations[:3] or supplements[:3] or risks[:3]
    return {
        "overall_judgment": overall,
        "why": why,
        "main_risks": risks,
        "expert_conflicts": conflicts[:4],
        "suggested_supplements": supplements,
        "focus_for_user": focus,
        "can_enter_next_stage": can_enter_next_stage,
        "user_confirmation_questions": confirmations,
    }


def _summary_markdown(result: Dict[str, Any]) -> str:
    lines = [
        f"**总体判断**：{result.get('overall_judgment') or '暂无'}",
        f"**原因**：{result.get('why') or '暂无'}",
    ]
    sections = [
        ("主要风险", result.get("main_risks")),
        ("专家分歧", result.get("expert_conflicts")),
        ("建议补充", result.get("suggested_supplements")),
        ("你需要重点看", result.get("focus_for_user")),
        ("需要你确认的问题", result.get("user_confirmation_questions")),
    ]
    for title, value in sections:
        items = _normalize_string_list(value, 6)
        if items:
            lines.append(f"\n**{title}**")
            lines.extend([f"- {item}" for item in items])
    lines.append(f"\n**是否建议进入下一阶段**：{'是' if result.get('can_enter_next_stage') else '否'}")
    return "\n".join(lines).strip()


def build_review_revision_instruction(review: WorkspaceStageReview) -> str:
    stage_key = WorkspaceStageKey(review.stage_key)
    config = _stage_review_config(stage_key)
    stage_name = str(config["stage_name"])
    revision_boundary = str(config["revision_boundary"])
    result: Dict[str, Any] = {}
    if review.result_json:
        try:
            parsed = json.loads(review.result_json)
            result = parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            result = {}

    findings: List[Dict[str, Any]] = []
    if review.expert_findings_json:
        try:
            parsed = json.loads(review.expert_findings_json)
            findings = parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            findings = []

    lines = [
        f"请基于刚生成的专业视角检查，修订当前「{stage_name}」阶段方案。",
        "",
        "修订要求：",
        "1. 保留原方案里已经成立的判断，不要从头换一个方案。",
        "2. 优先处理专家指出的主要风险、建议补充和需要用户确认的问题。",
        "3. 如果某个问题需要用户确认但当前信息不足，请给出清晰默认口径，并标明需要用户确认。",
        f"4. {revision_boundary}",
        "5. 输出一版可直接替换当前阶段草稿的新版方案。",
        "",
        "专家组汇总结论：",
        f"- 总体判断：{result.get('overall_judgment') or '暂无'}",
        f"- 原因：{result.get('why') or '暂无'}",
    ]
    sections = [
        ("主要风险", result.get("main_risks")),
        ("建议补充", result.get("suggested_supplements")),
        ("需要重点看", result.get("focus_for_user")),
        ("需要用户确认的问题", result.get("user_confirmation_questions")),
    ]
    for title, value in sections:
        items = _normalize_string_list(value, 6)
        if not items:
            continue
        lines.append("")
        lines.append(f"{title}：")
        lines.extend([f"- {item}" for item in items])

    if findings:
        lines.append("")
        lines.append("各专家关键意见：")
        for finding in findings[:3]:
            if finding.get("generation_error"):
                continue
            lines.append(f"- {finding.get('agent_name') or '专家'}：{finding.get('conclusion') or '无明确结论'}")
    return "\n".join(lines).strip()


def create_queued_stage_review(
    *,
    db: AsyncSession,
    user: User,
    workspace: Workspace,
    stage: WorkspaceStage,
    messages: Sequence[WorkspaceStageMessage],
) -> WorkspaceStageReview:
    if stage.stage_key not in EXPERT_REVIEW_STAGES:
        raise StageReviewError("当前阶段没有专业视角检查。")
    config = _stage_review_config(stage.stage_key)

    draft, draft_message_id = _latest_stage_draft(stage, messages)
    if not draft:
        raise StageReviewError("当前阶段还没有可检查的方案草稿，请先生成阶段回复或阶段结论。")

    result = {
        "overall_judgment": "专业检查已创建",
        "why": "专家组正在检查当前方案草稿。",
        "main_risks": [],
        "expert_conflicts": [],
        "suggested_supplements": [],
        "focus_for_user": [],
        "can_enter_next_stage": False,
        "user_confirmation_questions": [],
    }
    review = WorkspaceStageReview(
        workspace_id=workspace.id,
        stage_id=stage.id,
        stage_key=stage.stage_key.value,
        status=REVIEW_STATUS_QUEUED,
        review_type="expert_group",
        draft_message_id=draft_message_id,
        participants_json=json.dumps(config["experts"], ensure_ascii=False),
        expert_findings_json=json.dumps([], ensure_ascii=False),
        summary="专业视角检查已创建，正在生成结果。",
        result_json=json.dumps(result, ensure_ascii=False),
        created_by=user.id,
    )
    db.add(review)
    return review


async def run_stage_expert_review(
    *,
    db: AsyncSession,
    user: User,
    workspace: Workspace,
    stages: Sequence[WorkspaceStage],
    stage: WorkspaceStage,
    messages: Sequence[WorkspaceStageMessage],
    review: WorkspaceStageReview,
    runtime_options: Optional[Dict[str, Any]],
    resolve_generation_model: Callable[[AsyncSession, User], Awaitable[tuple[Optional[str], Optional[str], Optional[List[str]]]]],
) -> WorkspaceStageReview:
    if review.status == "superseded":
        return review

    draft, _draft_message_id = _latest_stage_draft(stage, messages)
    if not draft:
        raise StageReviewError("当前阶段还没有可检查的方案草稿，请先生成阶段回复或阶段结论。")

    model, provider, fallback_chain = await resolve_generation_model(db, user)
    if not model or not provider:
        raise StageReviewError("未配置可用模型，无法生成专业视角检查。")

    review.status = REVIEW_STATUS_RUNNING
    review.summary = "专家组正在检查当前方案草稿。"
    review.updated_at = datetime.now(timezone.utc)
    await db.commit()

    await llm_router.load_providers(db, user_id=user.id)
    context = _build_review_context(
        workspace=workspace,
        stage=stage,
        stages=stages,
        messages=messages,
        draft=draft,
    )
    config = _stage_review_config(stage.stage_key)
    experts = list(config["experts"])
    requested_effort = str((runtime_options or {}).get("reasoning_effort") or "").strip().lower()
    reasoning_effort = requested_effort if requested_effort in {"low", "medium", "high"} else "low"

    findings = await asyncio.gather(
        *[
            _call_expert(
                user=user,
                workspace=workspace,
                model=model,
                provider=provider,
                fallback_chain=fallback_chain,
                expert=expert,
                context=context,
                reasoning_effort=reasoning_effort,
            )
            for expert in experts
        ]
    )

    await db.refresh(review)
    if review.status == "superseded":
        return review

    result = _build_summary(findings)
    failed_count = len([item for item in findings if item.get("generation_error")])
    if failed_count == len(findings):
        review.status = REVIEW_STATUS_FAILED
    elif failed_count:
        review.status = REVIEW_STATUS_PARTIAL
    else:
        review.status = REVIEW_STATUS_COMPLETED
    review.expert_findings_json = json.dumps(findings, ensure_ascii=False)
    review.summary = _summary_markdown(result)
    review.result_json = json.dumps(result, ensure_ascii=False)
    review.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(review)
    return review
