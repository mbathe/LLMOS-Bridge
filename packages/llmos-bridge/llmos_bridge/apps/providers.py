"""LLM providers for the LLMOS App Language runtime.

Implements the LLMProvider protocol for real LLM backends.
Supports: Anthropic Claude, OpenAI GPT.

Also defines ``PROVIDER_CAPS`` — the single source of truth for which
parameters each provider accepts, which params are mutually exclusive, and
which params are required.  This registry is used by:
  - **Compiler** (``_validate_brain_params``) for static YAML validation
  - **Runtime** (``filter_params_for_provider``) to strip unsupported params
    before calling the LLM, so misconfigurations cause warnings, not crashes.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from .agent_runtime import LLMProvider

logger = logging.getLogger(__name__)


# ─── Provider capability registry ──────────────────────────────────


@dataclass(frozen=True)
class ProviderCaps:
    """Capabilities and parameter constraints for an LLM provider."""

    # Chat params this provider accepts (subset of the LLMProvider protocol)
    supported_params: frozenset[str] = frozenset()

    # Sets of params that cannot be used together (e.g. Anthropic: temperature + top_p)
    mutually_exclusive: tuple[frozenset[str], ...] = ()

    # Extra notes for compiler warnings
    notes: str = ""


PROVIDER_CAPS: dict[str, ProviderCaps] = {
    "anthropic": ProviderCaps(
        supported_params=frozenset({"temperature", "top_p", "max_tokens"}),
        mutually_exclusive=(frozenset({"temperature", "top_p"}),),
        notes="Claude does not allow temperature and top_p together",
    ),
    "openai": ProviderCaps(
        supported_params=frozenset({"temperature", "top_p", "max_tokens", "frequency_penalty", "presence_penalty"}),
    ),
    "ollama": ProviderCaps(
        supported_params=frozenset({"temperature", "top_p", "max_tokens"}),
    ),
    "bedrock": ProviderCaps(
        supported_params=frozenset({"temperature", "top_p", "max_tokens"}),
        mutually_exclusive=(frozenset({"temperature", "top_p"}),),
        notes="Bedrock Anthropic models share Claude's temperature/top_p constraint",
    ),
    "vertex": ProviderCaps(
        supported_params=frozenset({"temperature", "top_p", "max_tokens"}),
    ),
    "azure": ProviderCaps(
        supported_params=frozenset({"temperature", "top_p", "max_tokens", "frequency_penalty", "presence_penalty"}),
    ),
    "local": ProviderCaps(
        supported_params=frozenset({"temperature", "top_p", "max_tokens"}),
    ),
}

# Params that any provider might accept — union of all known supported_params.
ALL_KNOWN_PARAMS = frozenset().union(*(c.supported_params for c in PROVIDER_CAPS.values()))


# ─── Model context limits ──────────────────────────────────────


@dataclass(frozen=True)
class ModelLimits:
    """Known limits for a specific model."""
    context_window: int       # Total input+output context window (tokens)
    max_output: int           # Maximum output tokens

# Model limits — used to auto-configure context budget when the YAML
# doesn't specify model_context_window or sets it too high.
MODEL_LIMITS: dict[str, ModelLimits] = {
    # Anthropic Claude
    "claude-opus-4-6":             ModelLimits(context_window=200000, max_output=32000),
    "claude-sonnet-4-6":           ModelLimits(context_window=200000, max_output=16000),
    "claude-haiku-4-5-20251001":   ModelLimits(context_window=200000, max_output=8192),
    "claude-sonnet-4-20250514":    ModelLimits(context_window=200000, max_output=16000),
    # OpenAI
    "gpt-4o":                      ModelLimits(context_window=128000, max_output=16384),
    "gpt-4o-mini":                 ModelLimits(context_window=128000, max_output=16384),
    "gpt-4-turbo":                 ModelLimits(context_window=128000, max_output=4096),
    "o1":                          ModelLimits(context_window=200000, max_output=100000),
    "o3":                          ModelLimits(context_window=200000, max_output=100000),
    "o3-mini":                     ModelLimits(context_window=200000, max_output=100000),
}


def get_model_limits(model: str) -> ModelLimits | None:
    """Return known limits for a model, or None if unknown."""
    # Exact match first
    if model in MODEL_LIMITS:
        return MODEL_LIMITS[model]
    # Prefix match (e.g. "claude-sonnet-4-6" matches "claude-sonnet-4-*")
    for key, limits in MODEL_LIMITS.items():
        if model.startswith(key.rsplit("-", 1)[0]):
            return limits
    return None


def filter_params_for_provider(
    provider: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Return *params* filtered to only those supported by *provider*.

    - Unknown providers: pass through all params (don't break custom providers).
    - Known providers: strip unsupported params with a warning, resolve mutual
      exclusion conflicts (keep the first one specified).
    """
    caps = PROVIDER_CAPS.get(provider)
    if caps is None:
        return dict(params)  # unknown provider — don't filter

    filtered: dict[str, Any] = {}
    for key, value in params.items():
        if key not in caps.supported_params:
            logger.warning(
                "Provider '%s' does not support param '%s' — ignoring (value=%r)",
                provider, key, value,
            )
            continue
        filtered[key] = value

    # Resolve mutual exclusion: if both sides are present, keep the first.
    for excl_set in caps.mutually_exclusive:
        present = [k for k in filtered if k in excl_set]
        if len(present) > 1:
            keep = present[0]
            for drop in present[1:]:
                logger.warning(
                    "Provider '%s': params %s are mutually exclusive — "
                    "keeping '%s', dropping '%s'",
                    provider, sorted(excl_set), keep, drop,
                )
                del filtered[drop]

    return filtered


