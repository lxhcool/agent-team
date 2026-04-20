"""LLM 工厂 — 根据配置创建 LLM 实例"""

from __future__ import annotations

from typing import Any

from team_agent.config import ModelConfig
from team_agent.llm.base import BaseLLM
from team_agent.llm.registry import get_provider


def create_llm(config: ModelConfig | dict[str, Any] | str, **overrides: Any) -> BaseLLM:
    """根据配置创建 LLM 实例

    Args:
        config: 模型配置，支持以下格式：
            - ModelConfig 对象
            - "provider/model_name" 字符串
            - dict 配置字典
        **overrides: 覆盖配置参数

    Returns:
        BaseLLM 实例
    """
    if isinstance(config, str):
        config = ModelConfig.from_string(config, **overrides)
    elif isinstance(config, dict):
        if "model" in config and "provider" not in config:
            config = ModelConfig.from_string(config["model"], **{k: v for k, v in config.items() if k != "model"})
        else:
            config = ModelConfig(**config)
    elif not isinstance(config, ModelConfig):
        raise TypeError(f"Unsupported config type: {type(config)}")

    provider_cls = get_provider(config.provider)

    return provider_cls(
        model_name=config.model_name,
        api_key=config.api_key,
        base_url=config.base_url,
        temperature=config.temperature,
        max_tokens=config.max_tokens,
    )
