"""Artifact Service - manages proposal and planning summary artifacts.

Core objects:
- ProposalRenderer: renders agent discussion results into structured proposal.md
- PlanningSummarySerializer: serializes internal tasks into a handoff-ready summary
- ArtifactService: orchestrates creation, storage, and retrieval of artifacts
"""

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import async_session
from app.models.models import Artifact, Message, MessageType, PlanningSession, Task

logger = logging.getLogger(__name__)


class ProposalRenderer:
    """Renders a structured proposal.md from Planning Session data."""

    @staticmethod
    def render(
        session: PlanningSession,
        proposal_content: str,
        analysis_content: Optional[str] = None,
    ) -> str:
        """Render a proposal.md document.

        Args:
            session: The PlanningSession ORM object.
            proposal_content: The raw proposal markdown from the agent.
            analysis_content: Optional analysis markdown from the agent.

        Returns:
            Complete proposal.md string.
        """
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        parts = [
            f"# {session.title}",
            "",
            f"> proposal_id: `{session.id}`  ",
            f"> session_id: `{session.id}`  ",
            f"> status: {session.status.value}  ",
            f"> generated_at: {now}  ",
            f"> generated_by: Leader + Agent Team  ",
            "",
            "---",
            "",
        ]

        if session.input_text:
            parts.extend([
                "## 背景",
                "",
                session.input_text.strip(),
                "",
            ])

        if analysis_content:
            parts.extend([
                "## 需求分析",
                "",
                analysis_content.strip(),
                "",
            ])

        if session.summary:
            parts.extend([
                "## 分析摘要",
                "",
                session.summary.strip(),
                "",
            ])

        parts.extend([
            "## 技术方案",
            "",
            proposal_content.strip(),
            "",
        ])

        parts.extend([
            "---",
            "",
            f"*本文档由 Team Agent 系统自动生成于 {now}*",
        ])

        return "\n".join(parts)


