"""Reviewer Agent — 代码审查、质量检查"""

from __future__ import annotations

from team_agent.agents.base import BaseAgent
from team_agent.config import AgentConfig, ModelConfig, SkillConfig

DEFAULT_SYSTEM_PROMPT = """你是一个资深代码审查专家（Reviewer），擅长代码审查和质量保证。

你的职责：
- 审查代码质量和规范
- 发现潜在 bug 和安全漏洞
- 评估性能和可维护性
- 给出具体的改进建议

审查标准：
- 代码风格：是否符合项目规范
- 正确性：逻辑是否正确，边界情况是否处理
- 安全性：是否有安全隐患（注入、XSS 等）
- 性能：是否有性能瓶颈
- 可维护性：是否易于理解和修改
- 测试：是否有充分的测试覆盖

输出格式：
1. 总结：一句话评价
2. 严重问题（必须修改）
3. 建议改进（推荐修改）
4. 亮点（值得肯定的地方）
"""

DEFAULT_SKILLS = [
    SkillConfig(name="code_review", enabled=True),
    SkillConfig(name="file_read", enabled=True),
    SkillConfig(name="code_execute", enabled=True),
]


class ReviewerAgent(BaseAgent):
    """代码审查 Agent"""

    def get_role_description(self) -> str:
        return "代码审查专家：负责代码审查、质量检查"

    @classmethod
    def create_default(cls, model: str | ModelConfig = "openai/gpt-4o") -> ReviewerAgent:
        """创建默认配置的 Reviewer Agent"""
        if isinstance(model, str):
            model = ModelConfig.from_string(model)

        config = AgentConfig(
            name="reviewer",
            model=model,
            system_prompt=DEFAULT_SYSTEM_PROMPT,
            skills=DEFAULT_SKILLS,
            description="代码审查专家：负责代码审查、质量检查",
        )
        return cls(config=config)
