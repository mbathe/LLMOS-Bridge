"""Anthropic provider for the ComputerUseAgent.

Uses the ``anthropic`` SDK to communicate with Claude models.
Supports multimodal tool results (screenshots as image content blocks).
"""

from __future__ import annotations

import json
from typing import Any

from langchain_llmos.providers.base import (
    AgentLLMProvider,
    LLMTurn,
    ToolCall,
    ToolDefinition,
    ToolResult,
)

try:
    import anthropic

    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False


class AnthropicProvider(AgentLLMProvider):
    """LLM provider backed by Anthropic's Messages API.

    Args:
        api_key: Anthropic API key.  Falls back to ``ANTHROPIC_API_KEY`` env.
        model:   Model identifier (e.g. ``"claude-sonnet-4-20250514"``).
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "claude-sonnet-4-20250514",
    ) -> None:
        if not _AVAILABLE:
            raise ImportError(
                "The 'anthropic' package is required for AnthropicProvider. "
                "Install with: pip install langchain-llmos[anthropic]"
            )
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model

    # ------------------------------------------------------------------
    # ABC implementation
    # ------------------------------------------------------------------

    async def create_message(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[ToolDefinition],
        max_tokens: int = 4096,
    ) -> LLMTurn:
        native_tools = self.format_tool_definitions(tools)

        response = await self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            system=system,
            tools=native_tools,
            messages=messages,
        )

        # Parse tool calls.
        tool_calls = [
            ToolCall(
                id=block.id,
                name=block.name,
                arguments=block.input if isinstance(block.input, dict) else {},
            )
            for block in response.content
            if block.type == "tool_use"
        ]

        # Extract text.
        text_parts = [
            block.text for block in response.content if hasattr(block, "text")
        ]
        text = "\n".join(text_parts) if text_parts else None

        return LLMTurn(
            text=text,
            tool_calls=tool_calls,
            is_done=response.stop_reason == "end_turn",
            raw_response=response,
        )

    def format_tool_definitions(
        self, tools: list[ToolDefinition]
    ) -> list[dict[str, Any]]:
        result = []
        for t in tools:
            schema = dict(t.parameters_schema)
            if "type" not in schema:
                schema["type"] = "object"
            if "properties" not in schema:
                schema["properties"] = {}
            result.append({
                "name": t.name,
                "description": t.description,
                "input_schema": schema,
            })
        return result

    def build_user_message(self, text: str) -> list[dict[str, Any]]:
        return [{"role": "user", "content": text}]

    def build_assistant_message(self, turn: LLMTurn) -> dict[str, Any]:
        # Anthropic stores the raw content blocks in the assistant message.
        return {"role": "assistant", "content": turn.raw_response.content}

    def build_tool_results_message(
        self, results: list[ToolResult]
    ) -> list[dict[str, Any]]:
        tool_result_blocks: list[dict[str, Any]] = []

        for r in results:
            content: list[dict[str, Any]] = []

            # Image block first (so Claude sees screenshot before JSON).
            if r.image_b64:
                content.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": r.image_media_type,
                        "data": r.image_b64,
                    },
                })

            content.append({"type": "text", "text": r.text})

            tool_result_blocks.append({
                "type": "tool_result",
                "tool_use_id": r.tool_call_id,
                "content": content,
                "is_error": r.is_error,
            })

        # Anthropic bundles all tool results in a single user message.
        return [{"role": "user", "content": tool_result_blocks}]

    @property
    def supports_vision(self) -> bool:
        return True

    async def close(self) -> None:
        # The anthropic SDK client doesn't require explicit cleanup,
        # but we call close on the underlying httpx client if present.
        if hasattr(self._client, "_client"):
            await self._client._client.aclose()
