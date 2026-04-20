"""工具注册表 — 管理 Agent 可调用的底层工具"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ToolDefinition:
    """工具定义"""

    name: str
    description: str
    func: Callable[..., Any]
    parameters: dict[str, Any] = field(default_factory=dict)  # JSON Schema 格式

    def to_openai_schema(self) -> dict[str, Any]:
        """转换为 OpenAI function calling 格式"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """工具注册表"""

    def __init__(self):
        self._tools: dict[str, ToolDefinition] = {}

    def register(
        self,
        name: str | None = None,
        description: str | None = None,
        parameters: dict[str, Any] | None = None,
    ) -> Callable:
        """装饰器：注册工具"""

        def decorator(func: Callable) -> Callable:
            tool_name = name or func.__name__
            tool_desc = description or func.__doc__ or ""
            tool_params = parameters or self._infer_parameters(func)
            self._tools[tool_name] = ToolDefinition(
                name=tool_name,
                description=tool_desc.strip(),
                func=func,
                parameters=tool_params,
            )
            return func

        return decorator

    def add_tool(self, func: Callable, name: str | None = None, description: str | None = None) -> None:
        """直接添加工具"""
        tool_name = name or func.__name__
        tool_desc = description or func.__doc__ or ""
        tool_params = self._infer_parameters(func)
        self._tools[tool_name] = ToolDefinition(
            name=tool_name,
            description=tool_desc.strip(),
            func=func,
            parameters=tool_params,
        )

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def get_all(self) -> dict[str, ToolDefinition]:
        return dict(self._tools)

    def list_tools(self) -> list[str]:
        return list(self._tools.keys())

    def get_schemas(self, tool_names: list[str] | None = None) -> list[dict[str, Any]]:
        """获取工具的 OpenAI schema 列表"""
        if tool_names:
            return [self._tools[n].to_openai_schema() for n in tool_names if n in self._tools]
        return [t.to_openai_schema() for t in self._tools.values()]

    def _infer_parameters(self, func: Callable) -> dict[str, Any]:
        """从函数签名推断参数 JSON Schema"""
        sig = inspect.signature(func)
        properties: dict[str, Any] = {}
        required: list[str] = []

        type_map = {
            str: "string",
            int: "integer",
            float: "number",
            bool: "boolean",
            list: "array",
            dict: "object",
        }

        for param_name, param in sig.parameters.items():
            if param_name in ("self", "cls"):
                continue

            prop: dict[str, Any] = {}
            if param.annotation != inspect.Parameter.empty:
                prop["type"] = type_map.get(param.annotation, "string")
            else:
                prop["type"] = "string"

            if param.default == inspect.Parameter.empty:
                required.append(param_name)

            properties[param_name] = prop

        schema: dict[str, Any] = {
            "type": "object",
            "properties": properties,
        }
        if required:
            schema["required"] = required

        return schema


# 全局工具注册表
_global_registry = ToolRegistry()


def tool(
    name: str | None = None,
    description: str | None = None,
    parameters: dict[str, Any] | None = None,
) -> Callable:
    """便捷装饰器 — 注册到全局工具注册表"""
    return _global_registry.register(name=name, description=description, parameters=parameters)


def get_global_registry() -> ToolRegistry:
    return _global_registry
