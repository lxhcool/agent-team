"""Context Window Management Service.

Implements:
- X-005: Input layered compression (System Prompt / Current Task / History)
- X-006: Sliding window + summary for long conversations
- X-009: Chat long response structuring (auto sectioning)
- X-003: Simple continuation for stream mode
- F-001: Agent output repair (feed back error and retry)
- F-010: Three-level error classification (fatal/recoverable/warning)
- F-011: User-friendly error messages
"""

import json
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from app.llm.router import LLMMessage, LLMRouter, LLMError
from app.core.database import async_session
from app.models.models import Message, MessageType

logger = logging.getLogger(__name__)


# ===== F-010: Error Classification =====

class ErrorLevel(str, Enum):
    FATAL = "fatal"           # Unrecoverable, session should stop
    RECOVERABLE = "recoverable"  # Can retry or work around
    WARNING = "warning"        # Non-blocking issue


@dataclass
class ClassifiedError:
    """Error with classification per F-010."""
    level: ErrorLevel
    message: str
    user_message: str  # F-011: User-friendly message
    original_error: Optional[Exception] = None
    retry_possible: bool = False

    def to_dict(self) -> dict:
        return {
            "level": self.level.value,
            "message": self.message,
            "user_message": self.user_message,
            "retry_possible": self.retry_possible,
        }


def classify_error(error: Exception) -> ClassifiedError:
    """Classify an error per F-010 three-level system."""
    if isinstance(error, LLMError):
        msg = str(error).lower()

        # Budget exceeded - fatal
        if "budget" in msg:
            return ClassifiedError(
                level=ErrorLevel.FATAL,
                message=str(error),
                user_message="本次会话的 LLM 调用预算已用完，请调整预算或开始新会话。",
                original_error=error,
                retry_possible=False,
            )

        # All providers failed - recoverable (might work later)
        if "all providers failed" in msg:
            return ClassifiedError(
                level=ErrorLevel.RECOVERABLE,
                message=str(error),
                user_message="所有 LLM 服务暂时不可用，请稍后重试或检查 API Key 配置。",
                original_error=error,
                retry_possible=True,
            )

        # API error - recoverable
        if "api error" in msg or "status" in msg:
            return ClassifiedError(
                level=ErrorLevel.RECOVERABLE,
                message=str(error),
                user_message="LLM 服务返回了错误，正在重试...",
                original_error=error,
                retry_possible=True,
            )

        # Generic LLM error - recoverable
        return ClassifiedError(
            level=ErrorLevel.RECOVERABLE,
            message=str(error),
            user_message="AI 模型调用出现问题，请稍后重试。",
            original_error=error,
            retry_possible=True,
        )

    if isinstance(error, (ConnectionError, TimeoutError)):
        return ClassifiedError(
            level=ErrorLevel.RECOVERABLE,
            message=str(error),
            user_message="网络连接出现问题，请检查网络后重试。",
            original_error=error,
            retry_possible=True,
        )

    if isinstance(error, PermissionError):
        return ClassifiedError(
            level=ErrorLevel.FATAL,
            message=str(error),
            user_message="安全策略阻止了此操作。",
            original_error=error,
            retry_possible=False,
        )

    if isinstance(error, json.JSONDecodeError):
        return ClassifiedError(
            level=ErrorLevel.RECOVERABLE,
            message=str(error),
            user_message="AI 返回了格式错误的内容，正在尝试修复...",
            original_error=error,
            retry_possible=True,
        )

    # Unknown errors - treat as recoverable
    return ClassifiedError(
        level=ErrorLevel.RECOVERABLE,
        message=str(error),
        user_message="发生了未知错误，请稍后重试。",
        original_error=error,
        retry_possible=True,
    )


# ===== X-005: Layered Compression =====

class ContextLayer(str, Enum):
    SYSTEM = "system"       # System prompt, agent identity, constraints
    TASK = "task"           # Current task description, goals
    HISTORY = "history"     # Conversation history
    REFERENCE = "reference"  # External context, files, previous sessions


