"""配置管理 — 支持多模型、独立Agent配置"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class ModelConfig(BaseModel):
    """模型配置，格式: provider/model_name"""

    provider: str  # openai, anthropic, ollama, etc.
    model_name: str
    api_key: str | None = None
    base_url: str | None = None
    temperature: float = 0.7
    max_tokens: int = 4096

    @classmethod
    def from_string(cls, s: str, **overrides: Any) -> ModelConfig:
        """从 'provider/model_name' 字符串解析"""
        parts = s.split("/", 1)
        if len(parts) != 2:
            raise ValueError(f"Model config must be 'provider/model_name', got: {s}")
        provider, model_name = parts
        # 自动从环境变量读取 API Key
        env_key_map = {
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
        }
        api_key = overrides.pop("api_key", None) or os.getenv(env_key_map.get(provider, ""))
        base_url = overrides.pop("base_url", None) or os.getenv(f"{provider.upper()}_BASE_URL")
        return cls(
            provider=provider,
            model_name=model_name,
            api_key=api_key,
            base_url=base_url,
            **overrides,
        )


class SkillConfig(BaseModel):
    """Skill 配置"""

    name: str
    enabled: bool = True
    params: dict[str, Any] = Field(default_factory=dict)


class AgentConfig(BaseModel):
    """单个 Agent 的完整配置"""

    name: str
    model: ModelConfig
    system_prompt: str = ""
    skills: list[SkillConfig] = Field(default_factory=list)
    max_iterations: int = 10
    description: str = ""


class ProjectConfig(BaseModel):
    """项目全局配置"""

    project_name: str = "default"
    memory_dir: str = "memory"
    default_model: str = "openai/gpt-4o"
    agents: list[AgentConfig] = Field(default_factory=list)

    @classmethod
    def from_yaml(cls, path: Path) -> ProjectConfig:
        """从 YAML 文件加载配置"""
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return cls._from_dict(data)

    @classmethod
    def _from_dict(cls, data: dict[str, Any]) -> ProjectConfig:
        agents = []
        for a in data.get("agents", []):
            model = ModelConfig.from_string(a["model"]) if isinstance(a.get("model"), str) else a["model"]
            skills = [SkillConfig(**s) if isinstance(s, dict) else SkillConfig(name=s) for s in a.get("skills", [])]
            agents.append(
                AgentConfig(
                    name=a["name"],
                    model=model,
                    system_prompt=a.get("system_prompt", ""),
                    skills=skills,
                    max_iterations=a.get("max_iterations", 10),
                    description=a.get("description", ""),
                )
            )
        return ProjectConfig(
            project_name=data.get("project_name", "default"),
            memory_dir=data.get("memory_dir", "memory"),
            default_model=data.get("default_model", "openai/gpt-4o"),
            agents=agents,
        )

    def to_yaml(self, path: Path) -> None:
        """保存配置到 YAML"""
        data = self.model_dump()
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False)
