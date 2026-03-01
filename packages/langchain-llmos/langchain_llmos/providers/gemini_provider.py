"""Google Gemini provider for the ComputerUseAgent.

Uses the ``google-generativeai`` SDK to communicate with Gemini models.
Supports multimodal content (images, PDFs) and function calling.

Install::

    pip install langchain-llmos[gemini]
    # or: pip install google-generativeai
"""

from __future__ import annotations

import base64
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
    import google.generativeai as genai
    from google.generativeai.types import (
        ContentDict,
        FunctionDeclaration,
        Tool,
    )

    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False


class GeminiProvider(AgentLLMProvider):
    """LLM provider backed by Google's Gemini API.

    Args:
        api_key: Google API key.  Falls back to ``GOOGLE_API_KEY`` env.
        model:   Model identifier (e.g. ``"gemini-2.5-flash"``).
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gemini-2.5-flash",
        **_kwargs: Any,
    ) -> None:
        if not _AVAILABLE:
            raise ImportError(
                "The 'google-generativeai' package is required for GeminiProvider. "
                "Install with: pip install langchain-llmos[gemini]"
            )
        if api_key:
            genai.configure(api_key=api_key)
        self._model_name = model
        self._model = genai.GenerativeModel(model)

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
        # Rebuild model with system instruction on each call
        # (Gemini sets system_instruction at model level)
        model = genai.GenerativeModel(
            self._model_name,
            system_instruction=system,
        )

        native_tools = self.format_tool_definitions(tools)

        # Convert our message dicts to Gemini ContentDict format
        gemini_contents = self._to_gemini_contents(messages)

        generation_config = genai.types.GenerationConfig(
            max_output_tokens=max_tokens,
        )

        response = await model.generate_content_async(
            gemini_contents,
            tools=native_tools,
            generation_config=generation_config,
        )

        # Parse the response
        tool_calls: list[ToolCall] = []
        text_parts: list[str] = []

        for candidate in response.candidates:
            for part in candidate.content.parts:
                if hasattr(part, "function_call") and part.function_call.name:
                    fc = part.function_call
                    # Convert MapComposite to regular dict
                    args = dict(fc.args) if fc.args else {}
                    tool_calls.append(
                        ToolCall(
                            id=f"call_{fc.name}_{len(tool_calls)}",
                            name=fc.name,
                            arguments=args,
                        )
                    )
                elif hasattr(part, "text") and part.text:
                    text_parts.append(part.text)

        text = "\n".join(text_parts) if text_parts else None
        is_done = len(tool_calls) == 0

        return LLMTurn(
            text=text,
            tool_calls=tool_calls,
            is_done=is_done,
            raw_response=response,
        )

    def format_tool_definitions(
        self, tools: list[ToolDefinition]
    ) -> list[Any]:
        """Convert ToolDefinitions to Gemini FunctionDeclaration format."""
        declarations = []
        for t in tools:
            schema = dict(t.parameters_schema)
            # Gemini expects OpenAPI-style schema without 'additionalProperties'
            schema.pop("additionalProperties", None)
            if "type" not in schema:
                schema["type"] = "object"
            if "properties" not in schema:
                schema["properties"] = {}

            declarations.append(
                FunctionDeclaration(
                    name=t.name,
                    description=t.description,
                    parameters=schema,
                )
            )
        return [Tool(function_declarations=declarations)] if declarations else []

    def build_user_message(self, text: str) -> list[dict[str, Any]]:
        return [{"role": "user", "parts": [{"text": text}]}]

    def build_assistant_message(self, turn: LLMTurn) -> dict[str, Any]:
        parts: list[dict[str, Any]] = []

        # Rebuild parts from the turn
        if turn.text:
            parts.append({"text": turn.text})

        for tc in turn.tool_calls:
            parts.append({
                "function_call": {
                    "name": tc.name,
                    "args": tc.arguments,
                }
            })

        return {"role": "model", "parts": parts}

    def build_tool_results_message(
        self, results: list[ToolResult]
    ) -> list[dict[str, Any]]:
        """Build Gemini function response parts.

        Gemini uses ``function_response`` parts in a ``user`` role message.
        Images in tool results are sent as a separate user message part.
        """
        parts: list[dict[str, Any]] = []
        image_parts: list[dict[str, Any]] = []

        for r in results:
            # Function response
            try:
                response_data = json.loads(r.text)
            except (json.JSONDecodeError, TypeError):
                response_data = {"result": r.text}

            if r.is_error:
                response_data = {"error": r.text}

            # Extract function name from tool_call_id format "call_{name}_{idx}"
            tc_id = r.tool_call_id
            if tc_id.startswith("call_") and tc_id.count("_") >= 2:
                # "call_read_file_0" → "read_file"
                fn_name = "_".join(tc_id.split("_")[1:-1])
            else:
                fn_name = tc_id

            parts.append({
                "function_response": {
                    "name": fn_name,
                    "response": response_data,
                }
            })

            # Image as inline_data in a separate part
            if r.image_b64:
                image_parts.append({
                    "inline_data": {
                        "mime_type": r.image_media_type,
                        "data": r.image_b64,
                    }
                })

        messages: list[dict[str, Any]] = [{"role": "user", "parts": parts}]

        # Append images as a separate user message if any
        if image_parts:
            messages.append({"role": "user", "parts": image_parts})

        return messages

    @property
    def supports_vision(self) -> bool:
        return True

    async def close(self) -> None:
        pass

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _to_gemini_contents(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert our internal message format to Gemini ContentDict format.

        Our messages are already in Gemini-native format (role + parts)
        since build_user_message / build_assistant_message produce them.
        """
        contents: list[dict[str, Any]] = []
        for msg in messages:
            role = msg.get("role", "user")
            # Map 'assistant' → 'model' for Gemini
            if role == "assistant":
                role = "model"
            parts = msg.get("parts", [])
            if not parts and "content" in msg:
                # Fallback: plain text content
                content = msg["content"]
                if isinstance(content, str):
                    parts = [{"text": content}]
                elif isinstance(content, list):
                    parts = content
            contents.append({"role": role, "parts": parts})
        return contents
