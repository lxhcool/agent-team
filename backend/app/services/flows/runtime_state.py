"""Runtime state helpers for staged flow conversations."""

import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.models.models import WorkspaceStage, WorkspaceStageMessage


def _compact_text(value: str) -> str:
    return re.sub(r"\s+", "", (value or "")).lower()


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
    blocker_state: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    existing_runtime = recommendation.get("stage_runtime")
    existing_blocker_state = (
        existing_runtime.get("blocker_state")
        if isinstance(existing_runtime, dict) and isinstance(existing_runtime.get("blocker_state"), list)
        else []
    )
    recommendation["stage_runtime"] = {
        "ready_to_finalize": ready_to_finalize,
        "readiness_blockers": readiness_blockers[:3],
        "readiness_message_id": readiness_message_id,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "blocker_state": blocker_state if blocker_state is not None else existing_blocker_state,
    }
    return recommendation


def latest_user_message_id(messages: List[WorkspaceStageMessage]) -> Optional[str]:
    for item in reversed(messages):
        if item.role == "user" and item.content.strip():
            return item.id
    return None


def stage_is_ready_to_finalize(stage: WorkspaceStage, messages: List[WorkspaceStageMessage]) -> bool:
    metadata = stage_runtime_metadata(stage)
    if not metadata.get("ready_to_finalize"):
        return False
    readiness_message_id = str(metadata.get("readiness_message_id") or "").strip()
    return bool(readiness_message_id) and readiness_message_id == latest_user_message_id(messages)


def _blocker_key(value: str) -> str:
    text = _compact_text(value)
    text = re.sub(r"[^\u4e00-\u9fffa-z0-9]+", "", text)
    return text[:80]


