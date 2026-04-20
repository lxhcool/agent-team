"""Researcher Agent — 信息搜索、资料收集、知识检索"""

from __future__ import annotations

from team_agent.agents.base import BaseAgent
from team_agent.config import AgentConfig, ModelConfig, SkillConfig

DEFAULT_SYSTEM_PROMPT = """你是一个研究专家（Researcher），擅长信息搜索和知识整理。

你的职责：
- 搜索和收集相关资料
- 整理和总结关键信息
- 为团队提供可靠的知识支撑
- 发现潜在风险和机会

工作原则：
- 信息来源可靠，注明出处
- 客观中立，不夹带个人观点
- 结构化呈现，方便他人理解
- 主动补充遗漏的关键信息
"""

DEFAULT_SKILLS = [
    SkillConfig(name="web_search", enabled=True),
    SkillConfig(name="code_search", enabled=True),
]


class ResearcherAgent(BaseAgent):
    """研究专家 Agent"""

    def get_role_description(self) -> str:
        return "研究专家：负责信息搜索、资料收集、知识检索"

    @classmethod
    def create_default(cls, model: str | ModelConfig = "openai/gpt-4o") -> ResearcherAgent:
        """创建默认配置的 Researcher Agent"""
        if isinstance(model, str):
            model = ModelConfig.from_string(model)

        config = AgentConfig(
            name="researcher",
            model=model,
            system_prompt=DEFAULT_SYSTEM_PROMPT,
            skills=DEFAULT_SKILLS,
            description="研究专家：负责信息搜索、资料收集、知识检索",
        )
        return cls(config=config)
