"""用量统计 — Token 消耗、调用次数、成本追踪"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


# 各模型的参考价格（美元/1K tokens）
MODEL_PRICING: dict[str, dict[str, float]] = {
    "gpt-4o": {"prompt": 0.005, "completion": 0.015},
    "gpt-4o-mini": {"prompt": 0.00015, "completion": 0.0006},
    "gpt-4-turbo": {"prompt": 0.01, "completion": 0.03},
    "gpt-3.5-turbo": {"prompt": 0.0005, "completion": 0.0015},
    "claude-sonnet-4-20250514": {"prompt": 0.003, "completion": 0.015},
    "claude-3-5-sonnet-20241022": {"prompt": 0.003, "completion": 0.015},
    "claude-3-haiku-20240307": {"prompt": 0.00025, "completion": 0.00125},
}


@dataclass
class UsageRecord:
    """用量记录"""

    user_id: str
    session_id: str
    agent_name: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float
    timestamp: str


class UsageTracker:
    """用量追踪器"""

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
                CREATE TABLE IF NOT EXISTS usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    agent_name TEXT NOT NULL,
                    model TEXT NOT NULL,
                    prompt_tokens INTEGER DEFAULT 0,
                    completion_tokens INTEGER DEFAULT 0,
                    total_tokens INTEGER DEFAULT 0,
                    cost_usd REAL DEFAULT 0,
                    timestamp TEXT,
                    INDEX idx_usage_user (user_id),
                    INDEX idx_usage_session (session_id),
                    INDEX idx_usage_timestamp (timestamp)
                )
            """)
            conn.commit()
        finally:
            conn.close()

    def calculate_cost(self, model: str, prompt_tokens: int, completion_tokens: int) -> float:
        """计算费用"""
        pricing = MODEL_PRICING.get(model)
        if not pricing:
            # 模糊匹配
            for key, val in MODEL_PRICING.items():
                if key in model or model in key:
                    pricing = val
                    break

        if not pricing:
            return 0.0

        cost = (prompt_tokens / 1000) * pricing["prompt"] + (completion_tokens / 1000) * pricing["completion"]
        return round(cost, 6)

    def record_usage(
        self,
        user_id: str,
        session_id: str,
        agent_name: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> float:
        """记录一次用量"""
        total_tokens = prompt_tokens + completion_tokens
        cost = self.calculate_cost(model, prompt_tokens, completion_tokens)

        conn = self._get_conn()
        try:
            conn.execute(
                """INSERT INTO usage
                   (user_id, session_id, agent_name, model, prompt_tokens, completion_tokens, total_tokens, cost_usd, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (user_id, session_id, agent_name, model, prompt_tokens, completion_tokens, total_tokens, cost, datetime.now().isoformat()),
            )
            conn.commit()
        finally:
            conn.close()

        return cost

    def get_user_usage(
        self,
        user_id: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        """获取用户用量汇总"""
        conn = self._get_conn()
        try:
            query = "SELECT * FROM usage WHERE user_id = ?"
            params: list[Any] = [user_id]

            if start_date:
                query += " AND timestamp >= ?"
                params.append(start_date)
            if end_date:
                query += " AND timestamp <= ?"
                params.append(end_date)

            rows = conn.execute(query, params).fetchall()

            total_tokens = 0
            total_cost = 0.0
            by_model: dict[str, dict[str, Any]] = {}
            by_agent: dict[str, dict[str, Any]] = {}

            for r in rows:
                d = dict(r)
                total_tokens += d["total_tokens"]
                total_cost += d["cost_usd"]

                model = d["model"]
                if model not in by_model:
                    by_model[model] = {"tokens": 0, "cost": 0.0, "calls": 0}
                by_model[model]["tokens"] += d["total_tokens"]
                by_model[model]["cost"] += d["cost_usd"]
                by_model[model]["calls"] += 1

                agent = d["agent_name"]
                if agent not in by_agent:
                    by_agent[agent] = {"tokens": 0, "cost": 0.0, "calls": 0}
                by_agent[agent]["tokens"] += d["total_tokens"]
                by_agent[agent]["cost"] += d["cost_usd"]
                by_agent[agent]["calls"] += 1

            return {
                "user_id": user_id,
                "total_tokens": total_tokens,
                "total_cost_usd": round(total_cost, 4),
                "by_model": by_model,
                "by_agent": by_agent,
            }
        finally:
            conn.close()

    def get_session_usage(self, session_id: str) -> dict[str, Any]:
        """获取会话用量"""
        conn = self._get_conn()
        try:
            rows = conn.execute("SELECT * FROM usage WHERE session_id = ?", (session_id,)).fetchall()

            total_tokens = 0
            total_cost = 0.0
            by_agent: dict[str, dict[str, Any]] = {}

            for r in rows:
                d = dict(r)
                total_tokens += d["total_tokens"]
                total_cost += d["cost_usd"]

                agent = d["agent_name"]
                if agent not in by_agent:
                    by_agent[agent] = {"tokens": 0, "cost": 0.0, "calls": 0}
                by_agent[agent]["tokens"] += d["total_tokens"]
                by_agent[agent]["cost"] += d["cost_usd"]
                by_agent[agent]["calls"] += 1

            return {
                "session_id": session_id,
                "total_tokens": total_tokens,
                "total_cost_usd": round(total_cost, 4),
                "by_agent": by_agent,
            }
        finally:
            conn.close()

    def get_daily_usage(self, user_id: str, days: int = 7) -> list[dict[str, Any]]:
        """获取每日用量"""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                """SELECT date(timestamp) as date, SUM(total_tokens) as tokens, SUM(cost_usd) as cost, COUNT(*) as calls
                   FROM usage WHERE user_id = ? AND timestamp >= date('now', ?)
                   GROUP BY date(timestamp) ORDER BY date""",
                (user_id, f"-{days} days"),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
