"""LLM 基类 — 定义统一接口"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass
class LLMMessage:
    """统一的消息格式"""

    role: Role
    content: str
    name: str | None = None  # 工具调用时的名称
    tool_call_id: str | None = None  # 工具调用结果的 ID
    tool_calls: list[dict[str, Any]] | None = None  # 模型请求的工具调用


@dataclass
class LLMUsage:
    """Token 用量统计"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class LLMResponse:
    """统一的响应格式"""

    content: str
    finish_reason: str  # "stop" | "length" | "tool_calls"
    usage: LLMUsage = field(default_factory=LLMUsage)
    model: str = ""
    tool_calls: list[dict[str, Any]] | None = None
    latency_ms: float = 0.0  # 响应耗时


class BaseLLM(ABC):
    """LLM 抽象基类，所有 Provider 实现此接口"""

    def __init__(
        self,
        model_name: str,
        api_key: str | None = None,
        base_url: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        fallback_model: str | None = None,
        **kwargs: Any,
    ):
        self.model_name = model_name
        self.api_key = api_key
        self.base_url = base_url
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.fallback_model = fallback_model
        self.extra_params = kwargs

    @abstractmethod
    async def chat(
        self,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """发送对话请求"""
        ...

    async def chat_with_auto_continue(
        self,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        max_continuations: int = 5,
    ) -> LLMResponse:
        """自动续写 — 检测到 length 截断时自动续写"""
        all_content = ""
        total_usage = LLMUsage()
        all_tool_calls: list[dict[str, Any]] = []
        current_messages = list(messages)
        model = ""
        continue_count = 0

        while True:
            start = time.time()
            response = await self.chat(
                messages=current_messages,
                tools=tools if continue_count == 0 else None,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            elapsed = (time.time() - start) * 1000

            all_content += response.content
            total_usage.prompt_tokens += response.usage.prompt_tokens
            total_usage.completion_tokens += response.usage.completion_tokens
            total_usage.total_tokens += response.usage.total_tokens
            model = response.model

            if response.tool_calls:
                all_tool_calls.extend(response.tool_calls)

            if response.finish_reason != "length":
                break

            continue_count += 1
            if continue_count >= max_continuations:
                break

            # 把已输出内容加入上下文，请求续写
            current_messages.append(LLMMessage(role=Role.ASSISTANT, content=response.content))
            current_messages.append(LLMMessage(role=Role.USER, content="请继续"))

        return LLMResponse(
            content=all_content,
            finish_reason=response.finish_reason if continue_count == 0 else "stop",
            usage=total_usage,
            model=model,
            tool_calls=all_tool_calls if all_tool_calls else None,
            latency_ms=elapsed,
        )

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(model={self.model_name})"
