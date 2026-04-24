"""Agent Heartbeat Service - Liveness tracking per requirements (A-006).

Per requirements: System should record:
- heartbeat_at
- last_progress_at
- current_task_id
- status

When unresponsive detected, Leader or execution manager should:
- Continue waiting
- Retry
- Transfer to other agent
- Mark failed and notify user
"""

import logging
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import select, update

from app.core.database import async_session
from app.models.models import AgentHeartbeat

logger = logging.getLogger(__name__)


class HeartbeatService:
    """Service for tracking agent and CLI executor liveness."""

    async def register(
        self,
        agent_name: str,
        agent_type: str = "server",
    ) -> AgentHeartbeat:
        """Register or update an agent's heartbeat entry."""
        async with async_session() as db:
            result = await db.execute(
                select(AgentHeartbeat).where(AgentHeartbeat.agent_name == agent_name)
            )
            existing = result.scalars().first()

            now = datetime.now(timezone.utc)
            if existing:
                existing.agent_type = agent_type
                existing.heartbeat_at = now
                existing.status = "idle"
                await db.commit()
                await db.refresh(existing)
                return existing

            hb = AgentHeartbeat(
                agent_name=agent_name,
                agent_type=agent_type,
                status="idle",
                heartbeat_at=now,
            )
            db.add(hb)
            await db.commit()
            await db.refresh(hb)
            return hb

    async def heartbeat(
        self,
        agent_name: str,
        current_task_id: Optional[str] = None,
        current_session_id: Optional[str] = None,
        status: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> Optional[AgentHeartbeat]:
        """Update an agent's heartbeat.

        Args:
            agent_name: Name of the agent
            current_task_id: Currently executing task ID
            current_session_id: Currently active session ID
            status: Agent status (idle, busy, unresponsive)
            metadata: Optional metadata dict
        """
        import json
        async with async_session() as db:
            result = await db.execute(
                select(AgentHeartbeat).where(AgentHeartbeat.agent_name == agent_name)
            )
            hb = result.scalars().first()
            if not hb:
                logger.warning(f"Heartbeat for unknown agent: {agent_name}")
                return None

            now = datetime.now(timezone.utc)
            hb.heartbeat_at = now
            if current_task_id is not None:
                hb.current_task_id = current_task_id
            if current_session_id is not None:
                hb.current_session_id = current_session_id
            if status is not None:
                hb.status = status
            if metadata is not None:
                hb.metadata_json = json.dumps(metadata)
            if status == "busy" or current_task_id:
                hb.last_progress_at = now

            await db.commit()
            await db.refresh(hb)
            return hb

    async def report_progress(
        self,
        agent_name: str,
        task_id: Optional[str] = None,
    ):
        """Report progress for an agent (updates last_progress_at)."""
        async with async_session() as db:
            result = await db.execute(
                select(AgentHeartbeat).where(AgentHeartbeat.agent_name == agent_name)
            )
            hb = result.scalars().first()
            if hb:
                now = datetime.now(timezone.utc)
                hb.last_progress_at = now
                hb.heartbeat_at = now
                if task_id:
                    hb.current_task_id = task_id
                await db.commit()

    async def get_heartbeat(self, agent_name: str) -> Optional[AgentHeartbeat]:
        """Get the heartbeat record for an agent."""
        async with async_session() as db:
            result = await db.execute(
                select(AgentHeartbeat).where(AgentHeartbeat.agent_name == agent_name)
            )
            return result.scalars().first()

    async def list_heartbeats(self, agent_type: Optional[str] = None) -> List[AgentHeartbeat]:
        """List all agent heartbeats, optionally filtered by type."""
        async with async_session() as db:
            query = select(AgentHeartbeat)
            if agent_type:
                query = query.where(AgentHeartbeat.agent_type == agent_type)
            result = await db.execute(query.order_by(AgentHeartbeat.agent_name))
            return result.scalars().all()

    async def check_liveness(
        self,
        timeout_seconds: int = 120,
    ) -> List[dict]:
        """Check which agents are unresponsive.

        Args:
            timeout_seconds: Seconds since last heartbeat before considering unresponsive

        Returns:
            List of dicts with agent_name, status, last_heartbeat, is_unresponsive
        """
        now = datetime.now(timezone.utc)
        heartbeats = await self.list_heartbeats()

        results = []
        for hb in heartbeats:
            is_unresponsive = False
            if hb.heartbeat_at:
                elapsed = (now - hb.heartbeat_at).total_seconds()
                if elapsed > timeout_seconds:
                    is_unresponsive = True
                    # Auto-update status
                    if hb.status != "unresponsive":
                        async with async_session() as db:
                            result = await db.execute(
                                select(AgentHeartbeat).where(AgentHeartbeat.agent_name == hb.agent_name)
                            )
                            db_hb = result.scalars().first()
                            if db_hb:
                                db_hb.status = "unresponsive"
                                await db.commit()

            results.append({
                "agent_name": hb.agent_name,
                "agent_type": hb.agent_type,
                "status": "unresponsive" if is_unresponsive else hb.status,
                "current_task_id": hb.current_task_id,
                "current_session_id": hb.current_session_id,
                "heartbeat_at": hb.heartbeat_at.isoformat() if hb.heartbeat_at else None,
                "last_progress_at": hb.last_progress_at.isoformat() if hb.last_progress_at else None,
                "is_unresponsive": is_unresponsive,
            })

        return results

    async def unregister(self, agent_name: str):
        """Remove an agent's heartbeat record."""
        async with async_session() as db:
            result = await db.execute(
                select(AgentHeartbeat).where(AgentHeartbeat.agent_name == agent_name)
            )
            hb = result.scalars().first()
            if hb:
                await db.delete(hb)
                await db.commit()


# Global singleton
heartbeat_service = HeartbeatService()
