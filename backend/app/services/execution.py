"""Execution Services - P1-2, P1-5, P1-9, P2 additions.

Implements:
- P1-2: ExecutionSessionService - full lifecycle management for Execution Sessions
- P1-5: TaskScheduler - independent task scheduling with dependency ordering
- P1-9: PlanningSessionService - centralized planning session management
- P2-SS-009: Session pause/resume with checkpoint
- P2-SS-010: Active timeout - auto-pause idle sessions
- P2-O-013: Parallel DAG scheduler
- P2-F-005: Tool-level checkpoint recording
- P2-UF-010: Upload file cleanup
- P2-EX-001: Webhook notification
- P2-I-006: Roundtable consensus detection
"""

import asyncio
import hashlib
import json
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Set, Callable

from sqlalchemy import select, and_, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session
from app.models.models import (
    ExecutionSession, ExecutionStatus,
    PlanningSession, PlanningStatus,
    RoundtableSession, RoundtableStatus,
    Task, TaskStatus, Checkpoint, Artifact, Message, MessageType,
    validate_planning_transition, validate_task_transition,
)
from app.services.event_bus import event_bus, Event

logger = logging.getLogger(__name__)


# ===== P2-SS-009: Session Pause/Resume =====

class SessionPauseService:
    """P2-SS-009: Pause and resume sessions with checkpoint preservation.

    When a session is paused:
    - Resources are released (agent deallocation)
    - A checkpoint is saved for recovery
    - Status transitions to PAUSED

    When resumed:
    - State is restored from the last checkpoint
    - Agents are reallocated
    - Status transitions back to EXECUTING
    """

    @staticmethod
    async def pause_execution_session(session_id: str, reason: str = "User requested pause") -> Optional[ExecutionSession]:
        """Pause an Execution Session, saving a checkpoint."""
        async with async_session() as db:
            session = await db.get(ExecutionSession, session_id)
            if not session:
                return None
            if session.status != ExecutionStatus.EXECUTING:
                return session

            # Save checkpoint before pausing
            checkpoint = Checkpoint(
                session_type="execution",
                session_id=session_id,
                checkpoint_type="business",
                label="pre_pause",
                state_json=json.dumps({
                    "plan_id": session.plan_id,
                    "project_path": session.project_path,
                    "paused_at": datetime.now(timezone.utc).isoformat(),
                    "reason": reason,
                    "description": f"Session paused: {reason}",
                }),
                created_by="system",
            )
            db.add(checkpoint)

            session.status = ExecutionStatus.PAUSED
            await db.commit()
            await db.refresh(session)

        event_bus.publish(session_id, Event(
            event="execution_paused",
            data={"session_id": session_id, "reason": reason},
        ))
        return session

    @staticmethod
    async def resume_execution_session(session_id: str) -> Optional[ExecutionSession]:
        """Resume a paused Execution Session from the last checkpoint."""
        async with async_session() as db:
            session = await db.get(ExecutionSession, session_id)
            if not session or session.status != ExecutionStatus.PAUSED:
                return session

            # Save a resume checkpoint
            checkpoint = Checkpoint(
                session_type="execution",
                session_id=session_id,
                checkpoint_type="business",
                label="resumed",
                state_json=json.dumps({
                    "resumed_at": datetime.now(timezone.utc).isoformat(),
                    "description": "Session resumed from pause",
                }),
                created_by="system",
            )
            db.add(checkpoint)

            session.status = ExecutionStatus.EXECUTING
            await db.commit()
            await db.refresh(session)

        event_bus.publish(session_id, Event(
            event="execution_resumed",
            data={"session_id": session_id},
        ))
        return session

    @staticmethod
    async def pause_planning_session(session_id: str, reason: str = "User requested pause") -> Optional[PlanningSession]:
        """Pause a Planning Session (keeps current status but records checkpoint)."""
        async with async_session() as db:
            session = await db.get(PlanningSession, session_id)
            if not session:
                return None
            if session.status in (PlanningStatus.COMPLETED, PlanningStatus.CANCELLED, PlanningStatus.FAILED):
                return session

            checkpoint = Checkpoint(
                session_type="planning",
                session_id=session_id,
                checkpoint_type="business",
                label="pre_pause",
                state_json=json.dumps({
                    "status_at_pause": session.status.value,
                    "paused_at": datetime.now(timezone.utc).isoformat(),
                    "reason": reason,
                    "description": f"Planning session paused: {reason}",
                }),
                created_by="system",
            )
            db.add(checkpoint)
            await db.commit()

        event_bus.publish(session_id, Event(
            event="planning_paused",
            data={"session_id": session_id, "reason": reason},
        ))
        return session


