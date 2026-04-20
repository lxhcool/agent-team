"""Coder Agent — 代码编写、功能实现"""

from __future__ import annotations

from team_agent.agents.base import BaseAgent
from team_agent.config import AgentConfig, ModelConfig, SkillConfig

DEFAULT_SYSTEM_PROMPT = """你是一个高级程序员（Coder），擅长代码编写和功能实现。

你的职责：
- 根据需求编写高质量代码
- 遵循项目代码规范和最佳实践
- 编写清晰的注释和文档
- 确保代码可维护和可测试

工作原则：
- 代码简洁优雅，不过度设计
- 先理解需求，再动手写代码
- 写完代码后自检一遍
- 遇到不确定的技术选型，主动沟通
"""

DEFAULT_SKILLS = [
    SkillConfig(name="code_execute", enabled=True),
    SkillConfig(name="file_read", enabled=True),
    SkillConfig(name="file_write", enabled=True),
    SkillConfig(name="file_list", enabled=True),
]


class CoderAgent(BaseAgent):
    """程序员 Agent"""

    def get_role_description(self) -> str:
        return "高级程序员：负责代码编写、功能实现"

    @classmethod
    def create_default(cls, model: str | ModelConfig = "openai/gpt-4o") -> CoderAgent:
        """创建默认配置的 Coder Agent"""
        if isinstance(model, str):
            model = ModelConfig.from_string(model)

        config = AgentConfig(
            name="coder",
            model=model,
            system_prompt=DEFAULT_SYSTEM_PROMPT,
            skills=DEFAULT_SKILLS,
            description="高级程序员：负责代码编写、功能实现",
        )
        return cls(config=config)
