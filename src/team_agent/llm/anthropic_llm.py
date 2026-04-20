"""Anthropic Claude LLM 实现"""

from __future__ import annotations

import time
from typing import Any

from team_agent.llm.base import BaseLLM, LLMMessage, LLMResponse, LLMUsage, Role
from team_agent.llm.registry import register_provider


@register_provider("anthropic")
class AnthropicLLM(BaseLLM):
    """Anthropic Claude LLM 实现"""

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)
        self._client = None

    def _get_client(self):
        """懒加载 Anthropic 客户端"""
        if self._client is not None:
            return self._client

        try:
            from anthropic import AsyncAnthropic
        except ImportError:
            raise ImportError("anthropic package is required: pip install anthropic")

        client_kwargs: dict[str, Any] = {}
        if self.api_key:
            client_kwargs["api_key"] = self.api_key
        if self.base_url:
            client_kwargs["base_url"] = self.base_url

        self._client = AsyncAnthropic(**client_kwargs)
        return self._client

    def _convert_messages(self, messages: list[LLMMessage]) -> tuple[str | None, list[dict[str, Any]]]:
        """将统一消息格式转换为 Anthropic 格式，返回 (system, messages)"""
        system = None
        converted = []

        for msg in messages:
            if msg.role == Role.SYSTEM:
                system = msg.content
                continue

            d: dict[str, Any] = {"role": msg.role.value, "content": msg.content}

            if msg.role == Role.ASSISTANT and msg.tool_calls:
                content_blocks: list[dict[str, Any]] = []
                if msg.content:
                    content_blocks.append({"type": "text", "text": msg.content})
                for tc in msg.tool_calls:
                    func = tc.get("function", {})
                    content_blocks.append({
                        "type": "tool_use",
                        "id": tc.get("id", ""),
                        "name": func.get("name", ""),
                        "input": func.get("arguments", {}),
                    })
                d["content"] = content_blocks

            if msg.role == Role.TOOL and msg.tool_call_id:
                d = {
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": msg.tool_call_id,
                        "content": msg.content,
                    }],
                }

            converted.append(d)

        return system, converted

    def _convert_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """将 OpenAI 格式的 tools 转换为 Anthropic 格式"""
        anthropic_tools = []
        for tool in tools:
            func = tool.get("function", {})
            anthropic_tools.append({
                "name": func.get("name", ""),
                "description": func.get("description", ""),
                "input_schema": func.get("parameters", {"type": "object", "properties": {}}),
            })
        return anthropic_tools

    async def chat(
        self,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        client = self._get_client()
        start = time.time()

        system, converted_messages = self._convert_messages(messages)

        kwargs: dict[str, Any] = {
            "model": self.model_name,
            "messages": converted_messages,
            "temperature": temperature or self.temperature,
            "max_tokens": max_tokens or self.max_tokens,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = self._convert_tools(tools)

        response = await client.messages.create(**kwargs)
        elapsed = (time.time() - start) * 1000

        # 解析响应
        content_text = ""
        tool_calls = []

        for block in response.content:
            if block.type == "text":
                content_text += block.text
            elif block.type == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "type": "function",
                    "function": {
                        "name": block.name,
                        "arguments": block.input,
                    },
                })

        finish_reason = "stop"
        if response.stop_reason == "max_tokens":
            finish_reason = "length"
        elif response.stop_reason == "tool_use":
            finish_reason = "tool_calls"

        usage = LLMUsage(
            prompt_tokens=response.usage.input_tokens,
            completion_tokens=response.usage.output_tokens,
            total_tokens=response.usage.input_tokens + response.usage.output_tokens,
        )

        return LLMResponse(
            content=content_text,
            finish_reason=finish_reason,
            usage=usage,
            model=response.model,
            tool_calls=tool_calls if tool_calls else None,
            latency_ms=elapsed,
        )
