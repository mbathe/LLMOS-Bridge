"""Provider abstraction for the ComputerUseAgent.

Defines the ``AgentLLMProvider`` ABC and provider-agnostic data types
that decouple the agent loop from any specific LLM SDK.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class ToolDefinition:
    """Provider-agnostic tool definition built from daemon manifests."""

    name: str
    description: str
    parameters_schema: dict[str, Any]


@dataclass
class ToolCall:
    """A tool invocation extracted from the LLM response."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMTurn:
    """Parsed LLM response turn (provider-agnostic)."""

    text: str | None
    tool_calls: list[ToolCall]
    is_done: bool
    raw_response: Any  # original SDK response, opaque to the agent


@dataclass
class ToolResult:
    """Result of a tool execution, to be sent back to the LLM."""

    tool_call_id: str
    text: str
    image_b64: str | None = None
    image_media_type: str = "image/png"
    is_error: bool = False


# ---------------------------------------------------------------------------
# ABC
# ---------------------------------------------------------------------------


class AgentLLMProvider(ABC):
    """Abstract LLM provider for the :class:`ComputerUseAgent`.

    Concrete implementations handle SDK-specific formatting:
    tool schemas, message assembly, response parsing, and multimodal encoding.
    """

    @abstractmethod
    async def create_message(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[ToolDefinition],
        max_tokens: int = 4096,
    ) -> LLMTurn:
        """Send messages to the LLM and return the parsed turn."""
        ...

    @abstractmethod
    def format_tool_definitions(
        self, tools: list[ToolDefinition]
    ) -> list[dict[str, Any]]:
        """Convert ``ToolDefinition`` list to provider-native format."""
        ...

    @abstractmethod
    def build_user_message(self, text: str) -> list[dict[str, Any]]:
        """Build initial user message(s) from plain text."""
        ...

    @abstractmethod
    def build_assistant_message(self, turn: LLMTurn) -> dict[str, Any]:
        """Build provider-native assistant message for history."""
        ...

    @abstractmethod
    def build_tool_results_message(
        self, results: list[ToolResult]
    ) -> list[dict[str, Any]]:
        """Build provider-native tool result message(s).

        Returns a *list* of messages because some providers (OpenAI) require
        one message per tool result, while others (Anthropic) bundle them.
        """
        ...

    @property
    @abstractmethod
    def supports_vision(self) -> bool:
        """Whether this provider can handle image content in tool results."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Release resources held by the provider."""
        ...
