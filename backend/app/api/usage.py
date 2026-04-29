"""Usage Statistics API - aggregated LLM call usage and cost tracking."""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.database import get_db
from app.api.authz import get_owned_planning_session
from app.models.models import LLMCall, PlanningSession, User

router = APIRouter()


# ===== Endpoints =====

@router.get("/usage")
async def get_aggregated_usage(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get aggregated usage statistics across all sessions."""
    owned_session_ids = (
        select(PlanningSession.id)
        .where(PlanningSession.user_id == user.id)
        .scalar_subquery()
    )

    # Total aggregates
    total_result = await db.execute(
        select(
            func.count(LLMCall.id).label("total_calls"),
            func.coalesce(func.sum(LLMCall.prompt_tokens + LLMCall.completion_tokens), 0).label("total_tokens"),
            func.coalesce(func.sum(LLMCall.cost), 0.0).label("total_cost"),
        ).where(LLMCall.session_id.in_(owned_session_ids))
    )
    total_row = total_result.one()

    # By provider
    by_provider_result = await db.execute(
        select(
            LLMCall.provider,
            func.count(LLMCall.id).label("calls"),
            func.coalesce(func.sum(LLMCall.prompt_tokens + LLMCall.completion_tokens), 0).label("tokens"),
            func.coalesce(func.sum(LLMCall.cost), 0.0).label("cost"),
        )
        .where(LLMCall.session_id.in_(owned_session_ids))
        .group_by(LLMCall.provider)
    )
    by_provider = {}
    for row in by_provider_result.all():
        by_provider[row.provider] = {
            "calls": row.calls,
            "tokens": row.tokens,
            "cost": round(row.cost, 6),
        }

    # By model
    by_model_result = await db.execute(
        select(
            LLMCall.model,
            func.count(LLMCall.id).label("calls"),
            func.coalesce(func.sum(LLMCall.prompt_tokens + LLMCall.completion_tokens), 0).label("tokens"),
            func.coalesce(func.sum(LLMCall.cost), 0.0).label("cost"),
        )
        .where(LLMCall.session_id.in_(owned_session_ids))
        .group_by(LLMCall.model)
    )
    by_model = {}
    for row in by_model_result.all():
        by_model[row.model] = {
            "calls": row.calls,
            "tokens": row.tokens,
            "cost": round(row.cost, 6),
        }

    # By agent
    by_agent_result = await db.execute(
        select(
            LLMCall.agent_name,
            func.count(LLMCall.id).label("calls"),
            func.coalesce(func.sum(LLMCall.prompt_tokens + LLMCall.completion_tokens), 0).label("tokens"),
            func.coalesce(func.sum(LLMCall.cost), 0.0).label("cost"),
        )
        .where(LLMCall.session_id.in_(owned_session_ids))
        .group_by(LLMCall.agent_name)
    )
    by_agent = {}
    for row in by_agent_result.all():
        key = row.agent_name or "unknown"
        by_agent[key] = {
            "calls": row.calls,
            "tokens": row.tokens,
            "cost": round(row.cost, 6),
        }

    # Per session breakdown
    sessions_result = await db.execute(
        select(
            LLMCall.session_id,
            func.count(LLMCall.id).label("calls"),
            func.coalesce(func.sum(LLMCall.prompt_tokens + LLMCall.completion_tokens), 0).label("tokens"),
            func.coalesce(func.sum(LLMCall.cost), 0.0).label("cost"),
        )
        .where(LLMCall.session_id.in_(owned_session_ids))
        .group_by(LLMCall.session_id)
    )

    session_ids = []
    session_stats = {}
    for row in sessions_result.all():
        session_ids.append(row.session_id)
        session_stats[row.session_id] = {
            "calls": row.calls,
            "tokens": row.tokens,
            "cost": round(row.cost, 6),
        }

    # Get session titles
    sessions_list = []
    if session_ids:
        planning_result = await db.execute(
            select(PlanningSession).where(
                PlanningSession.id.in_(session_ids),
                PlanningSession.user_id == user.id,
            )
        )
        session_map = {s.id: s for s in planning_result.scalars().all()}
        for sid in session_ids:
            ps = session_map.get(sid)
            stats = session_stats[sid]
            sessions_list.append({
                "id": sid,
                "title": ps.title if ps else sid,
                "calls": stats["calls"],
                "tokens": stats["tokens"],
                "cost": stats["cost"],
            })

    return {
        "total_calls": total_row.total_calls,
        "total_tokens": total_row.total_tokens,
        "total_cost_usd": round(total_row.total_cost, 6),
        "by_provider": by_provider,
        "by_model": by_model,
        "by_agent": by_agent,
        "sessions": sessions_list,
    }


@router.get("/usage/sessions/{session_id}")
async def get_session_usage(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get detailed usage statistics for a specific session."""
    session = await get_owned_planning_session(db, session_id, user)

    # Session total
    total_result = await db.execute(
        select(
            func.count(LLMCall.id).label("total_calls"),
            func.coalesce(func.sum(LLMCall.prompt_tokens), 0).label("prompt_tokens"),
            func.coalesce(func.sum(LLMCall.completion_tokens), 0).label("completion_tokens"),
            func.coalesce(func.sum(LLMCall.cost), 0.0).label("total_cost"),
            func.coalesce(func.sum(LLMCall.duration_ms), 0).label("total_duration_ms"),
        ).where(LLMCall.session_id == session_id)
    )
    total_row = total_result.one()

    # By provider
    by_provider_result = await db.execute(
        select(
            LLMCall.provider,
            func.count(LLMCall.id).label("calls"),
            func.coalesce(func.sum(LLMCall.prompt_tokens + LLMCall.completion_tokens), 0).label("tokens"),
            func.coalesce(func.sum(LLMCall.cost), 0.0).label("cost"),
        ).where(LLMCall.session_id == session_id).group_by(LLMCall.provider)
    )
    by_provider = {}
    for row in by_provider_result.all():
        by_provider[row.provider] = {
            "calls": row.calls,
            "tokens": row.tokens,
            "cost": round(row.cost, 6),
        }

    # By model
    by_model_result = await db.execute(
        select(
            LLMCall.model,
            func.count(LLMCall.id).label("calls"),
            func.coalesce(func.sum(LLMCall.prompt_tokens + LLMCall.completion_tokens), 0).label("tokens"),
            func.coalesce(func.sum(LLMCall.cost), 0.0).label("cost"),
        ).where(LLMCall.session_id == session_id).group_by(LLMCall.model)
    )
    by_model = {}
    for row in by_model_result.all():
        by_model[row.model] = {
            "calls": row.calls,
            "tokens": row.tokens,
            "cost": round(row.cost, 6),
        }

    # By agent
    by_agent_result = await db.execute(
        select(
            LLMCall.agent_name,
            func.count(LLMCall.id).label("calls"),
            func.coalesce(func.sum(LLMCall.prompt_tokens + LLMCall.completion_tokens), 0).label("tokens"),
            func.coalesce(func.sum(LLMCall.cost), 0.0).label("cost"),
        ).where(LLMCall.session_id == session_id).group_by(LLMCall.agent_name)
    )
    by_agent = {}
    for row in by_agent_result.all():
        key = row.agent_name or "unknown"
        by_agent[key] = {
            "calls": row.calls,
            "tokens": row.tokens,
            "cost": round(row.cost, 6),
        }

    # Recent calls
    recent_result = await db.execute(
        select(LLMCall)
        .where(LLMCall.session_id == session_id)
        .order_by(LLMCall.created_at.desc())
        .limit(50)
    )
    recent_calls = [
        {
            "id": c.id,
            "agent_name": c.agent_name,
            "model": c.model,
            "provider": c.provider,
            "prompt_tokens": c.prompt_tokens,
            "completion_tokens": c.completion_tokens,
            "cost": c.cost,
            "duration_ms": c.duration_ms,
            "finish_reason": c.finish_reason,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        }
        for c in recent_result.scalars().all()
    ]

    return {
        "session_id": session_id,
        "session_title": session.title,
        "total_calls": total_row.total_calls,
        "prompt_tokens": total_row.prompt_tokens,
        "completion_tokens": total_row.completion_tokens,
        "total_tokens": total_row.prompt_tokens + total_row.completion_tokens,
        "total_cost_usd": round(total_row.total_cost, 6),
        "total_duration_ms": total_row.total_duration_ms,
        "by_provider": by_provider,
        "by_model": by_model,
        "by_agent": by_agent,
        "recent_calls": recent_calls,
    }
