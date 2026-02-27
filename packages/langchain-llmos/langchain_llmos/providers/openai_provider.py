"""OpenAI-compatible provider for the ComputerUseAgent.

Works with OpenAI, Ollama, Mistral, and any other provider that
implements the OpenAI Chat Completions API format.

Uses the ``openai`` SDK with configurable ``base_url``.
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
    import openai

    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False


class OpenAICompatibleProvider(AgentLLMProvider):
    """LLM provider for OpenAI-compatible APIs.

    Handles OpenAI, Ollama (``http://localhost:11434/v1``),
    Mistral (``https://api.mistral.ai/v1``), and others.

    Args:
        api_key:         API key (not needed for Ollama).
        model:           Model name (e.g. ``"gpt-4o"``, ``"llama3.2"``).
        base_url:        API base URL.
        vision:          Whether the model supports image inputs.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gpt-4o",
        base_url: str = "https://api.openai.com/v1",
        vision: bool = True,
    ) -> None:
        if not _AVAILABLE:
            raise ImportError(
                "The 'openai' package is required for OpenAICompatibleProvider. "
                "Install with: pip install langchain-llmos[openai]"
            )
        self._client = openai.AsyncOpenAI(api_key=api_key or "ollama", base_url=base_url)
        self._model = model
        self._vision = vision

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

        # OpenAI puts the system message at the start of the messages array.
        full_messages = [{"role": "system", "content": system}, *messages]

        response = await self._client.chat.completions.create(
            model=self._model,
            max_tokens=max_tokens,
            messages=full_messages,
            tools=native_tools if native_tools else openai.NOT_GIVEN,
            tool_choice="auto" if native_tools else openai.NOT_GIVEN,
        )

        choice = response.choices[0]
        message = choice.message

        # Parse tool calls.
        tool_calls: list[ToolCall] = []
        if message.tool_calls:
            for tc in message.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except (json.JSONDecodeError, TypeError):
                    args = {}
                tool_calls.append(ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=args,
                ))

        return LLMTurn(
            text=message.content,
            tool_calls=tool_calls,
            is_done=choice.finish_reason == "stop",
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
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": schema,
                },
            })
        return result

    def build_user_message(self, text: str) -> list[dict[str, Any]]:
        return [{"role": "user", "content": text}]

    def build_assistant_message(self, turn: LLMTurn) -> dict[str, Any]:
        msg: dict[str, Any] = {
            "role": "assistant",
            "content": turn.text or "",
        }
        if turn.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments),
                    },
                }
                for tc in turn.tool_calls
            ]
        return msg

    def build_tool_results_message(
        self, results: list[ToolResult]
    ) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []

        # OpenAI requires one tool-role message per tool result.
        has_image = False
        image_data: str | None = None

        for r in results:
            messages.append({
                "role": "tool",
                "tool_call_id": r.tool_call_id,
                "content": r.text,
            })
            # Track if any result has an image for a follow-up user message.
            if r.image_b64 and self._vision:
                has_image = True
                image_data = r.image_b64

        # OpenAI does not support images in tool-role messages.
        # Append a separate user message with the screenshot.
        if has_image and image_data:
            messages.append({
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Here is the annotated screenshot from the previous tool call:",
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{image_data}",
                            "detail": "low",
                        },
                    },
                ],
            })

        return messages

    @property
    def supports_vision(self) -> bool:
        return self._vision

    async def close(self) -> None:
        if hasattr(self._client, "close"):
            await self._client.close()
