"""LLM providers for the LLMOS App Language runtime.

Implements the LLMProvider protocol for real LLM backends.
Supports: Anthropic Claude, OpenAI GPT.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from .agent_runtime import LLMProvider

logger = logging.getLogger(__name__)


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

        response = await self._client.messages.create(**kwargs)

        return self._parse_response(response)

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

        response = await self._client.chat.completions.create(**kwargs)
        return self._parse_response(response)

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