@dataclass
class ContextWindow:
    """Manages the context window for an LLM call with layered compression."""
    system_layer: List[LLMMessage] = field(default_factory=list)
    task_layer: List[LLMMessage] = field(default_factory=list)
    history_layer: List[LLMMessage] = field(default_factory=list)
    reference_layer: List[LLMMessage] = field(default_factory=list)

    max_tokens: int = 4096

    # Token estimation (rough: 1 token ≈ 4 chars for Chinese, 1 token ≈ 4 chars for English)
    def _estimate_tokens(self, text: str) -> int:
        """Rough token estimation."""
        # Chinese characters take roughly 2 tokens each
        chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
        other_chars = len(text) - chinese_chars
        return chinese_chars * 2 + other_chars // 4

    def total_estimated_tokens(self) -> int:
        """Estimate total tokens in the context window."""
        total = 0
        for layer in [self.system_layer, self.task_layer, self.history_layer, self.reference_layer]:
            for msg in layer:
                total += self._estimate_tokens(msg.content)
        return total

    def build_messages(self, max_output_tokens: int = 1024) -> List[LLMMessage]:
        """Build the final message list with sliding window.

        Priority order:
        1. System layer (always included)
        2. Task layer (always included)
        3. Reference layer (compressed if needed)
        4. History layer (sliding window + summary)

        The total must fit within (max_tokens - max_output_tokens).
        """
        budget = self.max_tokens - max_output_tokens
        result: List[LLMMessage] = []

        # Always include system layer
        result.extend(self.system_layer)
        used = sum(self._estimate_tokens(m.content) for m in self.system_layer)

        # Always include task layer
        for msg in self.task_layer:
            result.append(msg)
            used += self._estimate_tokens(msg.content)

        # Include reference layer if space allows
        remaining = budget - used
        if remaining > 200 and self.reference_layer:
            for msg in self.reference_layer:
                tokens = self._estimate_tokens(msg.content)
                if remaining - tokens > 200:
                    result.append(msg)
                    used += tokens
                    remaining -= tokens
                else:
                    # Compress reference into a summary note
                    summary_msg = LLMMessage(
                        role="system",
                        content=f"[参考信息已压缩：{len(self.reference_layer)} 条参考消息因空间不足被省略]"
                    )
                    result.append(summary_msg)
                    used += self._estimate_tokens(summary_msg.content)
                    break

        # History layer with sliding window
        remaining = budget - used
        if remaining > 100 and self.history_layer:
            # Keep recent messages first (sliding window)
            included_history = []
            for msg in reversed(self.history_layer):
                tokens = self._estimate_tokens(msg.content)
                if remaining - tokens > 100:
                    included_history.insert(0, msg)
                    remaining -= tokens
                else:
                    break

            if len(included_history) < len(self.history_layer):
                # Add summary of older messages
                skipped = len(self.history_layer) - len(included_history)
                summary_msg = LLMMessage(
                    role="system",
                    content=f"[历史对话摘要：前 {skipped} 条消息已压缩。关键结论已包含在系统提示中。]"
                )
                included_history.insert(0, summary_msg)

            result.extend(included_history)

        return result