def stage_blocker_state(stage: WorkspaceStage) -> List[Dict[str, Any]]:
    metadata = stage_runtime_metadata(stage)
    raw = metadata.get("blocker_state")
    if not isinstance(raw, list):
        return []
    items: List[Dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        text = str(item.get("text") or "").strip()
        if not key or not text:
            continue
        items.append({
            "key": key,
            "text": text,
            "status": str(item.get("status") or "open").strip() or "open",
            "asked_count": max(0, int(item.get("asked_count") or 0)),
            "last_question_message_id": str(item.get("last_question_message_id") or "").strip() or None,
            "last_answer_message_id": str(item.get("last_answer_message_id") or "").strip() or None,
            "last_question": str(item.get("last_question") or "").strip() or None,
            "last_answer": str(item.get("last_answer") or "").strip() or None,
            "updated_at": str(item.get("updated_at") or "").strip() or None,
        })
    return items[:6]


def _message_index(messages: List[WorkspaceStageMessage], message_id: Optional[str]) -> int:
    if not message_id:
        return -1
    for index, item in enumerate(messages):
        if item.id == message_id:
            return index
    return -1


def _latest_user_text(messages: List[WorkspaceStageMessage]) -> str:
    for item in reversed(messages):
        if item.role == "user" and item.content.strip():
            return item.content.strip()
    return ""


def build_blocker_state(
    stage: WorkspaceStage,
    messages: List[WorkspaceStageMessage],
    blockers: List[str],
) -> List[Dict[str, Any]]:
    previous_by_key = {
        str(item.get("key")): item
        for item in stage_blocker_state(stage)
        if isinstance(item, dict) and str(item.get("key") or "").strip()
    }
    latest_user_id = latest_user_message_id(messages)
    latest_user_text = _latest_user_text(messages).strip()
    latest_user_index = _message_index(messages, latest_user_id)

    next_items: List[Dict[str, Any]] = []
    current_keys: set[str] = set()
    for blocker in blockers[:3]:
        text = str(blocker or "").strip()
        if not text:
            continue
        key = _blocker_key(text)
        current_keys.add(key)
        existing = previous_by_key.get(key, {})
        question_index = _message_index(messages, existing.get("last_question_message_id"))
        answer_index = _message_index(messages, existing.get("last_answer_message_id"))
        status = str(existing.get("status") or "open").strip() or "open"

        answered_after_question = (
            latest_user_id is not None
            and question_index >= 0
            and latest_user_index > question_index
            and latest_user_index > answer_index
            and bool(latest_user_text)
        )
        if answered_after_question:
            status = "answered"
        elif status == "closed":
            status = "open"

        item = {
            "key": key,
            "text": text,
            "status": status,
            "asked_count": max(0, int(existing.get("asked_count") or 0)),
            "last_question_message_id": existing.get("last_question_message_id"),
            "last_answer_message_id": latest_user_id if answered_after_question else existing.get("last_answer_message_id"),
            "last_question": existing.get("last_question"),
            "last_answer": latest_user_text if answered_after_question else existing.get("last_answer"),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        next_items.append(item)

    for key, existing in previous_by_key.items():
        if key in current_keys:
            continue
        closed_item = dict(existing)
        closed_item["status"] = "closed"
        closed_item["updated_at"] = datetime.now(timezone.utc).isoformat()
        next_items.append(closed_item)

    return next_items[:6]


def _active_blockers(blockers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [item for item in blockers if str(item.get("status") or "") != "closed"]


def build_blocker_instruction(blockers: List[Dict[str, Any]]) -> str:
    active = _active_blockers(blockers)
    if not active:
        return """
当前没有真实阻塞点。
不要继续发明新问题。
""".strip()

    primary = active[0]
    backlog = [str(item.get("text") or "").strip() for item in active[1:3] if str(item.get("text") or "").strip()]
    question_cap = max(0, 2 - int(primary.get("asked_count") or 0))

    lines = [
        f"当前第一阻塞点：{primary.get('text')}",
        "这一轮只围绕这个点推进。",
    ]
    if primary.get("status") == "answered":
        lines.extend([
            "用户刚刚已经回应过这个点，不要重复问同一个问题。",
            "先吸收这次回答，再给当前判断。",
        ])
        if question_cap > 0:
            lines.append("只有仍然无法推进时，才补 1 个更具体的问题。")
        else:
            lines.append("这个点已经追问到上限，直接给当前判断。")
    elif int(primary.get("asked_count") or 0) >= 2:
        lines.extend([
            "这个点已经追问到上限，不要继续追问。",
            "基于当前信息直接给当前判断，不要把判断再推回给用户。",
        ])
    else:
        lines.extend([
            f"这个点还允许最多追问 {question_cap} 次，但本轮最多只问 1 个问题。",
            "如果能先给当前判断，就先给；只有这样仍不足以推进时，才问问题。",
        ])

    if backlog:
        lines.append("其他点先不展开：" + "；".join(backlog))
    return "\n".join(lines)


def _extract_last_question(content: str) -> Optional[str]:
    lines = [line.strip() for line in re.split(r"[\n\r]+", content or "") if line.strip()]
    question_lines = [line for line in lines if "？" in line or "?" in line]
    if question_lines:
        return question_lines[-1]
    return None


def update_blockers_after_assistant_reply(
    blockers: List[Dict[str, Any]],
    assistant_message: WorkspaceStageMessage,
) -> List[Dict[str, Any]]:
    active = _active_blockers(blockers)
    if not active:
        return blockers

    primary = active[0]
    last_question = _extract_last_question(assistant_message.content)
    if last_question:
        primary["asked_count"] = min(2, int(primary.get("asked_count") or 0) + 1)
        primary["status"] = "open"
        primary["last_question_message_id"] = assistant_message.id
        primary["last_question"] = last_question
        primary["updated_at"] = datetime.now(timezone.utc).isoformat()
        return blockers

    if primary.get("status") == "answered":
        primary["status"] = "closed"
    else:
        primary["status"] = "assumed"
    primary["updated_at"] = datetime.now(timezone.utc).isoformat()
    return blockers
