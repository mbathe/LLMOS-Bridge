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
    # Ollama / local models
    "qwen2.5:14b-instruct-q4_K_M": ModelLimits(context_window=32768, max_output=8192),
    "qwen2.5-coder:7b":            ModelLimits(context_window=32768, max_output=8192),
    "llama3.1:8b":                 ModelLimits(context_window=131072, max_output=8192),
    "llama3.1:latest":             ModelLimits(context_window=131072, max_output=8192),
    "llama3.2:latest":             ModelLimits(context_window=131072, max_output=8192),
    "mistral-nemo:latest":         ModelLimits(context_window=131072, max_output=8192),
    "gemma3:4b":                   ModelLimits(context_window=131072, max_output=8192),
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
        """Parse OpenAI response to the LLMProvider format.

        Handles two tool-call styles:
        1. Structured ``tool_calls`` field (GPT, llama3.1, mistral-nemo…)
        2. JSON tool call emitted as plain text in ``content``
           (qwen2.5-coder, deepseek-coder, and other models that don't
           use the native tool_calls wire format).  We detect JSON objects
           containing ``"name"`` + ``"arguments"`` keys and promote them.

        Also detects ``finish_reason == "length"`` (response truncated at
        max_tokens) and injects an error message so the agent can adapt
        (e.g. retry with shorter content).
        """
        choice = response.choices[0]
        message = choice.message
        text = message.content or ""
        tool_calls: list[dict[str, Any]] = []
        truncated = choice.finish_reason == "length"

        # ── 1. Native structured tool_calls ──
        if message.tool_calls:
            for tc in message.tool_calls:
                args = tc.function.arguments
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        if truncated:
                            # Response was cut at max_tokens — the JSON is
                            # incomplete.  Skip this tool call entirely and
                            # let the agent know below.
                            logger.warning(
                                "Truncated tool call %s (finish_reason=length) "
                                "— JSON args incomplete, skipping",
                                tc.function.name,
                            )
                            continue
                        # Non-truncated malformed JSON — use empty args so the
                        # module validator reports the missing required fields.
                        logger.warning(
                            "Malformed JSON args for tool call %s — using empty args",
                            tc.function.name,
                        )
                        args = {}
                tool_calls.append({
                    "id": tc.id,
                    "name": tc.function.name,
                    "arguments": args,
                })

        # ── 2. Fallback: extract tool calls from text content ──
        if not tool_calls and text.strip() and not truncated:
            extracted = OpenAIProvider._extract_tool_calls_from_text(text)
            if extracted:
                tool_calls = extracted
                # Clear text so the agent loop doesn't echo raw JSON
                text = ""

        # ── 3. Handle truncation ──
        if truncated:
            warning = (
                "[SYSTEM: Your previous response was truncated because it "
                "exceeded max_tokens. Some tool calls may have been lost. "
                "Please retry with shorter content — split large file writes "
                "into smaller chunks or reduce the number of simultaneous "
                "tool calls.]"
            )
            if text:
                text = text + "\n\n" + warning
            else:
                text = warning
            logger.warning(
                "Response truncated (finish_reason=length): %d tool calls "
                "survived, injecting retry hint",
                len(tool_calls),
            )

        is_done = choice.finish_reason == "stop" and not tool_calls

        return {
            "text": text,
            "tool_calls": tool_calls,
            "done": is_done,
        }

    @staticmethod
    def _extract_tool_calls_from_text(text: str) -> list[dict[str, Any]]:
        """Try to extract tool call(s) from a text response.

        Supports:
        - Single JSON object: {"name": "...", "arguments": {...}}
        - JSON array of objects: [{"name": "...", "arguments": {...}}, ...]
        - Multiple JSON objects separated by newlines
        """
        import uuid as _uuid

        results: list[dict[str, Any]] = []
        stripped = text.strip()

        # Try parsing as a single JSON value (object or array)
        try:
            parsed = json.loads(stripped)
            candidates = parsed if isinstance(parsed, list) else [parsed]
            for obj in candidates:
                tc = OpenAIProvider._maybe_tool_call(obj)
                if tc:
                    results.append(tc)
            if results:
                return results
        except (json.JSONDecodeError, TypeError):
            pass

        # Try extracting multiple JSON objects separated by newlines/whitespace
        # Find all top-level { ... } blocks
        depth = 0
        start = -1
        for i, ch in enumerate(stripped):
            if ch == '{':
                if depth == 0:
                    start = i
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0 and start >= 0:
                    try:
                        obj = json.loads(stripped[start:i + 1])
                        tc = OpenAIProvider._maybe_tool_call(obj)
                        if tc:
                            results.append(tc)
                    except (json.JSONDecodeError, TypeError):
                        pass
                    start = -1

        return results

    @staticmethod
    def _maybe_tool_call(obj: Any) -> dict[str, Any] | None:
        """Return a normalized tool-call dict if *obj* looks like one."""
        import uuid as _uuid

        if not isinstance(obj, dict):
            return None
        name = obj.get("name")
        args = obj.get("arguments", obj.get("params", obj.get("parameters", {})))
        if not name or not isinstance(name, str):
            return None
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except (json.JSONDecodeError, TypeError):
                args = {}
        if not isinstance(args, dict):
            args = {}
        return {
            "id": str(_uuid.uuid4())[:8],
            "name": name,
            "arguments": args,
        }