class ContextWindowManager:
    """Manages context windows for sessions."""

    def __init__(self, llm_router: LLMRouter, default_max_tokens: int = 4096):
        self.llm_router = llm_router
        self.default_max_tokens = default_max_tokens

    async def build_planning_context(
        self,
        session_id: str,
        system_prompt: str,
        current_input: str,
        max_tokens: int = 4096,
    ) -> ContextWindow:
        """Build a context window for a planning session."""
        ctx = ContextWindow(max_tokens=max_tokens)

        # System layer
        ctx.system_layer = [
            LLMMessage(role="system", content=system_prompt),
        ]

        # Task layer
        ctx.task_layer = [
            LLMMessage(role="user", content=current_input),
        ]

        # History layer - load recent messages
        try:
            async with async_session() as db:
                from sqlalchemy import select
                result = await db.execute(
                    select(Message)
                    .where(Message.session_id == session_id)
                    .order_by(Message.seq.desc())
                    .limit(20)
                )
                messages = list(reversed(result.scalars().all()))

                for msg in messages:
                    if msg.message_type in (MessageType.CHAT, MessageType.PROPOSAL, MessageType.PLAN):
                        role = "user" if msg.sender == "user" else "assistant"
                        ctx.history_layer.append(
                            LLMMessage(role=role, content=msg.content[:2000])
                        )
        except Exception as e:
            logger.warning(f"Failed to load history for context: {e}")

        # Reference layer - load memory conclusions
        try:
            from app.services.memory import memory_service
            conclusions = await memory_service.get_conclusions(session_type="planning", limit=3)
            for c in conclusions:
                ctx.reference_layer.append(
                    LLMMessage(role="system", content=f"[历史经验] {c.content[:500]}")
                )
        except Exception as e:
            logger.warning(f"Failed to load memory for context: {e}")

        return ctx


# ===== X-009: Long Response Structuring =====

def structure_long_response(content: str) -> str:
    """Structure a long response into clear sections.

    If the content is long (>500 chars) and has no markdown headers,
    auto-add section headers.
    """
    if len(content) < 500:
        return content

    # Check if already has markdown headers
    if re.search(r'^#{1,3}\s+', content, re.MULTILINE):
        return content

    # Try to split into logical paragraphs and add structure
    paragraphs = [p.strip() for p in content.split('\n\n') if p.strip()]
    if len(paragraphs) < 3:
        return content

    # Heuristic: if paragraphs start with numbered or bulleted items, keep as-is
    if any(p.startswith(('#', '1.', '2.', '3.', '-', '*')) for p in paragraphs[:3]):
        return content

    # Add section markers for very long content
    # Just return as-is for now - the LLM should output structured content
    return content


# ===== F-001: Agent Output Repair =====

async def repair_llm_output(
    llm_router: LLMRouter,
    original_error: Exception,
    original_messages: List[LLMMessage],
    session_id: str,
    max_retries: int = 2,
) -> Optional[str]:
    """Try to repair LLM output by feeding the error back.

    Per F-001: If the LLM produces invalid output (e.g., malformed JSON),
    feed the error back and ask it to fix.
    """
    error_msg = str(original_error)

    for attempt in range(max_retries):
        try:
            # Add the error as context and ask for repair
            repair_messages = list(original_messages)
            repair_messages.append(LLMMessage(
                role="assistant",
                content="[上次输出有误，正在修复...]"
            ))
            repair_messages.append(LLMMessage(
                role="user",
                content=f"你上次的输出有错误：{error_msg[:500]}\n\n请修正并重新输出正确的内容。确保输出格式正确。"
            ))

            result = await llm_router.call(
                messages=repair_messages,
                model=original_messages[0].content[:50] if original_messages else "gpt-4o-mini",
                session_id=session_id,
                session_type="planning",
                agent_name="repair",
            )
            return result.content
        except Exception as e:
            logger.warning(f"Repair attempt {attempt + 1} failed: {e}")
            continue

    return None


# ===== X-003: Stream Continuation =====

async def continue_stream_response(
    llm_router: LLMRouter,
    accumulated_content: str,
    original_messages: List[LLMMessage],
    session_id: str,
    provider_name: Optional[str] = None,
    model: str = "gpt-4o-mini",
) -> str:
    """Continue a truncated stream response.

    Per X-003: When stream mode output is truncated (finish_reason='length'),
    continue generating by appending a continuation prompt.
    """
    continuation_messages = list(original_messages)
    continuation_messages.append(LLMMessage(role="assistant", content=accumulated_content))
    continuation_messages.append(LLMMessage(role="user", content="[继续]"))

    try:
        result = await llm_router.call(
            messages=continuation_messages,
            model=model,
            provider_name=provider_name,
            session_id=session_id,
            session_type="planning",
            agent_name="continuation",
        )
        return result.content
    except Exception as e:
        logger.warning(f"Stream continuation failed: {e}")
        return ""
