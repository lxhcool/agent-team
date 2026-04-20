"""执行轨迹追踪 — 记录每一步的输入输出"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class TraceRecord:
    """执行轨迹记录"""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = ""
    agent_name: str = ""
    step: str = ""  # 当前步骤描述
    action: str = ""  # 动作类型: llm_call / tool_call / message_send
    model: str = ""
    input_data: str = ""  # 输入（截断后的）
    output_data: str = ""  # 输出（截断后的）
    tokens_prompt: int = 0
    tokens_completion: int = 0
    tokens_total: int = 0
    latency_ms: float = 0.0
    status: str = "success"  # success / error / timeout
    error_message: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "agent_name": self.agent_name,
            "step": self.step,
            "action": self.action,
            "model": self.model,
            "input_data": self.input_data[:500],  # 截断
            "output_data": self.output_data[:500],
            "tokens_prompt": self.tokens_prompt,
            "tokens_completion": self.tokens_completion,
            "tokens_total": self.tokens_total,
            "latency_ms": self.latency_ms,
            "status": self.status,
            "error_message": self.error_message,
            "timestamp": self.timestamp,
        }


class Tracer:
    """执行轨迹追踪器"""

    def __init__(self, db_path: str | Path = "data/team_agent.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        conn = self._get_conn()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS traces (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    agent_name TEXT NOT NULL,
                    step TEXT,
                    action TEXT NOT NULL,
                    model TEXT,
                    input_data TEXT,
                    output_data TEXT,
                    tokens_prompt INTEGER DEFAULT 0,
                    tokens_completion INTEGER DEFAULT 0,
                    tokens_total INTEGER DEFAULT 0,
                    latency_ms REAL DEFAULT 0,
                    status TEXT DEFAULT 'success',
                    error_message TEXT,
                    timestamp TEXT,
                    metadata TEXT,
                    INDEX idx_traces_session (session_id),
                    INDEX idx_traces_agent (agent_name),
                    INDEX idx_traces_timestamp (timestamp)
                )
            """)
            conn.commit()
        finally:
            conn.close()

    def record(self, trace: TraceRecord) -> str:
        """记录一条轨迹"""
        conn = self._get_conn()
        try:
            conn.execute(
                """INSERT INTO traces
                   (id, session_id, agent_name, step, action, model, input_data, output_data,
                    tokens_prompt, tokens_completion, tokens_total, latency_ms, status,
                    error_message, timestamp, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    trace.id, trace.session_id, trace.agent_name, trace.step, trace.action,
                    trace.model, trace.input_data, trace.output_data,
                    trace.tokens_prompt, trace.tokens_completion, trace.tokens_total,
                    trace.latency_ms, trace.status, trace.error_message,
                    trace.timestamp, json.dumps(trace.metadata) if trace.metadata else None,
                ),
            )
            conn.commit()
            return trace.id
        finally:
            conn.close()

    def get_traces(
        self,
        session_id: str | None = None,
        agent_name: str | None = None,
        action: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """查询轨迹"""
        conn = self._get_conn()
        try:
            query = "SELECT * FROM traces WHERE 1=1"
            params: list[Any] = []

            if session_id:
                query += " AND session_id = ?"
                params.append(session_id)
            if agent_name:
                query += " AND agent_name = ?"
                params.append(agent_name)
            if action:
                query += " AND action = ?"
                params.append(action)

            query += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])

            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_session_timeline(self, session_id: str) -> list[dict[str, Any]]:
        """获取会话的时间线（按时间排序的轨迹）"""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM traces WHERE session_id = ? ORDER BY timestamp",
                (session_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    class TraceContext:
        """轨迹上下文管理器 — 自动计时和记录"""

        def __init__(
            self,
            tracer: Tracer,
            session_id: str,
            agent_name: str,
            step: str,
            action: str,
            model: str = "",
            input_data: str = "",
        ):
            self.tracer = tracer
            self.trace = TraceRecord(
                session_id=session_id,
                agent_name=agent_name,
                step=step,
                action=action,
                model=model,
                input_data=input_data,
            )
            self._start = 0.0

        def __enter__(self):
            self._start = time.time()
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            self.trace.latency_ms = (time.time() - self._start) * 1000
            if exc_type:
                self.trace.status = "error"
                self.trace.error_message = str(exc_val)
            self.tracer.record(self.trace)
            return False  # 不吞掉异常

        async def __aenter__(self):
            self._start = time.time()
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            self.trace.latency_ms = (time.time() - self._start) * 1000
            if exc_type:
                self.trace.status = "error"
                self.trace.error_message = str(exc_val)
            self.tracer.record(self.trace)
            return False

        def set_output(self, output: str) -> None:
            self.trace.output_data = output

        def set_tokens(self, prompt: int, completion: int) -> None:
            self.trace.tokens_prompt = prompt
            self.trace.tokens_completion = completion
            self.trace.tokens_total = prompt + completion

    def trace(
        self,
        session_id: str,
        agent_name: str,
        step: str,
        action: str,
        model: str = "",
        input_data: str = "",
    ) -> TraceContext:
        """创建轨迹上下文管理器"""
        return self.TraceContext(
            tracer=self,
            session_id=session_id,
            agent_name=agent_name,
            step=step,
            action=action,
            model=model,
            input_data=input_data,
        )
