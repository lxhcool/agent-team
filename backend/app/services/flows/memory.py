"""Structured memory extraction and retrieval for staged workspace flows."""

import json
import logging
import re
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.router import LLMMessage, llm_router
from app.models.models import User, Workspace, WorkspaceMemory, WorkspaceStage, WorkspaceStageKey

logger = logging.getLogger(__name__)

ACTIVE_MEMORY_STATUSES = {"candidate", "confirmed"}
MEMORY_TYPES = {
    "product_definition",
    "target_user",
    "core_goal",
    "constraint",
    "process_rule",
    "business_rule",
    "capacity_rule",
    "permission_rule",
    "module_definition",
    "page_structure",
    "technical_decision",
    "delivery_item",
}
MEMORY_SCOPES = {"global", "stage"}


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def _normalize_key(value: str) -> str:
    return re.sub(r"\s+", "", (value or "").strip().lower())


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


def _memory_tags_json(tags: Sequence[str]) -> Optional[str]:
    normalized = [item.strip() for item in tags if item and item.strip()]
    return json.dumps(normalized, ensure_ascii=False) if normalized else None


def _memory_tags(memory: WorkspaceMemory) -> List[str]:
    if not memory.tags_json:
        return []
    try:
        value = json.loads(memory.tags_json)
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
    except json.JSONDecodeError:
        return []
    return []


def build_memory_extraction_prompt(
    workspace: Workspace,
    stage: WorkspaceStage,
    *,
    source_kind: str,
    source_text: str,
    existing_memories: Sequence[WorkspaceMemory],
) -> str:
    memory_lines = []
    for item in existing_memories[-24:]:
        memory_lines.append(f"- [{item.memory_type}] {item.topic or '未命名'}：{item.content}")
    existing_block = "\n".join(memory_lines) if memory_lines else "- 暂无"
    return f"""
你要从一段产品协作内容里，提取可以长期复用的结构化事实记忆。

项目：{workspace.name}
当前阶段：{stage.title}
来源类型：{source_kind}

提取规则：
1. 只提取后续阶段仍然会依赖的明确事实，不要提取寒暄、过程描述、态度表达。
2. 不能把整段内容改写成摘要；要拆成一条条独立事实。
3. 只保留来源文本里明确表达的事实，不要脑补，不要补推断。
4. 同一个事实如果只是换说法，不要重复提取。
5. topic 要短，像稳定的记忆键，例如“产品形态”“服务范围”“预约模式”“洗车容量”。
6. memory_type 只能从下面这些值里选：
   product_definition, target_user, core_goal, constraint, process_rule, business_rule, capacity_rule, permission_rule, module_definition, page_structure, technical_decision, delivery_item
7. scope 只能是 global 或 stage。
8. status 只能是 confirmed。
9. tags 是可选短标签数组，没有就留空数组。

已有活跃记忆（避免重复）：
{existing_block}

来源文本：
{source_text}

只返回 JSON：
{{
  "memories": [
    {{
      "memory_type": "constraint",
      "topic": "服务范围",
      "content": "仅支持杭州单店到店预约，不提供上门服务。",
      "scope": "global",
      "status": "confirmed",
      "tags": ["预约", "门店"]
    }}
  ]
}}
""".strip()


async def list_workspace_memories(
    db: AsyncSession,
    workspace_id: str,
    *,
    statuses: Optional[Iterable[str]] = None,
    stage_keys: Optional[Iterable[str]] = None,
) -> List[WorkspaceMemory]:
    query = select(WorkspaceMemory).where(WorkspaceMemory.workspace_id == workspace_id)
    if statuses:
        query = query.where(WorkspaceMemory.status.in_(list(statuses)))
    if stage_keys:
        query = query.where(WorkspaceMemory.stage_key.in_(list(stage_keys)))
    query = query.order_by(WorkspaceMemory.created_at.asc())
    result = await db.execute(query)
    return list(result.scalars().all())


