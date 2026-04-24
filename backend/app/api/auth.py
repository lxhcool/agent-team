"""Authentication API: register, login, get current user."""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import (
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)
from app.core.database import get_db
from app.models.models import User

router = APIRouter()

logger = logging.getLogger(__name__)


# ===== Schemas =====

class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=50, pattern=r"^[a-zA-Z0-9_-]+$")
    email: EmailStr
    password: str = Field(..., min_length=6, max_length=128)
    display_name: str = Field(default="", max_length=100)


class LoginRequest(BaseModel):
    username: str
    password: str


class UserResponse(BaseModel):
    id: str
    username: str
    email: str
    display_name: str
    role: str
    created_at: str


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


# ===== Endpoints =====

@router.post("/auth/register", response_model=AuthResponse)
async def register(req: RegisterRequest, db: AsyncSession = Depends(get_db)):
    """Register a new user account."""
    # Check username uniqueness
    existing = await db.execute(
        select(User).where(User.username == req.username)
    )
    if existing.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="用户名已被使用",
        )

    # Check email uniqueness
    existing = await db.execute(
        select(User).where(User.email == req.email)
    )
    if existing.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="邮箱已被注册",
        )

    # Create user
    user = User(
        username=req.username,
        email=req.email,
        password_hash=hash_password(req.password),
        display_name=req.display_name or req.username,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    # Create default built-in providers for this user
    await _create_default_providers(user.id, db)

    token = create_access_token(user.id, user.username)
    return AuthResponse(
        access_token=token,
        user=UserResponse(
            id=user.id,
            username=user.username,
            email=user.email,
            display_name=user.display_name,
            role=user.role.value,
            created_at=user.created_at.isoformat(),
        ),
    )


@router.post("/auth/login", response_model=AuthResponse)
async def login(req: LoginRequest, db: AsyncSession = Depends(get_db)):
    """Login with username and password."""
    # Find user by username or email
    result = await db.execute(
        select(User).where(
            (User.username == req.username) | (User.email == req.username)
        )
    )
    user = result.scalars().first()

    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="账户已被禁用",
        )

    token = create_access_token(user.id, user.username)
    return AuthResponse(
        access_token=token,
        user=UserResponse(
            id=user.id,
            username=user.username,
            email=user.email,
            display_name=user.display_name,
            role=user.role.value,
            created_at=user.created_at.isoformat(),
        ),
    )


@router.get("/auth/me", response_model=UserResponse)
async def get_me(user: User = Depends(get_current_user)):
    """Get current authenticated user info."""
    return UserResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        display_name=user.display_name,
        role=user.role.value,
        created_at=user.created_at.isoformat(),
    )


async def _create_default_providers(user_id: str, db: AsyncSession):
    """Create default built-in providers for a new user (without API keys)."""
    from app.models.models import ProviderConfig
    import json

    defaults = [
        {
            "provider_name": "openai",
            "display_name": "OpenAI",
            "base_url": "https://api.openai.com/v1",
            "models": [
                {"model_id": "gpt-4o", "display_name": "GPT-4o", "context_window": 128000},
                {"model_id": "gpt-4o-mini", "display_name": "GPT-4o Mini", "context_window": 128000},
                {"model_id": "gpt-4-turbo", "display_name": "GPT-4 Turbo", "context_window": 128000},
            ],
            "default_model": "gpt-4o-mini",
        },
        {
            "provider_name": "deepseek",
            "display_name": "DeepSeek",
            "base_url": "https://api.deepseek.com/v1",
            "models": [
                {"model_id": "deepseek-chat", "display_name": "DeepSeek Chat", "context_window": 64000},
                {"model_id": "deepseek-reasoner", "display_name": "DeepSeek Reasoner", "context_window": 64000},
            ],
            "default_model": "deepseek-chat",
        },
    ]

    for d in defaults:
        provider = ProviderConfig(
            user_id=user_id,
            provider_name=f"{user_id}_{d['provider_name']}",
            display_name=d["display_name"],
            api_type="openai_compatible",
            base_url=d["base_url"],
            models_json=json.dumps(d["models"]),
            default_model=d["default_model"],
            is_builtin=True,
            enabled=True,
        )
        db.add(provider)

    await db.commit()
