"""OpenAI image generation helpers for workspace artifacts."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decrypt_api_key
from app.models.models import ModelSettings, ProviderConfig


OPENAI_IMAGE_MODEL_CANDIDATES = (
    "gpt-image-2",
    "gpt-image-1.5",
    "gpt-image-1",
    "gpt-image-1-mini",
)


class ImageGenerationError(Exception):
    """Raised when workspace image generation cannot proceed."""


@dataclass
class ImageProviderSelection:
    provider_name: str
    model: str
    api_key: str
    base_url: str


@dataclass
class GeneratedImageResult:
    content: bytes
    mime_type: str
    model: str
    provider: str
    revised_prompt: Optional[str] = None


def _load_provider_models(provider: ProviderConfig) -> list[str]:
    if not provider.models_json:
        return []
    try:
        raw = json.loads(provider.models_json)
    except json.JSONDecodeError:
        return []
    if not isinstance(raw, list):
        return []

    models: list[str] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        value = item.get("model_id") or item.get("id")
        if isinstance(value, str) and value.strip():
            models.append(value.strip())
    return models


def _looks_like_image_model(model: Optional[str]) -> bool:
    value = (model or "").strip().lower()
    if not value:
        return False
    return value.startswith("gpt-image-") or "image" in value


def _resolve_model_reference(
    model_ref: Optional[str],
    providers: list[ProviderConfig],
) -> tuple[Optional[str], Optional[str]]:
    if not model_ref:
        return None, None
    for provider in providers:
        prefix = f"{provider.provider_name}/"
        if model_ref.startswith(prefix):
            return model_ref[len(prefix):], provider.provider_name
    for provider in providers:
        if provider.default_model == model_ref:
            return model_ref, provider.provider_name
    return model_ref, None


def _provider_supports_image_generation(provider: ProviderConfig) -> bool:
    if _looks_like_image_model(provider.default_model):
        return True
    return any(_looks_like_image_model(model) for model in _load_provider_models(provider))


def _pick_image_model(provider: ProviderConfig, preferred_model: Optional[str] = None) -> str:
    if _looks_like_image_model(preferred_model):
        model = str(preferred_model).strip()
        if model.startswith("gpt-image-1"):
            return "gpt-image-2"
        return model

    available = _load_provider_models(provider)
    for candidate in OPENAI_IMAGE_MODEL_CANDIDATES:
        if candidate in available:
            if candidate.startswith("gpt-image-1"):
                return "gpt-image-2"
            return candidate

    default_model = (provider.default_model or "").strip()
    if default_model.startswith("gpt-image-"):
        if default_model.startswith("gpt-image-1"):
            return "gpt-image-2"
        return default_model

    return "gpt-image-2"


async def resolve_openai_image_provider(
    db: AsyncSession,
    user_id: str,
) -> ImageProviderSelection:
    provider_result = await db.execute(
        select(ProviderConfig).where(
            ProviderConfig.user_id == user_id,
            ProviderConfig.enabled == True,
        )
    )
    providers = list(provider_result.scalars().all())
    settings_result = await db.execute(
        select(ModelSettings).where(ModelSettings.user_id == user_id)
    )
    settings = settings_result.scalars().first()

    candidates = [
        provider for provider in providers
        if provider.api_key_encrypted
        and provider.api_type == "openai_compatible"
    ]

    if not candidates:
        raise ImageGenerationError("未找到可用的 OpenAI-compatible Provider。请先在模型配置里启用可用 Provider 并填写 API Key。")

    preferred_provider = None
    preferred_model = None
    if settings:
        for model_ref in (settings.execution_model, settings.default_model, settings.planning_model):
            model_name, provider_name = _resolve_model_reference(model_ref, providers)
            if provider_name:
                preferred_provider = provider_name
                preferred_model = model_name
                break

    provider = None
    if preferred_provider:
        provider = next(
            (item for item in candidates if item.provider_name == preferred_provider),
            None,
        )

    if not provider:
        provider = next(
            (item for item in candidates if _provider_supports_image_generation(item)),
            None,
        )

    if not provider:
        provider = candidates[0]
    try:
        api_key = decrypt_api_key(provider.api_key_encrypted or "")
    except Exception as exc:
        raise ImageGenerationError(f"Provider {provider.provider_name} 的 API Key 无法解密，请重新配置。") from exc

    if not api_key:
        raise ImageGenerationError(f"Provider {provider.provider_name} 缺少可用的 API Key。")

    base_url = (provider.base_url or "https://api.openai.com/v1").rstrip("/")
    if "api.openai.com" not in base_url:
        raise ImageGenerationError(
            f"Provider {provider.display_name or provider.provider_name} 不是 OpenAI 官方图片接口，跳过图片生成并改用 HTML 设计稿。"
        )

    return ImageProviderSelection(
        provider_name=provider.provider_name,
        model=_pick_image_model(provider, preferred_model if provider.provider_name == preferred_provider else None),
        api_key=api_key,
        base_url=base_url,
    )


async def generate_openai_image(
    selection: ImageProviderSelection,
    prompt: str,
    *,
    size: str,
    quality: str = "high",
    output_format: str = "png",
) -> GeneratedImageResult:
    payload = {
        "model": selection.model,
        "prompt": prompt,
        "size": size,
        "quality": quality,
        "output_format": output_format,
    }
    headers = {
        "Authorization": f"Bearer {selection.api_key}",
        "Content-Type": "application/json",
    }

    timeout = httpx.Timeout(180.0, connect=20.0, read=180.0, write=60.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            response = await client.post(
                f"{selection.base_url}/images/generations",
                headers=headers,
                json=payload,
            )
        except httpx.HTTPError as exc:
            raise ImageGenerationError(f"设计稿图片生成请求失败: {type(exc).__name__}: {exc}") from exc

    if response.status_code != 200:
        raise ImageGenerationError(
            f"设计稿图片生成失败: {response.status_code} - {response.text[:500]}"
        )

    try:
        data = response.json()
    except json.JSONDecodeError as exc:
        raise ImageGenerationError("图片生成接口返回了不可解析的响应。") from exc

    items = data.get("data")
    if not isinstance(items, list) or not items:
        raise ImageGenerationError("图片生成接口未返回有效图片数据。")

    first = items[0] if isinstance(items[0], dict) else {}
    b64_data = first.get("b64_json")
    if not isinstance(b64_data, str) or not b64_data:
        raise ImageGenerationError("图片生成接口未返回 b64 图片内容。")

    try:
        content = base64.b64decode(b64_data)
    except Exception as exc:
        raise ImageGenerationError("图片内容解码失败。") from exc

    return GeneratedImageResult(
        content=content,
        mime_type=f"image/{output_format}",
        model=str(data.get("model") or selection.model),
        provider=selection.provider_name,
        revised_prompt=first.get("revised_prompt") if isinstance(first.get("revised_prompt"), str) else None,
    )
