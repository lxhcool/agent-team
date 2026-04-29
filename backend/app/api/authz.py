"""Authorization helpers for API endpoints."""

from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import decode_access_token
from app.models.models import PlanningSession, User


async def get_owned_planning_session(
    db: AsyncSession,
    session_id: str,
    user: User,
) -> PlanningSession:
    """Load a planning session and ensure it belongs to the current user."""
    session = await db.get(PlanningSession, session_id)
    if not session or session.user_id != user.id:
        raise HTTPException(status_code=404, detail="Planning session not found")
    return session


async def get_user_from_query_token(db: AsyncSession, token: Optional[str]) -> User:
    """Authenticate endpoints, such as SSE, that cannot send Bearer headers."""
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing token",
        )

    payload = decode_access_token(token)
    if payload is None or not payload.get("sub"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

    result = await db.execute(select(User).where(User.id == payload["sub"]))
    user = result.scalars().first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    return user
