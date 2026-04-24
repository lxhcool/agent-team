"""Memory Service - Layered memory management per requirements (M-006).

Implements the memory write strategy from requirements:
- Planning Session: preserve goals, constraints, conclusions, final proposal (not all intermediate noise)
- Roundtable Session: only preserve per-round summaries and final conclusions
- Execution Session: preserve execution results, validation results, artifact refs, reusable experience
- Failure cases: preserve failure reasons and fix suggestions

Memory layers:
- Layer 1: Markdown (read-only human conventions and role files)
- Layer 2: Database (sessions, tasks, messages, logs, summaries) - THIS SERVICE
- Layer 3: Vector retrieval (future, not MVP)
"""

import json
import logging
from typing import List, Optional

from sqlalchemy import select, func

from app.core.database import async_session
from app.models.models import MemoryEntry

logger = logging.getLogger(__name__)


class MemoryService:
    """Service for writing and querying structured memory entries."""

    async def write(
        self,
        session_type: str,
        session_id: Optional[str],
        entry_type: str,
        content: str,
        category: Optional[str] = None,
        metadata: Optional[dict] = None,
        retention_policy: str = "session",
    ) -> MemoryEntry:
        """Write a memory entry.

        Args:
            session_type: "planning", "execution", "roundtable"
            session_id: Session ID (optional for cross-session memories)
            entry_type: "conclusion", "summary", "experience", "failure"
            content: The memory content text
            category: Optional sub-category
            metadata: Optional JSON-serializable metadata
            retention_policy: "session", "permanent", "ttl"
        """
        async with async_session() as db:
            entry = MemoryEntry(
                session_type=session_type,
                session_id=session_id,
                entry_type=entry_type,
                category=category,
                content=content,
                metadata_json=json.dumps(metadata) if metadata else None,
                retention_policy=retention_policy,
            )
            db.add(entry)
            await db.commit()
            await db.refresh(entry)
            return entry

    async def write_planning_summary(
        self,
        session_id: str,
        goals: str,
        constraints: Optional[str] = None,
        conclusion: Optional[str] = None,
        proposal_summary: Optional[str] = None,
    ):
        """Write structured planning session memory per requirements.

        Preserves: goals, constraints, conclusions, final proposal.
        Does NOT preserve all intermediate noise.
        """
        if goals:
            await self.write(
                session_type="planning",
                session_id=session_id,
                entry_type="conclusion",
                content=goals,
                category="goals",
                retention_policy="permanent",
            )
        if constraints:
            await self.write(
                session_type="planning",
                session_id=session_id,
                entry_type="conclusion",
                content=constraints,
                category="constraints",
                retention_policy="permanent",
            )
        if conclusion:
            await self.write(
                session_type="planning",
                session_id=session_id,
                entry_type="conclusion",
                content=conclusion,
                category="conclusion",
                retention_policy="permanent",
            )
        if proposal_summary:
            await self.write(
                session_type="planning",
                session_id=session_id,
                entry_type="summary",
                content=proposal_summary,
                category="proposal",
                retention_policy="permanent",
            )

    async def write_roundtable_summary(
        self,
        session_id: str,
        round_number: int,
        round_summary: str,
        final_conclusion: Optional[str] = None,
    ):
        """Write roundtable memory per requirements.

        Only preserves per-round summaries and final conclusions,
        NOT complete divergent text as long-term memory.
        """
        await self.write(
            session_type="roundtable",
            session_id=session_id,
            entry_type="summary",
            content=round_summary,
            category=f"round_{round_number}",
            retention_policy="session",
            metadata={"round_number": round_number},
        )
        if final_conclusion:
            await self.write(
                session_type="roundtable",
                session_id=session_id,
                entry_type="conclusion",
                content=final_conclusion,
                category="final",
                retention_policy="permanent",
            )

    async def write_execution_result(
        self,
        session_id: str,
        result_summary: str,
        validation_results: Optional[str] = None,
        artifacts: Optional[List[str]] = None,
        reusable_experience: Optional[str] = None,
    ):
        """Write execution session memory per requirements.

        Preserves: execution results, validation results, artifact refs, reusable experience.
        """
        await self.write(
            session_type="execution",
            session_id=session_id,
            entry_type="summary",
            content=result_summary,
            category="execution_result",
            retention_policy="permanent",
        )
        if validation_results:
            await self.write(
                session_type="execution",
                session_id=session_id,
                entry_type="summary",
                content=validation_results,
                category="validation",
                retention_policy="session",
            )
        if artifacts:
            await self.write(
                session_type="execution",
                session_id=session_id,
                entry_type="summary",
                content=", ".join(artifacts),
                category="artifacts",
                retention_policy="permanent",
            )
        if reusable_experience:
            await self.write(
                session_type="execution",
                session_id=session_id,
                entry_type="experience",
                content=reusable_experience,
                category="reusable",
                retention_policy="permanent",
            )

    async def write_failure(
        self,
        session_type: str,
        session_id: str,
        failure_reason: str,
        fix_suggestion: Optional[str] = None,
    ):
        """Write failure memory per requirements.

        Preserves failure reasons and fix suggestions.
        Avoids meaningless log dumping into long-term memory.
        """
        await self.write(
            session_type=session_type,
            session_id=session_id,
            entry_type="failure",
            content=failure_reason,
            category="failure_reason",
            retention_policy="permanent",
            metadata={"fix_suggestion": fix_suggestion} if fix_suggestion else None,
        )

    async def query(
        self,
        session_type: Optional[str] = None,
        session_id: Optional[str] = None,
        entry_type: Optional[str] = None,
        category: Optional[str] = None,
        retention_policy: Optional[str] = None,
        limit: int = 20,
    ) -> List[MemoryEntry]:
        """Query memory entries by criteria.

        Per requirements: MVP does not auto-retrieve all history.
        Only on-demand retrieval for planning, proposal reuse, execution failure retry, etc.
        Memory returns "conclusion summaries" first, not raw long conversations.
        """
        async with async_session() as db:
            query = select(MemoryEntry)

            if session_type:
                query = query.where(MemoryEntry.session_type == session_type)
            if session_id:
                query = query.where(MemoryEntry.session_id == session_id)
            if entry_type:
                query = query.where(MemoryEntry.entry_type == entry_type)
            if category:
                query = query.where(MemoryEntry.category == category)
            if retention_policy:
                query = query.where(MemoryEntry.retention_policy == retention_policy)

            query = query.order_by(MemoryEntry.created_at.desc()).limit(limit)
            result = await db.execute(query)
            return result.scalars().all()

    async def get_conclusions(
        self,
        session_type: Optional[str] = None,
        limit: int = 10,
    ) -> List[MemoryEntry]:
        """Get conclusion-type memories (the most important type for reuse).

        Per requirements: memory prioritizes "conclusion summaries" over raw long conversations.
        """
        return await self.query(
            session_type=session_type,
            entry_type="conclusion",
            retention_policy="permanent",
            limit=limit,
        )


# Global singleton
memory_service = MemoryService()
