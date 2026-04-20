"""Coordinator Agent — 任务协调、冲突解决（Leader 角色）"""

from __future__ import annotations

from team_agent.agents.base import BaseAgent
from team_agent.config import AgentConfig, ModelConfig, SkillConfig

DEFAULT_SYSTEM_PROMPT = """你是一个团队协调者（Coordinator/Leader），负责统筹整个 Agent 团队的协作。

你的职责：
- 理解用户需求，制定执行计划
- 将任务拆解并分配给合适的 Agent
- 监控执行进度，动态调整计划
- 仲裁 Agent 间的冲突和分歧
- 汇总结果，向用户汇报

协调原则：
- 合理分配任务，发挥每个 Agent 的专长
- 及时发现阻塞和异常，主动介入
- 监听所有 Agent 间的通信
- 子 Agent 之间只能"请求协作"，不能"命令"
- 只有你能分配任务和做出最终决定

输出格式（规划时）：
1. 需求理解
2. 执行计划（步骤 + 负责 Agent）
3. 预期产出
4. 风险点
"""

DEFAULT_SKILLS = [
    SkillConfig(name="planning", enabled=True),
    SkillConfig(name="task_management", enabled=True),
]


class CoordinatorAgent(BaseAgent):
    """协调者 Agent — 团队的 Leader"""

    def get_role_description(self) -> str:
        return "团队协调者：负责任务规划、分配、监控和冲突仲裁"

    @classmethod
    def create_default(cls, model: str | ModelConfig = "anthropic/claude-sonnet-4-20250514") -> CoordinatorAgent:
        """创建默认配置的 Coordinator Agent（默认使用更强的模型）"""
        if isinstance(model, str):
            model = ModelConfig.from_string(model)

        config = AgentConfig(
            name="coordinator",
            model=model,
            system_prompt=DEFAULT_SYSTEM_PROMPT,
            skills=DEFAULT_SKILLS,
            max_iterations=20,
            description="团队协调者：负责任务规划、分配、监控和冲突仲裁",
        )
        return cls(config=config)
