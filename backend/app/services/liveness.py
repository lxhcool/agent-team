"""Agent Liveness Detection - Runtime logic for A-006.

Per requirements: When unresponsive agents are detected, the system
should take action:
- Continue waiting
- Retry
- Transfer to other agent
- Mark failed and notify user

This service runs as a background task that periodically checks
agent heartbeats and takes appropriate action.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import select, and_

from app.core.database import async_session
from app.models.models import AgentHeartbeat, Task, TaskStatus
from app.services.event_bus import event_bus, Event

logger = logging.getLogger(__name__)


class LivenessDetector:
    """Background service that detects unresponsive agents and takes action.

    Per A-006:
    - Periodically checks agent heartbeats
    - Marks agents as unresponsive if timeout exceeded
    - Notifies Leader/orchestrator about unresponsive agents
    - Optionally reassigns tasks from unresponsive agents
    """

    def __init__(self, check_interval_seconds: int = 60, timeout_seconds: int = 180):
        self.check_interval = check_interval_seconds
        self.timeout_seconds = timeout_seconds
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self):
        """Start the liveness detection background task."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(f"LivenessDetector started (interval={self.check_interval}s, timeout={self.timeout_seconds})")

    async def stop(self):
        """Stop the liveness detection background task."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("LivenessDetector stopped")

    async def _run_loop(self):
        """Main loop that periodically checks agent liveness."""
        while self._running:
            try:
                await self._check_all_agents()
            except Exception as e:
                logger.error(f"Liveness check failed: {e}")
            await asyncio.sleep(self.check_interval)

    async def _check_all_agents(self):
        """Check all registered agents for liveness."""
        async with async_session() as db:
            result = await db.execute(select(AgentHeartbeat))
            heartbeats = result.scalars().all()

        now = datetime.now(timezone.utc)
        unresponsive_agents = []

        for hb in heartbeats:
            if hb.status == "idle":
                continue  # Idle agents don't need liveness checks

            if not hb.heartbeat_at:
                continue  # No heartbeat recorded yet

            elapsed = (now - hb.heartbeat_at).total_seconds()
            if elapsed > self.timeout_seconds:
                unresponsive_agents.append(hb)

                # Mark as unresponsive in DB
                async with async_session() as db:
                    result = await db.execute(
                        select(AgentHeartbeat).where(AgentHeartbeat.agent_name == hb.agent_name)
                    )
                    db_hb = result.scalars().first()
                    if db_hb and db_hb.status != "unresponsive":
                        db_hb.status = "unresponsive"
                        await db.commit()

        # Take action for unresponsive agents
        for hb in unresponsive_agents:
            await self._handle_unresponsive_agent(hb)

    async def _handle_unresponsive_agent(self, heartbeat: AgentHeartbeat):
        """Handle an unresponsive agent.

        Per A-006: Take appropriate action:
        1. If agent has a current task, mark it for reassignment
        2. Notify the orchestrator/leader
        3. Emit event for UI notification
        """
        agent_name = heartbeat.agent_name
        task_id = heartbeat.current_task_id
        session_id = heartbeat.current_session_id

        logger.warning(
            f"Agent {agent_name} is unresponsive "
            f"(last heartbeat: {heartbeat.heartbeat_at}, "
            f"task: {task_id}, session: {session_id})"
        )

        # If agent has a task, handle task reassignment
        if task_id:
            await self._handle_orphaned_task(task_id, session_id, agent_name)

        # Notify via event bus
        if session_id:
            event_bus.publish(session_id, Event(
                event="agent_unresponsive",
                data={
                    "agent_name": agent_name,
                    "task_id": task_id,
                    "last_heartbeat": heartbeat.heartbeat_at.isoformat() if heartbeat.heartbeat_at else None,
                    "action": "task_reassigned" if task_id else "notified",
                },
            ))

        # Also publish globally for monitoring
        event_bus.publish("__global__", Event(
            event="agent_unresponsive",
            data={
                "agent_name": agent_name,
                "task_id": task_id,
                "session_id": session_id,
            },
        ))

    async def _handle_orphaned_task(self, task_id: str, session_id: Optional[str], failed_agent: str):
        """Handle a task whose agent has become unresponsive.

        Strategy:
        1. Mark the task as BLOCKED
        2. Record the failure reason
        3. The Leader/orchestrator will see the blocked task and can reassign it
        """
        async with async_session() as db:
            task = await db.get(Task, task_id)
            if task and task.status not in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
                task.status = TaskStatus.BLOCKED
                task.result_summary = f"Agent {failed_agent} became unresponsive. Task needs reassignment."
                await db.commit()

                logger.info(f"Task {task_id} marked as BLOCKED due to unresponsive agent {failed_agent}")


# Global singleton
liveness_detector = LivenessDetector()
