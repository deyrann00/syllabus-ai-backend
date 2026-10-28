"""LLM access. `VllmProvider` talks to vLLM's OpenAI-compatible API; `FakeProvider` (app.ai.fake) is for tests/dev."""

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Protocol

import openai

from app.config import get_settings

log = logging.getLogger(__name__)


class LLMError(Exception):
    pass


class LLMProvider(Protocol):
    model_version: str

    async def complete_json(
        self,
        *,
        purpose: str,
        system: str,
        user: str,
        schema: dict[str, Any],
        meta: dict[str, Any] | None = None,
        run_id: uuid.UUID | None = None,
        trace: bool = True,
    ) -> dict[str, Any]: ...

    async def complete_text(
        self,
        *,
        purpose: str,
        system: str,
        user: str,
        meta: dict[str, Any] | None = None,
        trace: bool = True,
    ) -> str: ...


async def _save_trace(**fields: Any) -> None:
    from app.documents import LlmTrace

    try:
        await LlmTrace(**fields).insert()
    except Exception:  # tracing must never fail a check
        log.exception("failed to store LLM trace")


class VllmProvider:
    def __init__(self) -> None:
        s = get_settings()
        self.model = s.llm_model
        self.model_version = s.llm_model_version
        self.max_retries = s.llm_max_retries
        self.client = openai.AsyncOpenAI(
            base_url=s.llm_base_url, api_key=s.llm_api_key, timeout=s.llm_timeout_s, max_retries=0
        )

    async def _chat(
        self,
        *,
        purpose: str,
        messages: list[dict[str, str]],
        response_format: dict[str, Any] | None,
        run_id: uuid.UUID | None,
        trace: bool,
        parse: bool,
    ) -> Any:
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            started = time.monotonic()
            response_text: str | None = None
            usage = None
            try:
                async with _semaphore():
                    kwargs: dict[str, Any] = {"model": self.model, "messages": messages, "temperature": 0}
                    if response_format:
                        kwargs["response_format"] = response_format
                    completion = await self.client.chat.completions.create(**kwargs)
                response_text = completion.choices[0].message.content or ""
                usage = completion.usage
                result = json.loads(response_text) if parse else response_text
                error = None
            except (openai.APIError, json.JSONDecodeError) as exc:
                result, error, last_error = None, f"{type(exc).__name__}: {exc}", exc
            if trace:
                await _save_trace(
                    purpose=purpose,
                    run_id=run_id,
                    model=self.model,
                    messages=messages,
                    response=response_text,
                    prompt_tokens=getattr(usage, "prompt_tokens", None),
                    completion_tokens=getattr(usage, "completion_tokens", None),
                    duration_ms=int((time.monotonic() - started) * 1000),
                    error=error,
                )
            if error is None:
                return result
            log.warning("LLM %s attempt %s/%s failed: %s", purpose, attempt, self.max_retries, error)
            await asyncio.sleep(min(2**attempt, 20))
        raise LLMError(f"LLM call '{purpose}' failed after {self.max_retries} attempts: {last_error}")

    async def complete_json(
        self,
        *,
        purpose: str,
        system: str,
        user: str,
        schema: dict[str, Any],
        meta: dict[str, Any] | None = None,
        run_id: uuid.UUID | None = None,
        trace: bool = True,
    ) -> dict[str, Any]:
        response_format = {
            "type": "json_schema",
            "json_schema": {"name": purpose, "schema": schema, "strict": True},
        }
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        return await self._chat(
            purpose=purpose,
            messages=messages,
            response_format=response_format,
            run_id=run_id,
            trace=trace,
            parse=True,
        )

    async def complete_text(
        self,
        *,
        purpose: str,
        system: str,
        user: str,
        meta: dict[str, Any] | None = None,
        trace: bool = True,
    ) -> str:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        return await self._chat(
            purpose=purpose, messages=messages, response_format=None, run_id=None, trace=trace, parse=False
        )


# One semaphore per event loop: Celery tasks run each job in a fresh loop.
_semaphores: dict[int, asyncio.Semaphore] = {}


def _semaphore() -> asyncio.Semaphore:
    loop_id = id(asyncio.get_running_loop())
    if loop_id not in _semaphores:
        _semaphores.clear()
        _semaphores[loop_id] = asyncio.Semaphore(get_settings().llm_max_concurrency)
    return _semaphores[loop_id]


def get_llm() -> LLMProvider:
    if get_settings().llm_provider == "fake":
        from app.ai.fake import FakeProvider

        return FakeProvider()
    return VllmProvider()
