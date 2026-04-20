"""Skill 管理器 — 加载、注册、管理 Skill"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from team_agent.skills.skill import Skill

logger = logging.getLogger(__name__)


class SkillManager:
    """Skill 管理器 — 自动发现和加载 Markdown Skill 文件"""

    def __init__(self, skills_dir: str | Path = "skills"):
        self.skills_dir = Path(skills_dir)
        self._skills: dict[str, Skill] = {}

    def load_all(self) -> None:
        """加载 skills 目录下所有 .md 文件"""
        if not self.skills_dir.exists():
            logger.info(f"Skills directory not found: {self.skills_dir}")
            return

        for md_file in self.skills_dir.rglob("*.md"):
            try:
                skill = Skill.from_markdown(md_file)
                self._skills[skill.name] = skill
                logger.info(f"Loaded skill: {skill.name} from {md_file}")
            except Exception as e:
                logger.error(f"Failed to load skill from {md_file}: {e}")

    def load_skill(self, path: str | Path) -> Skill:
        """加载单个 Skill 文件"""
        path = Path(path)
        skill = Skill.from_markdown(path)
        self._skills[skill.name] = skill
        return skill

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def get_all(self) -> dict[str, Skill]:
        return dict(self._skills)

    def list_skills(self) -> list[str]:
        return list(self._skills.keys())

    def get_skills_for_agent(self, skill_names: list[str]) -> list[Skill]:
        """获取 Agent 配置的 Skill 列表"""
        skills = []
        for name in skill_names:
            skill = self._skills.get(name)
            if skill:
                skills.append(skill)
            else:
                logger.warning(f"Skill not found: {name}")
        return skills

    def build_skills_prompt(self, skill_names: list[str]) -> str:
        """构建 Agent 的 Skills 提示词"""
        skills = self.get_skills_for_agent(skill_names)
        if not skills:
            return ""
        parts = ["# 你具备以下技能：\n"]
        for skill in skills:
            parts.append(skill.to_system_prompt())
            parts.append("---\n")
        return "\n".join(parts)

    def create_skill_file(self, name: str, description: str = "", tools: list[str] | None = None, content: str = "") -> Path:
        """创建新的 Skill 文件"""
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        path = self.skills_dir / f"{name}.md"

        tools_str = ""
        if tools:
            tools_str = f"tools: [{', '.join(tools)}]"

        fm = "---\n"
        fm += f"name: {name}\n"
        if description:
            fm += f"description: {description}\n"
        if tools_str:
            fm += f"{tools_str}\n"
        fm += "---\n\n"

        path.write_text(fm + content, encoding="utf-8")
        logger.info(f"Created skill file: {path}")
        return path