class PlanningSummarySerializer:
    """Serializes Task records into a handoff-ready planning summary."""

    @staticmethod
    def serialize(
        session: PlanningSession,
        tasks: List[Task],
        proposal_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Serialize tasks into a planning summary JSON structure.

        Args:
            session: The PlanningSession ORM object.
            tasks: List of Task ORM objects.
            proposal_id: Optional proposal artifact ID.

        Returns:
            Dict representing the planning summary content.
        """
        now = datetime.now(timezone.utc).isoformat()

        # Collect all target paths and validation commands across tasks
        all_target_paths = set()
        all_validation_commands = []

        task_list = []
        for i, t in enumerate(tasks):
            deps = json.loads(t.dependencies_json) if t.dependencies_json else []
            target_paths = json.loads(t.target_paths_json) if t.target_paths_json else []
            val_cmds = json.loads(t.validation_commands_json) if t.validation_commands_json else []

            for p in target_paths:
                all_target_paths.add(p)
            all_validation_commands.extend(val_cmds)

            task_list.append({
                "task_id": t.id,
                "title": t.title,
                "description": t.description or "",
                "owner_role": t.owner_role or t.assigned_agent or "collaborator",
                "scope": target_paths,
                "acceptance_notes": val_cmds,
                "dependencies": deps,
            })

        plan = {
            "summary_id": f"summary_{session.id}",
            "source_session_id": session.id,
            "proposal_id": proposal_id or session.id,
            "title": session.title,
            "goal": session.input_text[:200] if session.input_text else "",
            "summary": session.summary or "",
            "handoff_items": task_list,
            "scope_paths": sorted(all_target_paths),
            "acceptance_notes": all_validation_commands,
            "expected_artifacts": ["proposal.md", "planning_summary.json"],
            "metadata": {
                "created_at": now,
                "version": "1.0.0",
                "generated_by": "Team Agent",
            },
        }

        return plan


class ArtifactService:
    """Manages artifact creation, storage, and retrieval."""

    def __init__(self):
        self.artifacts_dir = settings.artifacts_dir

    def _ensure_dir(self, session_id: str) -> Path:
        """Ensure the artifact directory exists for a session."""
        dir_path = self.artifacts_dir / session_id
        dir_path.mkdir(parents=True, exist_ok=True)
        return dir_path

    def _compute_checksum(self, content: Union[str, bytes]) -> str:
        """Compute SHA-256 checksum of content."""
        if isinstance(content, str):
            content = content.encode("utf-8")
        return hashlib.sha256(content).hexdigest()

    async def create_proposal_artifact(
        self,
        session_id: str,
        content: str,
        created_by: str = "leader",
    ) -> Artifact:
        """Create a proposal.md artifact for a session.

        Args:
            session_id: The planning session ID.
            content: The full proposal.md content.
            created_by: Agent name that created the proposal.

        Returns:
            The Artifact ORM object (saved to DB).
        """
        dir_path = self._ensure_dir(session_id)
        filename = "proposal.md"
        file_path = dir_path / filename

        # Write file
        file_path.write_text(content, encoding="utf-8")
        size_bytes = file_path.stat().st_size
        checksum = self._compute_checksum(content)

        # Create or update artifact record
        async with async_session() as db:
            # Check if artifact already exists
            result = await db.execute(
                select(Artifact).where(
                    Artifact.session_id == session_id,
                    Artifact.artifact_type == "proposal",
                )
            )
            existing = result.scalars().first()

            if existing:
                existing.filename = filename
                existing.path = str(file_path)
                existing.size_bytes = size_bytes
                existing.checksum = checksum
                existing.source = "generated"
                existing.created_by = created_by
                await db.commit()
                await db.refresh(existing)
                return existing

            artifact = Artifact(
                session_type="planning",
                session_id=session_id,
                artifact_type="proposal",
                filename=filename,
                path=str(file_path),
                mime_type="text/markdown",
                size_bytes=size_bytes,
                checksum=checksum,
                source="generated",
                created_by=created_by,
            )
            db.add(artifact)
            await db.commit()
            await db.refresh(artifact)
            return artifact

    async def create_execution_plan_artifact(
        self,
        session_id: str,
        plan_data: Dict[str, Any],
        created_by: str = "leader",
    ) -> Artifact:
        """Create a planning_summary.json artifact for a session.

        Args:
            session_id: The planning session ID.
            plan_data: The planning summary dict.
            created_by: Agent name that created the summary.

        Returns:
            The Artifact ORM object (saved to DB).
        """
        dir_path = self._ensure_dir(session_id)
        filename = "planning_summary.json"
        file_path = dir_path / filename

        content = json.dumps(plan_data, ensure_ascii=False, indent=2)

        # Write file
        file_path.write_text(content, encoding="utf-8")
        size_bytes = file_path.stat().st_size
        checksum = self._compute_checksum(content)

        # Create or update artifact record
        async with async_session() as db:
            result = await db.execute(
                select(Artifact).where(
                    Artifact.session_id == session_id,
                    Artifact.artifact_type == "planning_summary",
                )
            )
            existing = result.scalars().first()

            if existing:
                existing.filename = filename
                existing.path = str(file_path)
                existing.size_bytes = size_bytes
                existing.checksum = checksum
                existing.source = "generated"
                existing.created_by = created_by
                await db.commit()
                await db.refresh(existing)
                return existing

            artifact = Artifact(
                session_type="planning",
                session_id=session_id,
                artifact_type="planning_summary",
                filename=filename,
                path=str(file_path),
                mime_type="application/json",
                size_bytes=size_bytes,
                checksum=checksum,
                source="generated",
                created_by=created_by,
            )
            db.add(artifact)
            await db.commit()
            await db.refresh(artifact)
            return artifact

    async def generate_proposal(self, session_id: str) -> Optional[Artifact]:
        """Generate a proposal.md artifact from the session's data.

        Reads the proposal and analysis messages from the DB, renders them
        into a structured proposal.md, and saves it as an artifact.

        Args:
            session_id: The planning session ID.

        Returns:
            The created Artifact, or None if session has no proposal.
        """
        async with async_session() as db:
            session = await db.get(PlanningSession, session_id)
            if not session:
                return None

            # Get proposal message
            result = await db.execute(
                select(Message).where(
                    Message.session_id == session_id,
                    Message.message_type == MessageType.PROPOSAL,
                ).order_by(Message.seq.desc())
            )
            proposal_msg = result.scalars().first()
            if not proposal_msg:
                return None

            # Get analysis message (optional)
            analysis_result = await db.execute(
                select(Message).where(
                    Message.session_id == session_id,
                    Message.message_type == MessageType.CHAT,
                    Message.category == "analysis",
                ).order_by(Message.seq.desc())
            )
            analysis_msg = analysis_result.scalars().first()
            analysis_content = analysis_msg.content if analysis_msg else None

        # Render the proposal
        proposal_md = ProposalRenderer.render(
            session=session,
            proposal_content=proposal_msg.content,
            analysis_content=analysis_content,
        )

        return await self.create_proposal_artifact(
            session_id=session_id,
            content=proposal_md,
            created_by="leader",
        )

    async def generate_execution_plan(self, session_id: str) -> Optional[Artifact]:
        """Generate a planning summary artifact from the session's tasks.

        Args:
            session_id: The planning session ID.

        Returns:
            The created Artifact, or None if session has no tasks.
        """
        async with async_session() as db:
            session = await db.get(PlanningSession, session_id)
            if not session:
                return None

            # Get tasks
            result = await db.execute(
                select(Task).where(
                    Task.session_id == session_id,
                ).order_by(Task.order.asc())
            )
            tasks = result.scalars().all()
            if not tasks:
                return None

            # Get proposal artifact ID if exists
            proposal_result = await db.execute(
                select(Artifact).where(
                    Artifact.session_id == session_id,
                    Artifact.artifact_type == "proposal",
                )
            )
            proposal_artifact = proposal_result.scalars().first()
            proposal_id = proposal_artifact.id if proposal_artifact else None

        # Serialize
        plan_data = PlanningSummarySerializer.serialize(
            session=session,
            tasks=list(tasks),
            proposal_id=proposal_id,
        )

        return await self.create_execution_plan_artifact(
            session_id=session_id,
            plan_data=plan_data,
            created_by="leader",
        )

    async def get_proposal_content(self, session_id: str) -> Optional[str]:
        """Read the proposal.md content for a session.

        Args:
            session_id: The planning session ID.

        Returns:
            The proposal.md content string, or None.
        """
        async with async_session() as db:
            result = await db.execute(
                select(Artifact).where(
                    Artifact.session_id == session_id,
                    Artifact.artifact_type == "proposal",
                )
            )
            artifact = result.scalars().first()
            if not artifact:
                return None

        file_path = Path(artifact.path)
        if not file_path.exists():
            return None

        return file_path.read_text(encoding="utf-8")

    async def get_execution_plan(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Read the planning summary content for a session.

        Args:
            session_id: The planning session ID.

        Returns:
            The planning summary dict, or None.
        """
        async with async_session() as db:
            result = await db.execute(
                select(Artifact).where(
                    Artifact.session_id == session_id,
                    Artifact.artifact_type == "planning_summary",
                )
            )
            artifact = result.scalars().first()
            if not artifact:
                return None

        file_path = Path(artifact.path)
        if not file_path.exists():
            return None

        return json.loads(file_path.read_text(encoding="utf-8"))

    async def get_artifacts_for_session(self, session_id: str) -> List[Artifact]:
        """Get all artifacts for a session.

        Args:
            session_id: The planning session ID.

        Returns:
            List of Artifact ORM objects.
        """
        async with async_session() as db:
            result = await db.execute(
                select(Artifact).where(
                    Artifact.session_id == session_id,
                ).order_by(Artifact.created_at.asc())
            )
            return result.scalars().all()


# Global singleton
artifact_service = ArtifactService()