# ===== Planning Session Recovery =====

class PlanningSessionRecoveryService:
    """Recover stale planning sessions into a user-actionable state.

    A planning job can be interrupted after writing the proposal message but
    before updating the session status. Without recovery, the UI keeps showing
    an endless generating state even though the user can already review output.
    """

    @staticmethod
    async def recover_stale_planning_sessions(timeout_minutes: int = 60) -> dict[str, int]:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=timeout_minutes)
        recovered = 0
        failed = 0

        async with async_session() as db:
            result = await db.execute(
                select(PlanningSession).where(
                    PlanningSession.status == PlanningStatus.PLANNING,
                    PlanningSession.updated_at < cutoff,
                )
            )
            sessions = result.scalars().all()

            for session in sessions:
                proposal_result = await db.execute(
                    select(Message)
                    .where(
                        Message.session_id == session.id,
                        Message.message_type == MessageType.PROPOSAL,
                    )
                    .order_by(Message.seq.desc())
                    .limit(1)
                )
                proposal = proposal_result.scalars().first()

                if proposal:
                    session.status = PlanningStatus.AWAITING_APPROVAL
                    session.summary = session.summary or proposal.content[:500]
                    recovered += 1
                    event_bus.publish(session.id, Event(
                        event="status_changed",
                        data={
                            "session_id": session.id,
                            "status": PlanningStatus.AWAITING_APPROVAL.value,
                            "reason": "Recovered stale planning session with generated proposal",
                        },
                    ))
                    continue

                session.status = PlanningStatus.FAILED
                session.summary = (
                    f"Planning timed out with no generated proposal after {timeout_minutes} minutes. "
                    "Please retry this session."
                )
                failed += 1
                event_bus.publish(session.id, Event(
                    event="error",
                    data={
                        "session_id": session.id,
                        "message": session.summary,
                    },
                ))

            if recovered or failed:
                await db.commit()

        if recovered or failed:
            logger.info(
                "Recovered stale planning sessions: recovered=%s failed=%s timeout=%smin",
                recovered,
                failed,
                timeout_minutes,
            )

        return {"recovered": recovered, "failed": failed}


# ===== P2-SS-010: Active Timeout =====

class SessionTimeoutMonitor:
    """P2-SS-010: Monitor sessions for inactivity and auto-pause.

    Runs as a background task that checks for sessions with no
    recent activity (messages, status changes) and auto-pauses them.
    """

    def __init__(self, timeout_minutes: int = 60, check_interval_seconds: int = 300):
        self.timeout_minutes = timeout_minutes
        self.check_interval_seconds = check_interval_seconds
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(f"SessionTimeoutMonitor started (timeout={self.timeout_minutes}min, interval={self.check_interval_seconds}s)")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run_loop(self):
        while self._running:
            try:
                await self._check_sessions()
            except Exception as e:
                logger.error(f"Session timeout check failed: {e}")
            await asyncio.sleep(self.check_interval_seconds)

    async def _check_sessions(self):
        """Check all active sessions for timeout."""
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=self.timeout_minutes)

        async with async_session() as db:
            # Check Execution Sessions
            result = await db.execute(
                select(ExecutionSession).where(
                    ExecutionSession.status == ExecutionStatus.EXECUTING,
                    ExecutionSession.updated_at < cutoff,
                )
            )
            expired_exec_sessions = result.scalars().all()

            for session in expired_exec_sessions:
                logger.info(f"P2-SS-010: Auto-pausing idle execution session {session.id}")
                await SessionPauseService.pause_execution_session(
                    session.id, reason=f"Auto-paused: no activity for {self.timeout_minutes} minutes"
                )

        await PlanningSessionRecoveryService.recover_stale_planning_sessions(
            timeout_minutes=self.timeout_minutes
        )


# ===== P2-O-013: Parallel DAG Scheduler =====