def format_memory_context(memories: Sequence[WorkspaceMemory]) -> str:
    if not memories:
        return "- 暂无已确认记忆。"

    global_lines: List[str] = []
    stage_lines: List[str] = []
    for item in memories:
        line = f"- [{item.memory_type}] {item.topic or '未命名'}：{item.content}"
        if item.scope == "global":
            global_lines.append(line)
        else:
            stage_lines.append(f"- [{item.stage_key}] [{item.memory_type}] {item.topic or '未命名'}：{item.content}")

    blocks: List[str] = []
    if global_lines:
        blocks.extend(["全局已确认事实：", *global_lines])
    if stage_lines:
        if blocks:
            blocks.append("")
        blocks.extend(["阶段已确认事实：", *stage_lines])
    return "\n".join(blocks)


async def build_workspace_memory_context(
    db: AsyncSession,
    workspace_id: str,
    *,
    stage_order: Optional[int] = None,
    stages: Optional[Sequence[WorkspaceStage]] = None,
) -> str:
    memories = await list_workspace_memories(db, workspace_id, statuses={"confirmed"})
    if stage_order is not None and stages is not None:
        order_by_key = {item.stage_key.value: item.order for item in stages}
        memories = [
            item for item in memories
            if order_by_key.get(item.stage_key, -1) <= stage_order
        ]
    return format_memory_context(memories)


async def _extract_memories_with_llm(
    db: AsyncSession,
    user: User,
    workspace: Workspace,
    stage: WorkspaceStage,
    *,
    source_kind: str,
    source_text: str,
    existing_memories: Sequence[WorkspaceMemory],
    resolve_generation_model: Callable[[AsyncSession, User], Awaitable[tuple[Optional[str], Optional[str], Optional[List[str]]]]],
) -> List[Dict[str, Any]]:
    if not source_text.strip():
        return []

    model, provider, fallback_chain = await resolve_generation_model(db, user)
    if not model or not provider:
        return []

    prompt = build_memory_extraction_prompt(
        workspace,
        stage,
        source_kind=source_kind,
        source_text=source_text,
        existing_memories=existing_memories,
    )
    try:
        await llm_router.load_providers(db, user_id=user.id)
        result = await llm_router.call(
            messages=[
                LLMMessage(role="system", content="你是一个负责提取结构化产品事实记忆的助手。"),
                LLMMessage(role="user", content=prompt),
            ],
            model=model,
            provider_name=provider,
            fallback_chain=fallback_chain,
            max_tokens=700,
            temperature=0.0,
            session_id=workspace.id,
            session_type="workspace",
            agent_name="workspace-memory-extractor",
        )
        parsed = _parse_json_object_loose(result.content or "")
        items = parsed.get("memories") if isinstance(parsed, dict) else []
        return items if isinstance(items, list) else []
    except Exception as exc:
        logger.warning(
            "workspace memory extraction failed: workspace=%s stage=%s source=%s reason=%s",
            workspace.id,
            stage.stage_key.value,
            source_kind,
            exc,
        )
        return []


async def _upsert_memory_item(
    db: AsyncSession,
    workspace: Workspace,
    stage: WorkspaceStage,
    *,
    source_message_id: Optional[str],
    source_artifact_id: Optional[str],
    item: Dict[str, Any],
) -> Optional[WorkspaceMemory]:
    memory_type = str(item.get("memory_type") or "").strip()
    topic = _normalize_text(str(item.get("topic") or ""))
    content = _normalize_text(str(item.get("content") or ""))
    status = str(item.get("status") or "confirmed").strip()
    scope = str(item.get("scope") or "global").strip()
    raw_tags = item.get("tags")
    tags = [str(tag).strip() for tag in raw_tags] if isinstance(raw_tags, list) else []

    if not memory_type or memory_type not in MEMORY_TYPES or not content:
        return None
    if not topic:
        topic = memory_type
    if status not in ACTIVE_MEMORY_STATUSES:
        status = "confirmed"
    if scope not in MEMORY_SCOPES:
        scope = "global"

    result = await db.execute(
        select(WorkspaceMemory)
        .where(
            WorkspaceMemory.workspace_id == workspace.id,
            WorkspaceMemory.memory_type == memory_type,
            WorkspaceMemory.topic == topic,
            WorkspaceMemory.scope == scope,
            WorkspaceMemory.status.in_(list(ACTIVE_MEMORY_STATUSES)),
        )
        .order_by(WorkspaceMemory.updated_at.desc())
    )
    existing = list(result.scalars().all())
    for item_existing in existing:
        if _normalize_key(item_existing.content) == _normalize_key(content):
            if source_message_id and not item_existing.source_message_id:
                item_existing.source_message_id = source_message_id
            if source_artifact_id and not item_existing.source_artifact_id:
                item_existing.source_artifact_id = source_artifact_id
            if tags and not item_existing.tags_json:
                item_existing.tags_json = _memory_tags_json(tags)
            return item_existing

    superseded_id = existing[0].id if existing else None
    for item_existing in existing:
        item_existing.status = "superseded"

    memory = WorkspaceMemory(
        workspace_id=workspace.id,
        stage_key=stage.stage_key.value,
        source_message_id=source_message_id,
        source_artifact_id=source_artifact_id,
        memory_type=memory_type,
        topic=topic,
        content=content,
        status=status,
        scope=scope,
        supersedes_memory_id=superseded_id,
        tags_json=_memory_tags_json(tags),
    )
    db.add(memory)
    await db.flush()
    return memory


