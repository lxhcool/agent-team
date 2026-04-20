"""Monitor — Agent 状态监控与超时处理"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

from team_agent.agents.base import AgentState

logger = logging.getLogger(__name__)


@dataclass
class AgentStatus:
    """Agent 状态快照"""

    name: str
    state: AgentState
    current_task: str | None = None
    started_at: float | None = None
    last_active: float | None = None
    iterations: int = 0
    errors: int = 0


@dataclass
class TraceEntry:
    """执行轨迹条目"""

    agent_name: str
    action: str
    timestamp: float = field(default_factory=time.time)
    duration_ms: float = 0
    tokens: int = 0
    model: str = ""
    data: dict[str, Any] = field(default_factory=dict)


class Monitor:
    """监控器 — 跟踪 Agent 状态、执行轨迹、超时处理"""

    def __init__(self, timeout_seconds: float = 300.0, check_interval: float = 10.0):
        self.timeout_seconds = timeout_seconds
        self.check_interval = check_interval
        self._agent_statuses: dict[str, AgentStatus] = {}
        self._traces: list[TraceEntry] = []
        self._on_timeout: list[Callable[[str], Coroutine[Any, Any, None]]] = []
        self._running = False
        self._monitor_task: asyncio.Task | None = None

    def register_agent(self, name: str) -> None:
        """注册 Agent 到监控"""
        self._agent_statuses[name] = AgentStatus(
            name=name,
            state=AgentState.IDLE,
        )

    def update_agent_state(self, name: str, state: AgentState, task: str | None = None) -> None:
        """更新 Agent 状态"""
        if name not in self._agent_statuses:
            self.register_agent(name)

        status = self._agent_statuses[name]
        status.state = state
        status.last_active = time.time()

        if state == AgentState.WORKING:
            status.current_task = task
            if status.started_at is None:
                status.started_at = time.time()
        elif state in (AgentState.IDLE, AgentState.ERROR):
            if state == AgentState.ERROR:
                status.errors += 1
            status.current_task = None
            status.started_at = None

    def add_trace(self, entry: TraceEntry) -> None:
        """添加执行轨迹"""
        self._traces.append(entry)

    def on_timeout(self, handler: Callable[[str], Coroutine[Any, Any, None]]) -> None:
        """注册超时处理器"""
        self._on_timeout.append(handler)

    async def start_monitoring(self) -> None:
        """启动监控循环"""
        self._running = True
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        logger.info("Monitor started")

    async def stop_monitoring(self) -> None:
        """停止监控"""
        self._running = False
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        logger.info("Monitor stopped")

    async def _monitor_loop(self) -> None:
        """监控循环"""
        while self._running:
            await self._check_timeouts()
            await asyncio.sleep(self.check_interval)

    async def _check_timeouts(self) -> None:
        """检查超时"""
        now = time.time()
        for name, status in self._agent_statuses.items():
            if status.state == AgentState.WORKING and status.started_at:
                elapsed = now - status.started_at
                if elapsed > self.timeout_seconds:
                    logger.warning(f"Agent {name} timed out after {elapsed:.1f}s")
                    for handler in self._on_timeout:
                        try:
                            await handler(name)
                        except Exception as e:
                            logger.error(f"Timeout handler error for {name}: {e}")

    def get_status(self) -> dict[str, Any]:
        """获取监控状态"""
        return {
            "agents": {name: {
                "state": s.state.value,
                "current_task": s.current_task,
                "errors": s.errors,
                "last_active": s.last_active,
            } for name, s in self._agent_statuses.items()},
            "total_traces": len(self._traces),
            "total_tokens": sum(t.tokens for t in self._traces),
        }

    def get_traces(self, agent_name: str | None = None, limit: int = 100) -> list[TraceEntry]:
        """获取执行轨迹"""
        traces = self._traces
        if agent_name:
            traces = [t for t in traces if t.agent_name == agent_name]
        return traces[-limit:]

    def get_usage_summary(self) -> dict[str, dict[str, Any]]:
        """获取用量统计"""
        summary: dict[str, dict[str, Any]] = {}
        for trace in self._traces:
            if trace.agent_name not in summary:
                summary[trace.agent_name] = {
                    "calls": 0,
                    "tokens": 0,
                    "total_duration_ms": 0,
                }
            summary[trace.agent_name]["calls"] += 1
            summary[trace.agent_name]["tokens"] += trace.tokens
            summary[trace.agent_name]["total_duration_ms"] += trace.duration_ms
        return summary