class ParallelDAGScheduler:
    """P2-O-013: Parallel DAG scheduler for independent task execution.

    Unlike the serial TaskScheduler, this resolves the full DAG
    and identifies tasks that can run in parallel (no dependencies
    on each other). Returns execution waves.
    """

    @staticmethod
    async def get_parallel_waves(session_id: str) -> List[List[dict]]:
        """Get tasks organized in parallel execution waves.

        Returns a list of waves, where each wave contains tasks
        that can all be executed simultaneously.
        """
        async with async_session() as db:
            result = await db.execute(
                select(Task).where(Task.session_id == session_id).order_by(Task.order.asc())
            )
            all_tasks = list(result.scalars().all())

        if not all_tasks:
            return []

        # Build dependency graph
        task_by_id = {t.id: t for t in all_tasks}
        task_deps: Dict[str, Set[str]] = {}

        for t in all_tasks:
            deps = json.loads(t.dependencies_json) if t.dependencies_json else []
            # Dependencies can be task IDs or indices
            dep_ids = set()
            for d in deps:
                if isinstance(d, str) and d in task_by_id:
                    dep_ids.add(d)
                elif isinstance(d, int) and d < len(all_tasks):
                    dep_ids.add(all_tasks[d].id)
            task_deps[t.id] = dep_ids

        # Topological sort with wave tracking
        completed_ids: Set[str] = {
            t.id for t in all_tasks if t.status == TaskStatus.COMPLETED
        }
        remaining = set(task_by_id.keys()) - completed_ids
        waves = []

        while remaining:
            # Find tasks whose dependencies are all completed
            ready = []
            for tid in list(remaining):
                if task_deps[tid].issubset(completed_ids):
                    ready.append(tid)

            if not ready:
                # Circular dependency or stuck
                logger.warning(f"P2-O-013: Circular dependency detected, {len(remaining)} tasks stuck")
                break

            wave = []
            for tid in ready:
                t = task_by_id[tid]
                wave.append({
                    "task_id": t.id,
                    "title": t.title,
                    "assigned_agent": t.assigned_agent,
                    "status": t.status.value,
                    "risk_level": getattr(t, "risk_level", "medium"),
                })
                remaining.remove(tid)
                completed_ids.add(tid)

            waves.append(wave)

        return waves

    @staticmethod
    async def assign_parallel_wave(session_id: str, wave_index: int,
                                    agent_pool: Dict[str, List[str]]) -> List[Task]:
        """Assign a wave of parallel tasks to available agents.

        Args:
            session_id: The session to schedule tasks for
            wave_index: Which wave to assign (0-based)
            agent_pool: Map of role -> list of available agent names
        """
        waves = await ParallelDAGScheduler.get_parallel_waves(session_id)
        if wave_index >= len(waves):
            return []

        wave = waves[wave_index]
        assigned = []

        async with async_session() as db:
            for task_info in wave:
                task = await db.get(Task, task_info["task_id"])
                if not task or task.status not in (TaskStatus.PENDING, TaskStatus.READY):
                    continue

                # Find an available agent for the task's owner_role
                role = task.assigned_agent or "developer"
                agents = agent_pool.get(role, agent_pool.get("developer", []))

                if agents:
                    agent_name = agents.pop(0)  # Take first available
                    if validate_task_transition(task.status, TaskStatus.ASSIGNED):
                        task.status = TaskStatus.ASSIGNED
                        task.assigned_agent = agent_name
                        assigned.append(task)
                        # Put agent back at end of pool for round-robin
                        agents.append(agent_name)

            await db.commit()

        return assigned


# ===== P2-F-005: Tool-level Checkpoint =====