async def sync_memories_from_source(
    db: AsyncSession,
    user: User,
    workspace: Workspace,
    stage: WorkspaceStage,
    *,
    source_kind: str,
    source_text: str,
    source_message_id: Optional[str] = None,
    source_artifact_id: Optional[str] = None,
    resolve_generation_model: Callable[[AsyncSession, User], Awaitable[tuple[Optional[str], Optional[str], Optional[List[str]]]]],
) -> List[WorkspaceMemory]:
    if source_message_id:
        existing_by_message = await db.execute(
            select(WorkspaceMemory).where(WorkspaceMemory.source_message_id == source_message_id)
        )
        if existing_by_message.scalars().first():
            return []
    if source_artifact_id:
        existing_by_artifact = await db.execute(
            select(WorkspaceMemory).where(WorkspaceMemory.source_artifact_id == source_artifact_id)
        )
        if existing_by_artifact.scalars().first():
            return []

    existing_memories = await list_workspace_memories(
        db,
        workspace.id,
        statuses=ACTIVE_MEMORY_STATUSES,
    )
    extracted = await _extract_memories_with_llm(
        db=db,
        user=user,
        workspace=workspace,
        stage=stage,
        source_kind=source_kind,
        source_text=source_text,
        existing_memories=existing_memories,
        resolve_generation_model=resolve_generation_model,
    )
    created: List[WorkspaceMemory] = []
    for item in extracted:
        if not isinstance(item, dict):
            continue
        memory = await _upsert_memory_item(
            db=db,
            workspace=workspace,
            stage=stage,
            source_message_id=source_message_id,
            source_artifact_id=source_artifact_id,
            item=item,
        )
        if memory is not None:
            created.append(memory)
    return created


async def supersede_stage_memories_from_order(
    db: AsyncSession,
    workspace_id: str,
    stages: Sequence[WorkspaceStage],
    *,
    from_order: int,
) -> None:
    stage_keys = [item.stage_key.value for item in stages if item.order >= from_order]
    if not stage_keys:
        return
    result = await db.execute(
        select(WorkspaceMemory).where(
            WorkspaceMemory.workspace_id == workspace_id,
            WorkspaceMemory.stage_key.in_(stage_keys),
            WorkspaceMemory.status.in_(list(ACTIVE_MEMORY_STATUSES)),
        )
    )
    for item in result.scalars().all():
        item.status = "superseded"


def latest_conclusion_artifact_id(stage: WorkspaceStage) -> Optional[str]:
    if not stage.recommendation_json:
        return None
    try:
        recommendation = json.loads(stage.recommendation_json)
    except json.JSONDecodeError:
        return None
    artifacts = recommendation.get("artifacts")
    if not isinstance(artifacts, list):
        return None
    target_type = f"{stage.stage_key.value}_conclusion"
    for item in artifacts:
        if not isinstance(item, dict):
            continue
        if str(item.get("type") or "").strip() == target_type and str(item.get("artifact_id") or "").strip():
            return str(item.get("artifact_id")).strip()
    return None
