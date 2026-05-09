"""LLM Router - Unified model calling with provider routing, fallback, and retry.

Implements:
- L-007: Pre-call token estimation
- L-008: Cost budget check integration
- X-003: Stream continuation
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db, async_session
from app.core.security import decrypt_api_key
from app.models.models import ProviderConfig, LLMCall

logger = logging.getLogger(__name__)


@dataclass
class LLMMessage:
    role: str  # system, user, assistant
    content: str


@dataclass
class LLMCallResult:
    content: str
    model: str
    provider: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost: float = 0.0
    duration_ms: int = 0
    finish_reason: Optional[str] = None
    was_truncated: bool = False
    was_continued: bool = False


@dataclass
class ProviderInfo:
    name: str
    base_url: str
    api_key: str
    api_type: str = "openai_compatible"
    default_model: Optional[str] = None


class ProviderAdapter:
    """Adapter for calling LLM APIs using OpenAI-compatible protocol."""

    def __init__(self, provider: ProviderInfo):
        self.provider = provider

    async def complete(
        self,
        messages: list[LLMMessage],
        model: str,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        stream: bool = False,
        reasoning_effort: Optional[str] = None,
    ) -> LLMCallResult | AsyncIterator:
        """Call the LLM API in complete or stream mode."""
        start = time.time()

        if not self.provider.api_key:
            raise LLMError("API Key 未配置，请在设置中添加 LLM 服务的 API Key")

        url = f"{self.provider.base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.provider.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": stream,
        }
        selected_reasoning_effort = (reasoning_effort or "").strip().lower()
        if selected_reasoning_effort and selected_reasoning_effort != "default":
            payload["reasoning_effort"] = selected_reasoning_effort
        elif self._is_reasoning_model(model):
            # Xiaomi MiMo and similar reasoning models may spend a long time in
            # hidden reasoning before producing content. Prefer low effort for
            # product workflow latency; unsupported providers generally ignore it.
            payload["reasoning_effort"] = "low"

        if stream:
            return self._stream(url, headers, payload, model, start)

        timeout = httpx.Timeout(240.0, connect=15.0, read=240.0, write=30.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                resp = await client.post(url, headers=headers, json=payload)
            except httpx.HTTPError as e:
                raise LLMError(f"LLM request failed: {type(e).__name__}: {e}") from e
            duration_ms = int((time.time() - start) * 1000)

            if resp.status_code != 200:
                if resp.status_code == 400 and "reasoning_effort" in payload:
                    payload.pop("reasoning_effort", None)
                    try:
                        resp = await client.post(url, headers=headers, json=payload)
                    except httpx.HTTPError as e:
                        raise LLMError(f"LLM request failed: {type(e).__name__}: {e}") from e
                    duration_ms = int((time.time() - start) * 1000)
                    if resp.status_code == 200:
                        data = resp.json()
                    else:
                        raise LLMError(
                            f"LLM API error: {resp.status_code} - {resp.text[:500]}"
                        )
                else:
                    raise LLMError(
                        f"LLM API error: {resp.status_code} - {resp.text[:500]}"
                    )
            else:
                data = resp.json()

            try:
                choice = data["choices"][0]
            except (KeyError, IndexError, TypeError) as e:
                raise LLMError(
                    f"LLM API returned invalid response: {str(data)[:500]}"
                ) from e

            usage = data.get("usage", {})
            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)

            content = self._extract_choice_content(choice, data)
            was_truncated = choice.get("finish_reason") == "length"
            was_continued = False

            # P1-7: Compute actual cost based on model pricing
            cost = self._compute_cost(model, prompt_tokens, completion_tokens)

            if was_truncated:
                # Continue generation with the same context
                continuation_messages = list(messages)
                continuation_messages.append(LLMMessage(role="assistant", content=content))
                continuation_messages.append(LLMMessage(role="user", content="[继续]"))

                # Call again for continuation (max 3 times)
                for _ in range(3):
                    try:
                        cont_resp = await client.post(url, headers=headers, json={
                            "model": model,
                            "messages": [{"role": m.role, "content": m.content} for m in continuation_messages],
                            "max_tokens": max_tokens,
                            "temperature": temperature,
                            **({"reasoning_effort": payload["reasoning_effort"]} if "reasoning_effort" in payload else {}),
                        })
                    except httpx.HTTPError:
                        break
                    if cont_resp.status_code != 200:
                        break
                    cont_data = cont_resp.json()
                    cont_choice = cont_data["choices"][0]
                    cont_content = self._extract_choice_content(cont_choice, cont_data)
                    content += cont_content
                    was_continued = True

                    cont_usage = cont_data.get("usage", {})
                    prompt_tokens += cont_usage.get("prompt_tokens", 0)
                    completion_tokens += cont_usage.get("completion_tokens", 0)
                    # P1-7: Accumulate cost for continuation calls
                    cost += self._compute_cost(model, cont_usage.get("prompt_tokens", 0), cont_usage.get("completion_tokens", 0))

                    if cont_choice.get("finish_reason") != "length":
                        break

                    # Add continuation to context
                    continuation_messages.append(LLMMessage(role="assistant", content=cont_content))
                    continuation_messages.append(LLMMessage(role="user", content="[继续]"))

                duration_ms = int((time.time() - start) * 1000)

            return LLMCallResult(
                content=content,
                model=data.get("model", model),
                provider=self.provider.name,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost=cost,
                duration_ms=duration_ms,
                finish_reason=choice.get("finish_reason"),
                was_truncated=was_truncated,
                was_continued=was_continued,
            )

    async def _stream(self, url, headers, payload, model, start):
        """Stream responses from the LLM API.

        Yields content chunks as they arrive for real-time display.
        Handles: None content deltas, [DONE] marker, malformed data lines.
        """
        timeout = httpx.Timeout(90.0, connect=15.0, read=90.0, write=30.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                async with client.stream("POST", url, headers=headers, json=payload) as resp:
                    if resp.status_code != 200:
                        body = await resp.aread()
                        raise LLMError(f"LLM API error: {resp.status_code} - {body[:500]!r}")

                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        data_str = line[6:].strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            import json
                            data = json.loads(data_str)
                            choices = data.get("choices", [])
                            if not choices:
                                continue
                            choice = choices[0]
                            delta = choice.get("delta", {})
                            content = self._extract_stream_text(delta.get("content"))
                            if not content:
                                content = self._extract_stream_text(delta.get("text"))
                            if not content:
                                content = self._extract_stream_text(delta.get("output_text"))
                            if not content:
                                content = self._extract_stream_text(choice.get("message", {}).get("content"))
                            if not content:
                                content = self._extract_stream_text(choice.get("message", {}).get("text"))
                            if not content:
                                content = self._extract_stream_text(choice.get("text"))
                            if not content:
                                content = self._extract_stream_text(choice.get("content"))
                            if content:
                                yield {"type": "content", "content": content}
                                continue
                            reasoning_content = self._extract_stream_text(delta.get("reasoning_content"))
                            if not reasoning_content:
                                reasoning_content = self._extract_stream_text(delta.get("reasoning"))
                            if reasoning_content:
                                yield {"type": "reasoning", "content": reasoning_content}
                        except (json.JSONDecodeError, IndexError, KeyError):
                            continue
            except httpx.HTTPError as e:
                raise LLMError(f"LLM stream request failed: {type(e).__name__}: {e}") from e

    @staticmethod
    def _extract_stream_text(value) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            parts: list[str] = []
            for item in value:
                if isinstance(item, str):
                    parts.append(item)
                    continue
                if not isinstance(item, dict):
                    continue
                text = item.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
                    continue
                for key in ("content", "value", "output_text", "output", "answer"):
                    nested = item.get(key)
                    if isinstance(nested, str) and nested:
                        parts.append(nested)
                        break
                    if isinstance(nested, (list, dict)):
                        extracted = ProviderAdapter._extract_stream_text(nested)
                        if extracted:
                            parts.append(extracted)
                            break
            return "".join(parts)
        if isinstance(value, dict):
            for key in ("text", "content", "value", "output_text", "output", "answer"):
                nested = value.get(key)
                if isinstance(nested, str) and nested:
                    return nested
                if isinstance(nested, (list, dict)):
                    extracted = ProviderAdapter._extract_stream_text(nested)
                    if extracted:
                        return extracted
            for key in ("delta", "message"):
                nested = value.get(key)
                if isinstance(nested, (list, dict)):
                    extracted = ProviderAdapter._extract_stream_text(nested)
                    if extracted:
                        return extracted
        return ""

    @staticmethod
    def _extract_choice_content(choice, data=None) -> str:
        if not isinstance(choice, dict):
            return ""
        for candidate in (
            choice.get("message", {}).get("content") if isinstance(choice.get("message"), dict) else None,
            choice.get("message", {}).get("text") if isinstance(choice.get("message"), dict) else None,
            choice.get("text"),
            choice.get("content"),
            data.get("output_text") if isinstance(data, dict) else None,
        ):
            extracted = ProviderAdapter._extract_stream_text(candidate)
            if extracted:
                return extracted
        return ""

    @staticmethod
    def _is_reasoning_model(model: str) -> bool:
        model_lower = (model or "").lower()
        return model_lower.startswith("gpt-5") or any(keyword in model_lower for keyword in ("mimo", "reasoner", "thinking"))

    @staticmethod
    def _compute_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
        """P1-7: Compute actual cost for an LLM call based on model pricing."""
        pricing = DEFAULT_PRICING
        model_lower = model.lower()
        for key, price in MODEL_PRICING.items():
            if key in model_lower:
                pricing = price
                break
        input_cost = (prompt_tokens / 1000) * pricing["input"]
        output_cost = (completion_tokens / 1000) * pricing["output"]
        return round(input_cost + output_cost, 8)


class LLMError(Exception):
    """Custom exception for LLM-related errors."""
    pass


# P1-7: Model pricing table for cost calculation
MODEL_PRICING = {
    "gpt-4o": {"input": 0.0025, "output": 0.01},
    "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
    "gpt-4-turbo": {"input": 0.01, "output": 0.03},
    "gpt-4": {"input": 0.03, "output": 0.06},
    "gpt-3.5-turbo": {"input": 0.0005, "output": 0.0015},
    "claude-3-5-sonnet": {"input": 0.003, "output": 0.015},
    "claude-3-opus": {"input": 0.015, "output": 0.075},
    "claude-3-sonnet": {"input": 0.003, "output": 0.015},
    "claude-3-haiku": {"input": 0.00025, "output": 0.00125},
    "gemini-1.5-pro": {"input": 0.00125, "output": 0.005},
    "gemini-1.5-flash": {"input": 0.000075, "output": 0.0003},
    "deepseek-chat": {"input": 0.00014, "output": 0.00028},
    "deepseek-coder": {"input": 0.00014, "output": 0.00028},
}
DEFAULT_PRICING = {"input": 0.002, "output": 0.008}


class LLMRouter:
    """
    Routes LLM calls to the appropriate provider with fallback and retry.

    Design principles:
    - All providers use OpenAI-compatible Chat Completions protocol
    - Built-in providers (OpenAI/Anthropic/Google) get special adapter treatment
    - Custom providers (SiliconFlow, DeepSeek, etc.) work via base_url + api_key
    - Fallback chain: try primary, then backups on failure
    - Exponential backoff retry on transient errors (429, 500, 502, 503)
    """

    MAX_RETRIES = 3
    RETRYABLE_STATUS_CODES = {429, 500, 502, 503}

    def __init__(self):
        self._providers: dict[str, ProviderInfo] = {}

    async def load_providers(self, db: AsyncSession, user_id: Optional[str] = None):
        """Load enabled providers from database. If user_id given, loads only that user's providers."""
        query = select(ProviderConfig).where(ProviderConfig.enabled == True)
        if user_id:
            query = query.where(ProviderConfig.user_id == user_id)
        result = await db.execute(query)
        providers = result.scalars().all()

        # If user_id specified, only clear and reload for that user
        if user_id:
            # Remove existing providers for this user
            keys_to_remove = [k for k in self._providers if k.startswith(f"{user_id}_")]
            for k in keys_to_remove:
                del self._providers[k]
        else:
            self._providers.clear()

        for p in providers:
            api_key = ""
            if p.api_key_encrypted:
                try:
                    api_key = decrypt_api_key(p.api_key_encrypted)
                except Exception:
                    continue

            base_url = p.base_url
            if p.provider_name == "openai":
                base_url = base_url or "https://api.openai.com/v1"
            elif p.provider_name == "anthropic":
                base_url = base_url or "https://api.anthropic.com/v1"

            self._providers[p.provider_name] = ProviderInfo(
                name=p.provider_name,
                base_url=base_url,
                api_key=api_key,
                api_type=p.api_type,
                default_model=p.default_model,
            )

    def get_provider(self, provider_name: str) -> Optional[ProviderInfo]:
        return self._providers.get(provider_name)

    async def call(
        self,
        messages: list[LLMMessage],
        model: str,
        provider_name: Optional[str] = None,
        fallback_chain: Optional[list[str]] = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        session_id: Optional[str] = None,
        session_type: str = "planning",
        agent_name: Optional[str] = None,
        budget_usd: Optional[float] = None,
        reasoning_effort: Optional[str] = None,
    ) -> LLMCallResult:
        """
        Call an LLM with automatic provider routing, retry, and fallback.

        Implements L-007 (token estimation) and L-008 (budget check).

        Args:
            messages: List of chat messages
            model: Model name to use
            provider_name: Explicit provider to use (optional)
            fallback_chain: List of "provider/model" pairs for fallback
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature
            session_id: Session ID for logging
            session_type: Session type for logging
            agent_name: Agent name for logging
            budget_usd: Override budget (if None, uses session budget from settings)

        Returns:
            LLMCallResult with the response

        Raises:
            LLMError: If all providers fail or budget exceeded
        """
        # L-007: Pre-call token estimation
        from app.services.token_estimation import estimate_call, should_compress_context
        token_estimate = estimate_call(messages, model, max_tokens)
        if token_estimate.exceeds_limit:
            # Log warning but don't block - let the API handle it
            import logging
            logging.getLogger(__name__).warning(
                f"Token estimate exceeds limit for model {model}: "
                f"prompt={token_estimate.estimated_prompt_tokens}, "
                f"limit={token_estimate.model_token_limit}, "
                f"available={token_estimate.available_for_completion}, "
                f"requested={max_tokens}"
            )

        # L-008: Budget check (integrated into call chain)
        if session_id:
            from app.services.token_estimation import check_session_budget
            budget_allowed, budget_reason = await check_session_budget(
                session_id, model, token_estimate.estimated_prompt_tokens, max_tokens
            )
            if not budget_allowed:
                raise LLMError(budget_reason)

        # Check session budget if specified (legacy path)
        if budget_usd is not None and session_id:
            async with async_session() as db:
                from sqlalchemy import func as sa_func
                result = await db.execute(
                    select(sa_func.sum(LLMCall.cost)).where(LLMCall.session_id == session_id)
                )
                total_cost = result.scalar() or 0.0
                if total_cost >= budget_usd:
                    raise LLMError("Session budget exceeded")

        # Build the list of providers to try
        attempts: list[tuple[str, str]] = []  # (provider_name, model)

        if provider_name:
            attempts.append((provider_name, model))
        else:
            # Try to find which provider has this model
            for pname, pinfo in self._providers.items():
                if pinfo.default_model == model or not provider_name:
                    attempts.append((pname, model))

        # Add fallback chain
        if fallback_chain:
            for entry in fallback_chain:
                parts = entry.split("/", 1)
                if len(parts) == 2:
                    attempts.append((parts[0], parts[1]))

        last_error = None
        for provider_name, model_name in attempts:
            provider = self._providers.get(provider_name)
            if not provider:
                continue

            adapter = ProviderAdapter(provider)

            # Retry with exponential backoff
            for attempt in range(self.MAX_RETRIES):
                try:
                    result = await adapter.complete(
                        messages=messages,
                        model=model_name,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        reasoning_effort=reasoning_effort,
                    )
                    # Log successful call
                    if session_id:
                        try:
                            await self._log_call(
                                session_id=session_id,
                                session_type=session_type,
                                agent_name=agent_name,
                                result=result,
                            )
                        except Exception as log_exc:
                            logger.warning(
                                "llm call succeeded but usage logging failed: session=%s provider=%s model=%s reason=%s",
                                session_id,
                                result.provider,
                                result.model,
                                log_exc,
                            )
                    return result
                except LLMError as e:
                    last_error = e
                    # Check if retryable
                    should_retry = any(
                        f"{code}" in str(e) for code in self.RETRYABLE_STATUS_CODES
                    )
                    if should_retry and attempt < self.MAX_RETRIES - 1:
                        wait = 2 ** attempt  # 1s, 2s, 4s
                        await asyncio.sleep(wait)
                        continue
                    break  # Non-retryable error, try next provider

        raise LLMError(f"All providers failed. Last error: {last_error}")

    async def _log_call(self, session_id, session_type, agent_name, result: LLMCallResult):
        """Log an LLM call to the database."""
        async with async_session() as db:
            call = LLMCall(
                session_type=session_type,
                session_id=session_id,
                agent_name=agent_name,
                model=result.model,
                provider=result.provider,
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                cost=result.cost,
                duration_ms=result.duration_ms,
                finish_reason=result.finish_reason,
                was_truncated=result.was_truncated,
                was_continued=result.was_continued,
            )
            db.add(call)
            await db.commit()


# Global singleton
llm_router = LLMRouter()
