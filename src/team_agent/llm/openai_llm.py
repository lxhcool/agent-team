"""OpenAI LLM 实现"""

from __future__ import annotations

import time
from typing import Any

from team_agent.llm.base import BaseLLM, LLMMessage, LLMResponse, LLMUsage, Role
from team_agent.llm.registry import register_provider


@register_provider("openai")
class OpenAILLM(BaseLLM):
    """OpenAI 兼容的 LLM 实现（支持 OpenAI、Azure、各种兼容 API）"""

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)
        self._client = None

    def _get_client(self):
        """懒加载 OpenAI 客户端"""
        if self._client is not None:
            return self._client

        try:
            from openai import AsyncOpenAI
        except ImportError:
            raise ImportError("openai package is required: pip install openai")

        client_kwargs: dict[str, Any] = {}
        if self.api_key:
            client_kwargs["api_key"] = self.api_key
        if self.base_url:
            client_kwargs["base_url"] = self.base_url

        self._client = AsyncOpenAI(**client_kwargs)
        return self._client

    def _convert_messages(self, messages: list[LLMMessage]) -> list[dict[str, Any]]:
        """将统一消息格式转换为 OpenAI 格式"""
        result = []
        for msg in messages:
            d: dict[str, Any] = {"role": msg.role.value, "content": msg.content}
            if msg.name:
                d["name"] = msg.name
            if msg.tool_call_id:
                d["tool_call_id"] = msg.tool_call_id
            if msg.tool_calls:
                d["tool_calls"] = msg.tool_calls
                d["content"] = d["content"] or None
            result.append(d)
        return result

    async def chat(
        self,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        client = self._get_client()
        start = time.time()

        kwargs: dict[str, Any] = {
            "model": self.model_name,
            "messages": self._convert_messages(messages),
            "temperature": temperature or self.temperature,
            "max_tokens": max_tokens or self.max_tokens,
        }
        if tools:
            kwargs["tools"] = tools

        response = await client.chat.completions.create(**kwargs)
        elapsed = (time.time() - start) * 1000

        choice = response.choices[0]
        message = choice.message

        usage = LLMUsage(
            prompt_tokens=response.usage.prompt_tokens if response.usage else 0,
            completion_tokens=response.usage.completion_tokens if response.usage else 0,
            total_tokens=response.usage.total_tokens if response.usage else 0,
        )

        tool_calls = None
        if message.tool_calls:
            tool_calls = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in message.tool_calls
            ]

        return LLMResponse(
            content=message.content or "",
            finish_reason=choice.finish_reason,
            usage=usage,
            model=response.model,
            tool_calls=tool_calls,
            latency_ms=elapsed,
        )
