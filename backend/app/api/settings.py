"""Settings API endpoints for model configuration, providers, and usage."""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.database import get_db
from app.core.security import encrypt_api_key, decrypt_api_key, mask_api_key
from app.models.models import ProviderConfig, ModelSettings, User

router = APIRouter()


# ===== Schemas =====

class ModelPricing(BaseModel):
    prompt_per_million: float = 0.0
    completion_per_million: float = 0.0
    currency: str = "USD"


class ModelConfig(BaseModel):
    model_id: str
    display_name: str
    context_window: int = 32768
    pricing: Optional[ModelPricing] = None


class AddProviderRequest(BaseModel):
    provider_name: str = Field(..., min_length=1, max_length=100)
    display_name: str = Field(..., min_length=1, max_length=200)
    base_url: str = Field(..., min_length=1)
    api_key: Optional[str] = None
    api_type: str = Field(default="openai_compatible")
    models: List[ModelConfig] = Field(default_factory=list)
    default_model: Optional[str] = None


class UpdateProviderRequest(BaseModel):
    display_name: Optional[str] = None
    base_url: Optional[str] = None
    api_type: Optional[str] = None
    models: Optional[List[ModelConfig]] = None
    default_model: Optional[str] = None
    enabled: Optional[bool] = None


class SetApiKeyRequest(BaseModel):
    api_key: str = Field(..., min_length=1)


class UpdateModelSettingsRequest(BaseModel):
    default_model: Optional[str] = None
    planning_model: Optional[str] = None
    execution_model: Optional[str] = None
    fallback_chain: Optional[List[str]] = None
    session_budget_usd: Optional[float] = None


class ProviderResponse(BaseModel):
    provider_name: str
    display_name: str
    api_type: str
    base_url: Optional[str] = None
    has_api_key: bool
    masked_api_key: Optional[str] = None
    models: List[ModelConfig] = []
    default_model: Optional[str] = None
    is_builtin: bool
    enabled: bool


class ModelSettingsResponse(BaseModel):
    default_model: Optional[str] = None
    planning_model: Optional[str] = None
    execution_model: Optional[str] = None
    fallback_chain: List[str] = []
    session_budget_usd: float = 10.0


# ===== Provider Endpoints =====

