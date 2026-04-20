"""LLM Provider 注册表"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from team_agent.llm.base import BaseLLM

_REGISTRY: dict[str, type[BaseLLM]] = {}


def register_provider(name: str):
    """装饰器：注册 LLM Provider"""

    def decorator(cls: type[BaseLLM]) -> type[BaseLLM]:
        _REGISTRY[name.lower()] = cls
        return cls

    return decorator


def get_provider(name: str) -> type[BaseLLM]:
    """获取已注册的 Provider 类"""
    name = name.lower()
    if name not in _REGISTRY:
        available = ", ".join(_REGISTRY.keys()) or "none"
        raise ValueError(f"Unknown LLM provider: {name}. Available: {available}")
    return _REGISTRY[name]


def list_providers() -> list[str]:
    """列出所有已注册的 Provider"""
    return list(_REGISTRY.keys())


class LLMRegistry:
    """LLM 注册表 — 管理所有已注册的 Provider"""

    @staticmethod
    def register(name: str, cls: type[BaseLLM]) -> None:
        _REGISTRY[name.lower()] = cls

    @staticmethod
    def get(name: str) -> type[BaseLLM]:
        return get_provider(name)

    @staticmethod
    def list_all() -> list[str]:
        return list_providers()
