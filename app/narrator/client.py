"""The Claude API client of the narrator (PLAN 8.1).

One `AsyncAnthropic` for the whole bot, built from `ANTHROPIC_API_KEY`. The SDK's own retries are off
(`max_retries=0`): this module retries itself, exactly `retries` times and only for errors that can pass (timeout, a
dropped connection, rate limit, 5xx, overload). A bad key, a bad request or a refusal fail at once.

Request details checked against the API docs (platform.claude.com, Messages API, thinking, effort, prompt caching):

- `system` is a list of text blocks; a block with `cache_control: {"type": "ephemeral"}` is cached (5 minutes,
  refreshed by every hit). The first block of every request is the lore block, the same for the whole era.
- Sonnet 5.5 and Haiku 5.5 reject any `temperature`, `top_p` and `top_k`: this client never sends them.
- Both models think by default, and thinking counts against `max_tokens`. The short texts of the game do not need it:
  Sonnet 5.5 turns up-front thinking off with `{"type": "between_tools"}` (it rejects `disabled`), Haiku 5.5 with
  `{"type": "disabled"}`; `output_config.effort: "low"` keeps the rest cheap. Other models get no extras. If the API
  answers 400 to the extras, they are dropped for that model for good and the call is repeated once without them.
- The extras travel in `extra_body`, so the code does not depend on the SDK version knowing those parameters.
- `usage` carries `input_tokens` (uncached tail), `cache_creation_input_tokens`, `cache_read_input_tokens` and
  `output_tokens`; the budget module turns them into money.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import anthropic

log = logging.getLogger(__name__)

RETRY_REASONS = frozenset({"timeout", "connection", "rate_limit", "server"})
_BACKOFF_S = 0.7
_RETRY_AFTER_CAP_S = 4.0


class LlmError(Exception):
    """A call to Claude that gave no usable answer. `reason` is a short key for logs and the circuit breaker."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail

    @property
    def retryable(self) -> bool:
        return self.reason in RETRY_REASONS


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0  # uncached input after the last cache breakpoint
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0


@dataclass(frozen=True)
class Reply:
    text: str
    model: str
    usage: Usage
    stop_reason: str = ""
    attempts: int = 1

    @property
    def truncated(self) -> bool:
        return self.stop_reason == "max_tokens"


def request_extras(model: str) -> dict[str, Any]:
    """Parameters that keep a short answer short on the models that think by default."""
    name = model.lower()
    if "sonnet-5" in name:
        return {"thinking": {"type": "between_tools"}, "output_config": {"effort": "low"}}
    if "haiku-5" in name:
        return {"thinking": {"type": "disabled"}, "output_config": {"effort": "low"}}
    return {}


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _usage_of(resp: Any) -> Usage:
    u = getattr(resp, "usage", None)
    return Usage(
        _as_int(getattr(u, "input_tokens", 0)), _as_int(getattr(u, "output_tokens", 0)),
        _as_int(getattr(u, "cache_read_input_tokens", 0)), _as_int(getattr(u, "cache_creation_input_tokens", 0)),
    )


def _text_of(resp: Any) -> str:
    parts: list[str] = []
    for block in getattr(resp, "content", None) or []:
        if getattr(block, "type", None) == "text":
            parts.append(str(getattr(block, "text", "") or ""))
    return "".join(parts).strip()


def classify_error(exc: BaseException) -> LlmError:
    """Map an SDK exception to an `LlmError` with a short reason."""
    if isinstance(exc, LlmError):
        return exc
    if isinstance(exc, (asyncio.TimeoutError, anthropic.APITimeoutError)):
        return LlmError("timeout", type(exc).__name__)
    if isinstance(exc, anthropic.APIConnectionError):
        return LlmError("connection", str(exc)[:200])
    if isinstance(exc, anthropic.APIStatusError):
        code = int(getattr(exc, "status_code", 0) or 0)
        detail = f"{code} {str(exc)[:300]}"
        if code in (401, 403):
            return LlmError("auth", detail)
        if code == 429:
            return LlmError("rate_limit", detail)
        if code == 400:
            return LlmError("bad_request", detail)
        if code >= 500 or code == 529:
            return LlmError("server", detail)
        return LlmError("api", detail)
    return LlmError("api", f"{type(exc).__name__}: {str(exc)[:200]}")