class AnthropicProvider(LLMProvider):
    """Anthropic Claude provider using the official SDK.

    Maps the LLMProvider protocol to the Anthropic Messages API,
    including tool_use support.
    """

    def __init__(
        self,
        *,
        api_key: str = "",
        model: str = "claude-sonnet-4-20250514",
    ):
        import anthropic

        self._model = model
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key or os.environ.get("ANTHROPIC_API_KEY", ""),
        )

    async def chat(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int = 4096,
        temperature: float | None = None,
        top_p: float | None = None,
    ) -> dict[str, Any]:
        """Send a chat request to Claude.

        Translates OpenAI-style tool format to Anthropic format and back.
        """
        # Convert messages to Anthropic format
        anthropic_messages = self._convert_messages(messages)

        # Convert tools to Anthropic format
        anthropic_tools = self._convert_tools(tools)

        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "messages": anthropic_messages,
        }
        if system:
            kwargs["system"] = system
        if anthropic_tools:
            kwargs["tools"] = anthropic_tools
        if temperature is not None:
            kwargs["temperature"] = temperature
        if top_p is not None:
            kwargs["top_p"] = top_p

        import asyncio
        import anthropic

        last_error = None
        for attempt in range(4):  # 1 initial + 3 retries
            try:
                response = await self._client.messages.create(**kwargs)
                return self._parse_response(response)
            except anthropic.RateLimitError as e:
                last_error = e
                # Parse retry-after header if available, else exponential backoff
                retry_after = getattr(e, "response", None)
                wait = None
                if retry_after and hasattr(retry_after, "headers"):
                    ra = retry_after.headers.get("retry-after")
                    if ra:
                        try:
                            wait = float(ra)
                        except ValueError:
                            pass
                if wait is None:
                    wait = min(2 ** attempt * 2, 60)  # 2s, 4s, 8s, capped at 60s
                logger.warning(
                    "Rate limited (attempt %d/4) — waiting %.1fs before retry",
                    attempt + 1, wait,
                )
                await asyncio.sleep(wait)

        raise last_error  # type: ignore[misc]

    async def close(self) -> None:
        await self._client.close()

    def _convert_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert OpenAI-style messages to Anthropic format."""
        result: list[dict[str, Any]] = []

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            tool_calls = msg.get("tool_calls")
            tool_call_id = msg.get("tool_call_id")

            if role == "system":
                continue  # system is handled separately

            if role == "assistant":
                if tool_calls:
                    # Assistant message with tool use
                    blocks: list[dict[str, Any]] = []
                    if content:
                        blocks.append({"type": "text", "text": content})
                    for tc in tool_calls:
                        func = tc.get("function", {})
                        args = func.get("arguments", "{}")
                        if isinstance(args, str):
                            try:
                                args = json.loads(args)
                            except json.JSONDecodeError:
                                args = {}
                        blocks.append({
                            "type": "tool_use",
                            "id": tc.get("id", ""),
                            "name": func.get("name", ""),
                            "input": args,
                        })
                    result.append({"role": "assistant", "content": blocks})
                else:
                    result.append({"role": "assistant", "content": content or ""})

            elif role == "tool":
                # Tool result — Anthropic uses role="user" with tool_result content
                result.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": tool_call_id or "",
                        "content": content or "",
                    }],
                })

            else:
                # User message
                result.append({"role": "user", "content": content or ""})

        # Anthropic requires alternating user/assistant — merge consecutive same-role
        return self._merge_consecutive(result)

    @staticmethod
    def _merge_consecutive(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Merge consecutive messages with the same role (Anthropic requirement)."""
        if not messages:
            return []

        merged: list[dict[str, Any]] = [messages[0]]
        for msg in messages[1:]:
            if msg["role"] == merged[-1]["role"]:
                # Merge content
                prev = merged[-1]["content"]
                curr = msg["content"]
                if isinstance(prev, str) and isinstance(curr, str):
                    merged[-1]["content"] = prev + "\n" + curr
                elif isinstance(prev, list) and isinstance(curr, list):
                    merged[-1]["content"] = prev + curr
                elif isinstance(prev, str) and isinstance(curr, list):
                    merged[-1]["content"] = [{"type": "text", "text": prev}] + curr
                elif isinstance(prev, list) and isinstance(curr, str):
                    merged[-1]["content"] = prev + [{"type": "text", "text": curr}]
            else:
                merged.append(msg)
        return merged

    @staticmethod
    def _convert_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert OpenAI-style tool defs to Anthropic format."""
        result = []
        for tool in tools:
            func = tool.get("function", {})
            result.append({
                "name": func.get("name", ""),
                "description": func.get("description", ""),
                "input_schema": func.get("parameters", {"type": "object", "properties": {}}),
            })
        return result

    @staticmethod
    def _parse_response(response: Any) -> dict[str, Any]:
        """Parse Anthropic response to the LLMProvider format."""
        text = ""
        tool_calls: list[dict[str, Any]] = []

        for block in response.content:
            if block.type == "text":
                text = block.text
            elif block.type == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "name": block.name,
                    "arguments": block.input,
                })

        # done = no tool calls (model wants to stop)
        is_done = response.stop_reason == "end_turn" and not tool_calls

        return {
            "text": text,
            "tool_calls": tool_calls,
            "done": is_done,
        }


class OpenAIProvider(LLMProvider):
    """OpenAI GPT provider using the official SDK.

    Supports GPT-4, GPT-4o, o1, o3, etc. with function calling.
    """

    def __init__(
        self,
        *,
        api_key: str = "",
        model: str = "gpt-4o",
        base_url: str = "",
    ):
        try:
            import openai
        except ImportError:
            raise ImportError(
                "openai package not installed. Install with: pip install openai"
            )

        kwargs: dict[str, Any] = {}
        if api_key or os.environ.get("OPENAI_API_KEY"):
            kwargs["api_key"] = api_key or os.environ.get("OPENAI_API_KEY", "")
        if base_url:
            kwargs["base_url"] = base_url

        self._model = model
        self._client = openai.AsyncOpenAI(**kwargs)

    async def chat(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int = 4096,
        temperature: float | None = None,
        top_p: float | None = None,
    ) -> dict[str, Any]:
        """Send a chat request to OpenAI."""
        openai_messages: list[dict[str, Any]] = []

        if system:
            openai_messages.append({"role": "system", "content": system})

        for msg in messages:
            openai_messages.append(msg)

        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": openai_messages,
            "max_tokens": max_tokens,
        }
        if tools:
            kwargs["tools"] = tools
        if temperature is not None:
            kwargs["temperature"] = temperature
        if top_p is not None:
            kwargs["top_p"] = top_p

        import asyncio

        last_error = None
        for attempt in range(4):  # 1 initial + 3 retries
            try:
                response = await self._client.chat.completions.create(**kwargs)
                return self._parse_response(response)
            except Exception as e:
                # Check for rate limit (OpenAI raises openai.RateLimitError)
                if "rate_limit" in type(e).__name__.lower() or "429" in str(e):
                    last_error = e
                    wait = min(2 ** attempt * 2, 60)
                    logger.warning(
                        "Rate limited (attempt %d/4) — waiting %.1fs before retry",
                        attempt + 1, wait,
                    )
                    await asyncio.sleep(wait)
                    continue
                raise

        raise last_error  # type: ignore[misc]

    async def close(self) -> None:
        await self._client.close()

    @staticmethod
    def _parse_response(response: Any) -> dict[str, Any]:
        """Parse OpenAI response to the LLMProvider format."""
        choice = response.choices[0]
        message = choice.message
        text = message.content or ""
        tool_calls: list[dict[str, Any]] = []

        if message.tool_calls:
            for tc in message.tool_calls:
                args = tc.function.arguments
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                tool_calls.append({
                    "id": tc.id,
                    "name": tc.function.name,
                    "arguments": args,
                })

        is_done = choice.finish_reason == "stop" and not tool_calls

        return {
            "text": text,
            "tool_calls": tool_calls,
            "done": is_done,
        }