@router.get("/models")
async def get_model_settings(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get current model settings and provider list for the authenticated user."""
    import json

    result = await db.execute(
        select(ProviderConfig).where(ProviderConfig.user_id == user.id)
    )
    providers = result.scalars().all()

    settings_result = await db.execute(
        select(ModelSettings).where(ModelSettings.user_id == user.id)
    )
    settings = settings_result.scalars().first()

    provider_responses = []
    for p in providers:
        masked = None
        has_key = bool(p.api_key_encrypted)
        if has_key:
            try:
                masked = mask_api_key(decrypt_api_key(p.api_key_encrypted))
            except Exception:
                masked = "***"

        models = []
        if p.models_json:
            try:
                models = json.loads(p.models_json)
            except Exception:
                pass

        provider_responses.append({
            "provider_name": p.provider_name,
            "display_name": p.display_name,
            "api_type": p.api_type,
            "base_url": p.base_url,
            "has_api_key": has_key,
            "masked_api_key": masked,
            "models": models,
            "default_model": p.default_model,
            "is_builtin": p.is_builtin,
            "enabled": p.enabled,
        })

    fallback = []
    if settings and settings.fallback_chain_json:
        try:
            fallback = json.loads(settings.fallback_chain_json)
        except Exception:
            pass

    return {
        "providers": provider_responses,
        "settings": {
            "default_model": settings.default_model if settings else None,
            "planning_model": settings.planning_model if settings else None,
            "execution_model": settings.execution_model if settings else None,
            "fallback_chain": fallback,
            "session_budget_usd": settings.session_budget_usd if settings else 10.0,
        },
    }


@router.put("/models")
async def update_model_settings(
    req: UpdateModelSettingsRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Update global model settings for the authenticated user."""
    import json

    result = await db.execute(
        select(ModelSettings).where(ModelSettings.user_id == user.id)
    )
    settings = result.scalars().first()

    if not settings:
        settings = ModelSettings(id="default", user_id=user.id)
        db.add(settings)

    if req.default_model is not None:
        settings.default_model = req.default_model
    if req.planning_model is not None:
        settings.planning_model = req.planning_model
    if req.execution_model is not None:
        settings.execution_model = req.execution_model
    if req.fallback_chain is not None:
        settings.fallback_chain_json = json.dumps(req.fallback_chain)
    if req.session_budget_usd is not None:
        settings.session_budget_usd = req.session_budget_usd

    await db.commit()
    return {"status": "updated"}


@router.post("/models/test")
async def test_provider_connection(
    provider_name: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Test if a provider's API key and base URL are valid."""
    provider = await db.execute(
        select(ProviderConfig).where(
            ProviderConfig.provider_name == provider_name,
            ProviderConfig.user_id == user.id,
        )
    )
    p = provider.scalars().first()
    if not p:
        raise HTTPException(status_code=404, detail="Provider not found")

    if not p.api_key_encrypted:
        return {"success": False, "error": "No API key configured"}

    try:
        api_key = decrypt_api_key(p.api_key_encrypted)
    except Exception:
        return {"success": False, "error": "Failed to decrypt API key"}

    import httpx
    base_url = p.base_url or "https://api.openai.com/v1"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{base_url.rstrip('/')}/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if resp.status_code == 200:
                return {"success": True, "message": "Connection successful"}
            else:
                return {
                    "success": False,
                    "error": f"API returned status {resp.status_code}",
                }
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/models/providers")
async def add_provider(
    req: AddProviderRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Add a custom LLM provider for the authenticated user."""
    import json

    # Provider name is scoped per user: {user_id}_{name}
    full_provider_name = f"{user.id}_{req.provider_name}"
    existing = await db.execute(
        select(ProviderConfig).where(
            ProviderConfig.provider_name == full_provider_name,
            ProviderConfig.user_id == user.id,
        )
    )
    if existing.scalars().first():
        raise HTTPException(status_code=400, detail="Provider already exists")

    # Auto-fetch models from provider API if api_key is given and no models specified
    fetched_models = []
    default_model = req.default_model
    if req.api_key and not req.models:
        try:
            import httpx
            base_url = req.base_url or "https://api.openai.com/v1"
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{base_url.rstrip('/')}/models",
                    headers={"Authorization": f"Bearer {req.api_key}"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    raw_models = data.get("data", [])
                    # Filter to chat-compatible models, sort by id
                    for m in sorted(raw_models, key=lambda x: x.get("id", "")):
                        mid = m.get("id", "")
                        # Skip embedding/image/tts/whisper models
                        skip_kw = ["embed", "dall", "tts", "whisper", "audio", "davinci", "babbage", "curie"]
                        if any(kw in mid.lower() for kw in skip_kw):
                            continue
                        fetched_models.append({
                            "model_id": mid,
                            "display_name": mid,
                            "context_window": 32768,
                        })
                    # Auto-set default_model to first chat model
                    if not default_model and fetched_models:
                        default_model = fetched_models[0]["model_id"]
        except Exception:
            pass  # Non-fatal: provider is still created, user can fetch models later

    models_json = None
    if req.models:
        models_json = json.dumps([m.dict() for m in req.models])
    elif fetched_models:
        models_json = json.dumps(fetched_models)

    provider = ProviderConfig(
        user_id=user.id,
        provider_name=full_provider_name,
        display_name=req.display_name,
        api_type=req.api_type,
        base_url=req.base_url,
        api_key_encrypted=encrypt_api_key(req.api_key) if req.api_key else None,
        models_json=models_json,
        default_model=default_model,
        is_builtin=False,
        enabled=True,
    )
    db.add(provider)
    await db.commit()
    return {
        "status": "created",
        "provider_name": req.provider_name,
        "models_fetched": len(fetched_models),
        "default_model": default_model,
    }


@router.put("/models/providers/{provider_name}")
async def update_provider(
    provider_name: str,
    req: UpdateProviderRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Update a custom provider configuration."""
    import json

    result = await db.execute(
        select(ProviderConfig).where(
            ProviderConfig.provider_name == provider_name,
            ProviderConfig.user_id == user.id,
        )
    )
    provider = result.scalars().first()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    if req.display_name is not None:
        provider.display_name = req.display_name
    if req.base_url is not None:
        provider.base_url = req.base_url
    if req.api_type is not None:
        provider.api_type = req.api_type
    if req.models is not None:
        provider.models_json = json.dumps([m.dict() for m in req.models])
    if req.default_model is not None:
        provider.default_model = req.default_model
    if req.enabled is not None:
        provider.enabled = req.enabled

    await db.commit()
    return {"status": "updated"}


@router.delete("/models/providers/{provider_name}")
async def delete_provider(
    provider_name: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Delete a custom provider (built-in providers cannot be deleted)."""
    result = await db.execute(
        select(ProviderConfig).where(
            ProviderConfig.provider_name == provider_name,
            ProviderConfig.user_id == user.id,
        )
    )
    provider = result.scalars().first()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")
    if provider.is_builtin:
        raise HTTPException(status_code=400, detail="Cannot delete built-in providers")

    await db.delete(provider)
    await db.commit()
    return {"status": "deleted"}


@router.put("/models/providers/{provider_name}/api-key")
async def set_provider_api_key(
    provider_name: str,
    req: SetApiKeyRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Set or update a provider's API key (encrypted storage)."""
    result = await db.execute(
        select(ProviderConfig).where(
            ProviderConfig.provider_name == provider_name,
            ProviderConfig.user_id == user.id,
        )
    )
    provider = result.scalars().first()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    provider.api_key_encrypted = encrypt_api_key(req.api_key)
    await db.commit()
    return {"status": "updated", "masked_key": mask_api_key(req.api_key)}


@router.get("/models/providers/{provider_name}/models")
async def get_provider_models(
    provider_name: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Fetch available models from a provider's API."""
    result = await db.execute(
        select(ProviderConfig).where(
            ProviderConfig.provider_name == provider_name,
            ProviderConfig.user_id == user.id,
        )
    )
    provider = result.scalars().first()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    if not provider.api_key_encrypted:
        raise HTTPException(status_code=400, detail="No API key configured")

    api_key = decrypt_api_key(provider.api_key_encrypted)
    base_url = provider.base_url or "https://api.openai.com/v1"

    import httpx
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{base_url.rstrip('/')}/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if resp.status_code == 200:
                data = resp.json()
                models = data.get("data", [])
                return {
                    "models": [
                        {"id": m.get("id", ""), "name": m.get("id", "")}
                        for m in models
                    ]
                }
            else:
                raise HTTPException(
                    status_code=resp.status_code,
                    detail=f"Provider API returned {resp.status_code}",
                )
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=str(e))