def _retry_after(exc: BaseException) -> float:
    headers = getattr(getattr(exc, "response", None), "headers", None)
    try:
        value = float(headers.get("retry-after")) if headers is not None and headers.get("retry-after") else 0.0
    except (TypeError, ValueError):
        value = 0.0
    return min(max(value, _BACKOFF_S), _RETRY_AFTER_CAP_S)


class LlmClient:
    """Thin wrapper over `AsyncAnthropic.messages.create` with the game's timeout and retry rules."""

    def __init__(self, api_key: str, *, timeout: float = 30.0, retries: int = 1, sdk: Any | None = None) -> None:
        self.api_key = (api_key or "").strip()
        self.timeout = float(timeout)
        self.retries = max(0, int(retries))
        self._sdk = sdk  # a test double replaces the real client here
        self._no_extras: set[str] = set()

    @property
    def enabled(self) -> bool:
        return self._sdk is not None or bool(self.api_key)

    def _client(self) -> Any:
        if self._sdk is None:
            self._sdk = anthropic.AsyncAnthropic(api_key=self.api_key, max_retries=0, timeout=self.timeout)
        return self._sdk

    async def _once(
        self, *, model: str, system: list[dict[str, Any]], messages: list[dict[str, Any]], max_tokens: int,
        timeout: float, extras: dict[str, Any],
    ) -> Reply:
        kwargs: dict[str, Any] = {
            "model": model, "max_tokens": max_tokens, "system": system, "messages": messages, "timeout": timeout,
        }
        if extras:
            kwargs["extra_body"] = dict(extras)
        try:
            resp = await asyncio.wait_for(self._client().messages.create(**kwargs), timeout + 3.0)
        except Exception as exc:  # classified below; cancellation is not an Exception
            raise classify_error(exc) from exc
        stop = str(getattr(resp, "stop_reason", "") or "")
        text = _text_of(resp)
        usage = _usage_of(resp)
        if stop == "refusal":
            raise LlmError("refusal", "the model declined")
        if not text:
            raise LlmError("empty", f"no text, stop_reason={stop or '?'}")
        return Reply(text, model, usage, stop)

    async def complete(
        self, *, model: str, system: list[dict[str, Any]], messages: list[dict[str, Any]], max_tokens: int,
        timeout: float | None = None, retries: int | None = None,
    ) -> Reply:
        """One answer from Claude. Raises `LlmError`; the narrator turns that into a fallback text."""
        if not self.enabled:
            raise LlmError("disabled")
        wait = self.timeout if timeout is None else float(timeout)
        tries = 1 + (self.retries if retries is None else max(0, retries))
        attempt = 0
        last: LlmError | None = None
        while attempt < tries:
            attempt += 1
            extras = {} if model in self._no_extras else request_extras(model)
            try:
                try:
                    reply = await self._once(
                        model=model, system=system, messages=messages, max_tokens=max_tokens, timeout=wait, extras=extras,
                    )
                except LlmError as err:
                    if err.reason == "bad_request" and extras:
                        # the model does not take thinking/effort: forget them and repeat once, not counted as a retry
                        log.warning("narrator: %s refused the request extras (%s), repeating without them", model, err.detail)
                        self._no_extras.add(model)
                        reply = await self._once(
                            model=model, system=system, messages=messages, max_tokens=max_tokens, timeout=wait, extras={},
                        )
                    else:
                        raise
                return Reply(reply.text, reply.model, reply.usage, reply.stop_reason, attempt)
            except LlmError as err:
                last = err
                log.warning("narrator: call failed (attempt %d of %d): %s", attempt, tries, err)
                if not err.retryable or attempt >= tries:
                    break
                await asyncio.sleep(_BACKOFF_S if err.reason != "rate_limit" else _BACKOFF_S * 2)
        assert last is not None
        raise last
