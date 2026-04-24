"""SkillRegistry - Runtime service for Skill discovery, registration, and matching.

Per requirements: Skills need a runtime registry so Agents can dynamically
discover and use available skills during planning and execution.

Key responsibilities:
- Discover and load skills from DB
- Match skills to agent capabilities and task requirements
- Provide skill content for agent prompts
- Validate skill compatibility with agent tools/constraints
"""

import json
import logging
from typing import Dict, List, Optional, Set

from sqlalchemy import select

from app.core.database import async_session
from app.models.models import Skill

logger = logging.getLogger(__name__)


class SkillInfo:
    """Runtime representation of a skill."""
    __slots__ = ("id", "name", "display_name", "description", "version",
                 "source_type", "tools", "recommended_for", "output_format",
                 "content")

    def __init__(self, skill: Skill):
        self.id = skill.id
        self.name = skill.name
        self.display_name = skill.display_name
        self.description = skill.description or ""
        self.version = skill.version
        self.source_type = skill.source_type
        self.tools: List[str] = json.loads(skill.tools_json) if skill.tools_json else []
        self.recommended_for: List[str] = json.loads(skill.recommended_for_json) if skill.recommended_for_json else []
        self.output_format = skill.output_format
        self.content = skill.content


class SkillRegistry:
    """Runtime skill registry for dynamic skill discovery and matching."""

    def __init__(self):
        self._skills: Dict[str, SkillInfo] = {}  # name -> SkillInfo
        self._loaded = False

    async def load_skills(self):
        """Load all skills from database into memory."""
        async with async_session() as db:
            result = await db.execute(select(Skill))
            skills = result.scalars().all()
            self._skills.clear()
            for s in skills:
                self._skills[s.name] = SkillInfo(s)
            self._loaded = True
            logger.info(f"SkillRegistry loaded {len(self._skills)} skills")

    async def reload_skills(self):
        """Reload skills from database."""
        await self.load_skills()

    def get_skill(self, name: str) -> Optional[SkillInfo]:
        """Get a skill by name."""
        if not self._loaded:
            logger.warning("SkillRegistry not loaded, call load_skills() first")
        return self._skills.get(name)

    def list_skills(self) -> List[SkillInfo]:
        """List all registered skills."""
        if not self._loaded:
            logger.warning("SkillRegistry not loaded")
        return list(self._skills.values())

    def find_skills_for_agent(
        self,
        agent_role: str,
        agent_capabilities: Optional[List[str]] = None,
        agent_tools: Optional[List[str]] = None,
    ) -> List[SkillInfo]:
        """Find skills recommended for an agent based on role and capabilities.

        Args:
            agent_role: The agent's role (e.g., 'architect', 'developer')
            agent_capabilities: List of agent capability names
            agent_tools: List of tool names the agent has access to

        Returns:
            List of matching SkillInfo objects
        """
        if not self._loaded:
            return []

        results = []
        for skill in self._skills.values():
            # Check if skill is recommended for this role
            if skill.recommended_for:
                if agent_role in skill.recommended_for:
                    results.append(skill)
                    continue

            # Check if skill tools are a subset of agent tools
            if agent_tools and skill.tools:
                if set(skill.tools).issubset(set(agent_tools)):
                    results.append(skill)
                    continue

            # Check if skill is general-purpose (no specific recommendation)
            if not skill.recommended_for and not skill.tools:
                results.append(skill)

        return results

    def find_skills_for_task(
        self,
        task_description: str,
        required_tools: Optional[List[str]] = None,
    ) -> List[SkillInfo]:
        """Find skills relevant to a task based on description and required tools.

        Args:
            task_description: The task description text
            required_tools: List of tools needed for the task

        Returns:
            List of matching SkillInfo objects
        """
        if not self._loaded:
            return []

        results = []
        desc_lower = task_description.lower()

        for skill in self._skills.values():
            # Match by required tools
            if required_tools and skill.tools:
                if any(t in skill.tools for t in required_tools):
                    results.append(skill)
                    continue

            # Match by description keywords
            if skill.description:
                skill_keywords = set(skill.description.lower().split())
                desc_keywords = set(desc_lower.split())
                overlap = skill_keywords & desc_keywords
                if len(overlap) >= 2:  # At least 2 keyword overlap
                    results.append(skill)
                    continue

            # Match by recommended_for in description
            if skill.recommended_for:
                for rec in skill.recommended_for:
                    if rec.lower() in desc_lower:
                        results.append(skill)
                        break

        return results

    def get_skill_prompt(self, name: str) -> Optional[str]:
        """Get the skill's content/prompt for injection into agent system message.

        Args:
            name: Skill name

        Returns:
            The skill content string, or None if not found
        """
        skill = self.get_skill(name)
        if not skill:
            return None
        if skill.content:
            return skill.content

        # Generate a basic prompt from metadata
        parts = [f"## Skill: {skill.display_name}"]
        if skill.description:
            parts.append(f"\n{skill.description}")
        if skill.tools:
            parts.append(f"\nRequired tools: {', '.join(skill.tools)}")
        if skill.output_format:
            parts.append(f"\nOutput format: {skill.output_format}")
        return "\n".join(parts)

    def validate_skill_for_agent(
        self,
        skill_name: str,
        agent_tools: Optional[List[str]] = None,
        agent_constraints: Optional[List[str]] = None,
    ) -> tuple:
        """Validate if a skill can be used by an agent.

        Returns:
            (valid, reason) tuple
        """
        skill = self.get_skill(skill_name)
        if not skill:
            return False, f"Skill '{skill_name}' not found"

        # Check if all required tools are available
        if skill.tools and agent_tools:
            missing = set(skill.tools) - set(agent_tools)
            if missing:
                return False, f"Missing required tools: {missing}"

        # Check against agent constraints
        if agent_constraints:
            for constraint in agent_constraints:
                constraint_lower = constraint.lower()
                if "no" in constraint_lower and skill.source_type == "imported":
                    if "external" in constraint_lower or "import" in constraint_lower:
                        return False, f"Agent constraint forbids imported skills: {constraint}"

        return True, ""


# Global singleton
skill_registry = SkillRegistry()
