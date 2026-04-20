"""Planner — LLM 驱动的任务规划器"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from team_agent.llm.base import BaseLLM, LLMMessage, Role
from team_agent.llm.factory import create_llm
from team_agent.config import ModelConfig

logger = logging.getLogger(__name__)

PLANNING_PROMPT = """你是一个任务规划专家。请根据用户需求，制定一个详细的执行计划。

可用的 Agent 角色：
{agent_roles}

请输出 JSON 格式的计划：
```json
{{
  "understanding": "对需求的理解",
  "steps": [
    {{
      "id": "step_1",
      "description": "步骤描述",
      "agent": "负责的 Agent 名称",
      "dependencies": [],
      "expected_output": "预期产出"
    }}
  ],
  "risks": ["可能的风险点"],
  "estimated_iterations": 5
}}
```

只输出 JSON，不要其他内容。
"""


@dataclass
class PlanStep:
    """计划步骤"""

    id: str
    description: str
    agent: str
    dependencies: list[str] = field(default_factory=list)
    expected_output: str = ""
    status: str = "pending"  # pending | running | completed | failed


@dataclass
class Plan:
    """执行计划"""

    understanding: str
    steps: list[PlanStep] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    estimated_iterations: int = 5
    raw_response: str = ""


class Planner:
    """LLM 驱动的任务规划器"""

    def __init__(self, llm: BaseLLM | ModelConfig | str = "openai/gpt-4o"):
        if isinstance(llm, (str, ModelConfig)):
            self.llm = create_llm(llm)
        else:
            self.llm = llm

    async def plan(self, task: str, agent_roles: dict[str, str]) -> Plan:
        """根据任务和可用角色，生成执行计划"""
        # 构建角色描述
        roles_text = "\n".join([f"- {name}: {desc}" for name, desc in agent_roles.items()])

        prompt = PLANNING_PROMPT.format(agent_roles=roles_text)

        messages = [
            LLMMessage(role=Role.SYSTEM, content=prompt),
            LLMMessage(role=Role.USER, content=task),
        ]

        response = await self.llm.chat_with_auto_continue(messages=messages, temperature=0.3)

        # 解析 JSON
        plan = self._parse_plan(response.content)
        plan.raw_response = response.content
        return plan

    def _parse_plan(self, content: str) -> Plan:
        """解析 LLM 输出的计划 JSON"""
        # 尝试提取 JSON
        json_str = content
        if "```json" in content:
            json_str = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            json_str = content.split("```")[1].split("```")[0]

        try:
            data = json.loads(json_str.strip())
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse plan JSON: {e}")
            return Plan(understanding="Failed to parse plan", steps=[], risks=["Plan parsing failed"])

        steps = []
        for s in data.get("steps", []):
            steps.append(PlanStep(
                id=s.get("id", f"step_{len(steps) + 1}"),
                description=s.get("description", ""),
                agent=s.get("agent", ""),
                dependencies=s.get("dependencies", []),
                expected_output=s.get("expected_output", ""),
            ))

        return Plan(
            understanding=data.get("understanding", ""),
            steps=steps,
            risks=data.get("risks", []),
            estimated_iterations=data.get("estimated_iterations", 5),
        )

    async def replan(self, task: str, agent_roles: dict[str, str], completed_steps: list[PlanStep], failed_step: PlanStep | None = None) -> Plan:
        """基于已完成步骤重新规划"""
        context = f"原始任务: {task}\n\n"
        context += "已完成步骤:\n"
        for step in completed_steps:
            context += f"- {step.id}: {step.description} (由 {step.agent} 完成)\n"
        if failed_step:
            context += f"\n失败步骤: {failed_step.id}: {failed_step.description}\n"
            context += "请调整计划，跳过或替换失败步骤。\n"

        return await self.plan(context, agent_roles)