class ToolCheckpointRecorder:
    """P2-F-005: Record fine-grained Tool execution checkpoints in debug_mode.

    When debug_mode is enabled, records detailed checkpoints for
    each Tool invocation including input, output, timing, and errors.
    """

    @staticmethod
    async def record_tool_execution(
        session_id: str,
        session_type: str,
        tool_name: str,
        agent_name: str,
        input_params: dict,
        result: Any = None,
        error: Optional[str] = None,
        duration_ms: Optional[int] = None,
        task_id: Optional[str] = None,
    ):
        """Record a tool execution checkpoint."""
        state = {
            "tool": tool_name,
            "agent": agent_name,
            "input": {k: str(v)[:500] for k, v in input_params.items()},
            "duration_ms": duration_ms,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        if error:
            state["error"] = error[:1000]
            label = f"tool_error:{tool_name}"
        else:
            state["success"] = True
            if result:
                # Truncate large results
                result_str = json.dumps(result, ensure_ascii=False, default=str)[:2000]
                state["result_preview"] = result_str
            label = f"tool_exec:{tool_name}"

        async with async_session() as db:
            checkpoint = Checkpoint(
                session_type=session_type,
                session_id=session_id,
                task_id=task_id,
                checkpoint_type="tool",
                label=label,
                state_json=json.dumps({**state, "description": f"Tool {tool_name} executed by {agent_name}"}, ensure_ascii=False),
                created_by=agent_name,
            )
            db.add(checkpoint)
            await db.commit()


# ===== P2-UF-010: Upload File Cleanup =====

class FileCleanupService:
    """P2-UF-010: Automatic cleanup of uploaded files past retention period."""

    @staticmethod
    async def cleanup_expired_uploads(retention_days: int = 30) -> int:
        """Delete artifacts older than retention_days.

        Returns the number of deleted artifacts.
        """
        from app.core.config import settings
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        deleted_count = 0

        async with async_session() as db:
            # Find old upload artifacts
            result = await db.execute(
                select(Artifact).where(
                    Artifact.source == "upload",
                    Artifact.created_at < cutoff,
                )
            )
            old_artifacts = result.scalars().all()

            for artifact in old_artifacts:
                # Delete the physical file
                if artifact.file_path:
                    from pathlib import Path
                    file_path = Path(artifact.file_path)
                    if file_path.exists():
                        try:
                            file_path.unlink()
                            deleted_count += 1
                        except OSError as e:
                            logger.warning(f"Failed to delete file {file_path}: {e}")

                # Remove DB record
                await db.delete(artifact)

            await db.commit()

        if deleted_count > 0:
            logger.info(f"P2-UF-010: Cleaned up {deleted_count} expired upload artifacts (>{retention_days}d)")

        return deleted_count


# ===== P2-EX-001: Webhook Notification =====

class WebhookNotifier:
    """P2-EX-001: Send webhook notifications for task completion/failure."""

    _webhooks: Dict[str, List[dict]] = {}  # session_id -> list of webhook configs

    @classmethod
    def register_webhook(cls, session_id: str, url: str, events: Optional[List[str]] = None,
                         headers: Optional[dict] = None, secret: Optional[str] = None):
        """Register a webhook for a session."""
        if session_id not in cls._webhooks:
            cls._webhooks[session_id] = []

        cls._webhooks[session_id].append({
            "url": url,
            "events": events or ["task_completed", "task_failed", "session_completed"],
            "headers": headers or {},
            "secret": secret,
        })

    @classmethod
    def unregister_webhooks(cls, session_id: str):
        """Remove all webhooks for a session."""
        cls._webhooks.pop(session_id, None)

    @classmethod
    async def notify(cls, session_id: str, event_type: str, payload: dict):
        """Send webhook notification for an event."""
        webhooks = cls._webhooks.get(session_id, [])
        if not webhooks:
            return

        import httpx
        for webhook in webhooks:
            if event_type not in webhook.get("events", []):
                continue

            try:
                headers = {"Content-Type": "application/json"}
                headers.update(webhook.get("headers", {}))

                # Sign payload if secret is provided
                body = json.dumps({
                    "event": event_type,
                    "session_id": session_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "data": payload,
                }, ensure_ascii=False)

                if webhook.get("secret"):
                    sig = hashlib.sha256(
                        (body + webhook["secret"]).encode()
                    ).hexdigest()
                    headers["X-Webhook-Signature"] = f"sha256={sig}"

                async with httpx.AsyncClient(timeout=10.0) as client:
                    await client.post(webhook["url"], content=body, headers=headers)

            except Exception as e:
                logger.warning(f"P2-EX-001: Webhook delivery failed to {webhook['url']}: {e}")


# ===== P2-I-006: Roundtable Consensus Detection =====

class ConsensusDetector:
    """P2-I-006: Use LLM to detect if roundtable participants have reached consensus."""

    CONSENSUS_PROMPT = """分析以下圆桌讨论中各位参与者的发言，判断是否已经达成共识。

判定标准：
1. 所有关键参与者是否都对主要结论表示同意
2. 是否还有明显的分歧或反对意见
3. 讨论是否已经收敛到具体方案

请输出 JSON 格式：
{
  "consensus_reached": true/false,
  "confidence": 0.0-1.0,
  "main_points_of_agreement": ["..."],
  "remaining_disagreements": ["..."],
  "summary": "一句话总结当前共识状态"
}

讨论内容：
{discussion}"""

    @staticmethod
    async def detect_consensus(session_id: str) -> Optional[dict]:
        """Analyze a roundtable session for consensus.

        Returns a consensus analysis dict or None on failure.
        """
        try:
            from app.llm.router import llm_router
            from sqlalchemy import select, func

            async with async_session() as db:
                # Get session and messages
                session = await db.get(RoundtableSession, session_id)
                if not session:
                    return None

                from app.models.models import Message
                result = await db.execute(
                    select(Message).where(
                        Message.session_id == session_id,
                        Message.sender != "system",
                    ).order_by(Message.seq.asc())
                )
                messages = result.scalars().all()

            if not messages:
                return {"consensus_reached": False, "confidence": 0.0, "summary": "No discussion yet"}

            # Build discussion text
            discussion = ""
            for msg in messages:
                discussion += f"[{msg.sender}]: {msg.content[:500]}\n\n"

            # Truncate if too long
            if len(discussion) > 8000:
                discussion = discussion[:4000] + "\n...(中间内容已省略)...\n" + discussion[-4000:]

            # Call LLM for consensus analysis
            from app.llm.router import LLMMessage
            result = await llm_router.call(
                messages=[
                    LLMMessage(role="system", content="你是一个共识分析专家。请客观分析讨论内容。"),
                    LLMMessage(role="user", content=ConsensusDetector.CONSENSUS_PROMPT.format(discussion=discussion)),
                ],
                model="gpt-4o-mini",
                max_tokens=1024,
                temperature=0.3,
                session_id=session_id,
                session_type="roundtable",
                agent_name="consensus_detector",
            )

            # Parse JSON response
            content = result.content.strip()
            # Extract JSON from response (may have markdown fences)
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()

            analysis = json.loads(content)
            return analysis

        except Exception as e:
            logger.warning(f"P2-I-006: Consensus detection failed for session {session_id}: {e}")
            return None


# ===== P1-2: ExecutionSessionService =====

class ExecutionSessionService:
    """Full lifecycle management for Execution Sessions.

    Per P1-2: Execution Sessions need server-driven lifecycle management
    including state transitions, task tracking, and completion handling.
    """

    @staticmethod
    async def create(
        plan_id: str,
        user_id: str,
        project_path: Optional[str] = None,
        proposal_id: Optional[str] = None,
    ) -> ExecutionSession:
        """Create a new Execution Session."""
        async with async_session() as db:
            session = ExecutionSession(
                plan_id=plan_id,
                user_id=user_id,
                status=ExecutionStatus.CREATED,
                project_path=project_path,
                proposal_id=proposal_id,
            )
            db.add(session)
            await db.commit()
            await db.refresh(session)
            return session

    @staticmethod
    async def transition(session_id: str, target: ExecutionStatus) -> Optional[ExecutionSession]:
        """Transition an Execution Session to a new status with validation."""
        VALID_EXECUTION_TRANSITIONS = {
            ExecutionStatus.CREATED: {ExecutionStatus.READY, ExecutionStatus.CANCELLED},
            ExecutionStatus.READY: {ExecutionStatus.EXECUTING, ExecutionStatus.CANCELLED},
            ExecutionStatus.EXECUTING: {ExecutionStatus.COMPLETED, ExecutionStatus.PAUSED, ExecutionStatus.FAILED, ExecutionStatus.CANCELLED, ExecutionStatus.PARTIAL},
            ExecutionStatus.PAUSED: {ExecutionStatus.EXECUTING, ExecutionStatus.CANCELLED},
            ExecutionStatus.COMPLETED: set(),
            ExecutionStatus.FAILED: {ExecutionStatus.READY},  # Retry
            ExecutionStatus.PARTIAL: {ExecutionStatus.EXECUTING, ExecutionStatus.CANCELLED},
            ExecutionStatus.CANCELLED: set(),
        }

        async with async_session() as db:
            session = await db.get(ExecutionSession, session_id)
            if not session:
                return None

            allowed = VALID_EXECUTION_TRANSITIONS.get(session.status, set())
            if target not in allowed and session.status != target:
                logger.warning(
                    f"Invalid execution transition: {session.status.value} -> {target.value}"
                )
                return session  # Don't transition

            session.status = target
            await db.commit()
            await db.refresh(session)

            # Publish event
            event_bus.publish(session_id, Event(
                event="execution_status",
                data={
                    "session_id": session_id,
                    "status": target.value,
                    "plan_id": session.plan_id,
                },
            ))

            return session

    @staticmethod
    async def get(session_id: str) -> Optional[ExecutionSession]:
        """Get an Execution Session by ID."""
        async with async_session() as db:
            return await db.get(ExecutionSession, session_id)

    @staticmethod
    async def get_by_plan_id(plan_id: str) -> Optional[ExecutionSession]:
        """Get an Execution Session by plan_id."""
        async with async_session() as db:
            result = await db.execute(
                select(ExecutionSession).where(ExecutionSession.plan_id == plan_id)
            )
            return result.scalars().first()


# ===== P1-5: TaskScheduler =====

class TaskScheduler:
    """Independent task scheduling with dependency ordering and execution control.

    Per P1-5: Tasks need a scheduler that:
    - Resolves dependency ordering
    - Manages task assignment
    - Controls execution flow
    """

    @staticmethod
    async def get_ready_tasks(session_id: str) -> List[Task]:
        """Get tasks that are ready to execute (all dependencies met).

        A task is ready when:
        1. Its status is PENDING or READY
        2. All its dependencies are COMPLETED
        """
        async with async_session() as db:
            # Get all tasks for the session
            result = await db.execute(
                select(Task).where(Task.session_id == session_id).order_by(Task.order.asc())
            )
            all_tasks = result.scalars().all()

            # Build a map of task_id -> status
            task_status_map = {t.id: t.status for t in all_tasks}

            ready_tasks = []
            for task in all_tasks:
                if task.status not in (TaskStatus.PENDING, TaskStatus.READY):
                    continue

                # Check dependencies
                deps = json.loads(task.dependencies_json) if task.dependencies_json else []
                all_deps_met = True

                for dep_idx in deps:
                    # deps are indices, find the task at that index
                    if dep_idx < len(all_tasks):
                        dep_task = all_tasks[dep_idx]
                        if dep_task.status != TaskStatus.COMPLETED:
                            all_deps_met = False
                            break
                    else:
                        all_deps_met = False
                        break

                if all_deps_met:
                    ready_tasks.append(task)

            return ready_tasks

    @staticmethod
    async def assign_task(task_id: str, agent_name: str) -> Optional[Task]:
        """Assign a task to an agent with state transition validation (P1-4)."""
        async with async_session() as db:
            task = await db.get(Task, task_id)
            if not task:
                return None

            # P1-4: Validate transition
            target_status = TaskStatus.ASSIGNED
            if task.status == TaskStatus.PENDING:
                # PENDING -> READY -> ASSIGNED
                if not validate_task_transition(task.status, TaskStatus.READY):
                    return task
                task.status = TaskStatus.READY
                if not validate_task_transition(TaskStatus.READY, target_status):
                    return task
            elif not validate_task_transition(task.status, target_status):
                return task

            task.status = target_status
            task.assigned_agent = agent_name
            await db.commit()
            await db.refresh(task)
            return task

    @staticmethod
    async def update_task_status(task_id: str, target: TaskStatus,
                                  result_summary: Optional[str] = None) -> Optional[Task]:
        """Update a task's status with validation (P1-4)."""
        async with async_session() as db:
            task = await db.get(Task, task_id)
            if not task:
                return None

            # P1-4: Validate transition
            if not validate_task_transition(task.status, target):
                logger.warning(
                    f"Invalid task transition: {task.status.value} -> {target.value} "
                    f"for task {task_id}"
                )
                return task

            task.status = target
            if result_summary is not None:
                task.result_summary = result_summary
            await db.commit()
            await db.refresh(task)

            # Publish event
            if task.session_id:
                event_bus.publish(task.session_id, Event(
                    event="task_status",
                    data={
                        "task_id": task_id,
                        "status": target.value,
                        "title": task.title,
                    },
                ))

            return task

    @staticmethod
    async def get_execution_order(session_id: str) -> List[List[Task]]:
        """Get tasks grouped by execution order (topological sort).

        Returns a list of lists, where each inner list contains tasks
        that can be executed in parallel.
        """
        async with async_session() as db:
            result = await db.execute(
                select(Task).where(Task.session_id == session_id).order_by(Task.order.asc())
            )
            all_tasks = list(result.scalars().all())

            if not all_tasks:
                return []

            # Build dependency graph
            task_index_map = {i: t for i, t in enumerate(all_tasks)}
            completed_ids: Set[str] = {
                t.id for t in all_tasks if t.status == TaskStatus.COMPLETED
            }

            order = []
            remaining = set(range(len(all_tasks)))

            while remaining:
                # Find tasks with all deps satisfied
                ready = []
                for idx in list(remaining):
                    task = task_index_map[idx]
                    deps = json.loads(task.dependencies_json) if task.dependencies_json else []
                    if all(d not in remaining for d in deps):
                        ready.append(idx)

                if not ready:
                    # Circular dependency or stuck
                    break

                order.append([task_index_map[idx] for idx in ready])
                remaining -= set(ready)

            return order


# ===== P1-9: PlanningSessionService =====

class PlanningSessionService:
    """Centralized service for Planning Session management.

    Per P1-9: Logic currently scattered across API and orchestrator
    should be consolidated into a dedicated service.
    """

    @staticmethod
    async def create(title: str, input_text: str, user_id: str = "default_user",
                     mode: str = "planning") -> PlanningSession:
        """Create a new Planning Session."""
        async with async_session() as db:
            session = PlanningSession(
                title=title,
                user_id=user_id,
                input_text=input_text,
                mode=mode,
                status=PlanningStatus.CREATED,
            )
            db.add(session)
            await db.commit()
            await db.refresh(session)
            return session

    @staticmethod
    async def get(session_id: str) -> Optional[PlanningSession]:
        """Get a Planning Session by ID."""
        async with async_session() as db:
            return await db.get(PlanningSession, session_id)

    @staticmethod
    async def list_sessions(user_id: str = "default_user") -> List[PlanningSession]:
        """List all Planning Sessions for a user."""
        async with async_session() as db:
            result = await db.execute(
                select(PlanningSession)
                .where(PlanningSession.user_id == user_id)
                .order_by(PlanningSession.created_at.desc())
            )
            return list(result.scalars().all())

    @staticmethod
    async def transition(session_id: str, target: PlanningStatus,
                         detail: Optional[str] = None) -> Optional[PlanningSession]:
        """Transition a Planning Session's status with validation (P1-1)."""
        async with async_session() as db:
            session = await db.get(PlanningSession, session_id)
            if not session:
                return None

            # P1-1: Validate transition
            if not validate_planning_transition(session.status, target):
                logger.warning(
                    f"Invalid planning transition: {session.status.value} -> {target.value} "
                    f"for session {session_id}"
                )
                raise ValueError(
                    f"Cannot transition from '{session.status.value}' to '{target.value}'"
                )

            session.status = target
            await db.commit()
            await db.refresh(session)

            # Publish SSE event
            event_bus.publish(session_id, Event(
                event="status",
                data={"status": target.value, "detail": detail},
            ))

            return session

    @staticmethod
    async def cancel(session_id: str) -> Optional[PlanningSession]:
        """Cancel a Planning Session with validation (P1-1)."""
        return await PlanningSessionService.transition(
            session_id, PlanningStatus.CANCELLED, detail="Cancelled by user"
        )

    @staticmethod
    async def update_summary(session_id: str, summary: str) -> Optional[PlanningSession]:
        """Update a Planning Session's summary."""
        async with async_session() as db:
            session = await db.get(PlanningSession, session_id)
            if not session:
                return None
            session.summary = summary
            await db.commit()
            await db.refresh(session)
            return session


# ===== Global Singletons =====

execution_session_service = ExecutionSessionService()
task_scheduler = TaskScheduler()
planning_session_service = PlanningSessionService()
session_pause_service = SessionPauseService()
session_timeout_monitor = SessionTimeoutMonitor()
parallel_dag_scheduler = ParallelDAGScheduler()
tool_checkpoint_recorder = ToolCheckpointRecorder()
file_cleanup_service = FileCleanupService()
webhook_notifier = WebhookNotifier()
consensus_detector = ConsensusDetector()
