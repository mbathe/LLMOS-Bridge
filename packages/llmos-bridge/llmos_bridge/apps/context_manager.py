"""Context window manager for agent conversations.

Manages the conversation history within the LLM's context window by:
- Tracking token usage (estimated)
- Applying compression strategies (truncate, summarize, sliding window)
- Preserving system prompt and recent messages
- Injecting memory and context on start
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .models import ContextConfig, ContextStrategy


@dataclass
class Message:
    """A single message in the conversation."""
    role: str                     # system | user | assistant | tool
    content: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_call_id: str = ""
    name: str = ""                # tool name for tool results
    token_estimate: int = 0       # estimated token count

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for LLM API calls."""
        d: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            d["tool_calls"] = self.tool_calls
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        if self.name:
            d["name"] = self.name
        return d


class ContextManager:
    """Manages conversation context window for an agent."""

    def __init__(self, config: ContextConfig):
        self._config = config
        self._system_prompt: str = ""
        self._messages: list[Message] = []
        self._total_tokens: int = 0

    @property
    def messages(self) -> list[Message]:
        """All messages in the conversation."""
        return list(self._messages)

    @property
    def total_tokens(self) -> int:
        """Estimated total tokens in context."""
        return self._total_tokens

    @property
    def message_count(self) -> int:
        """Number of messages (excluding system)."""
        return len(self._messages)

    def set_system_prompt(self, prompt: str) -> None:
        """Set the system prompt (always kept in context)."""
        self._system_prompt = prompt

    def add_message(self, message: Message) -> None:
        """Add a message and apply context management if needed."""
        if not message.token_estimate:
            message.token_estimate = estimate_tokens(message.content)
        self._messages.append(message)
        self._total_tokens += message.token_estimate
        self._maybe_compress()

    def add_user_message(self, content: str) -> None:
        """Convenience: add a user message."""
        self.add_message(Message(role="user", content=content))

    def add_assistant_message(
        self, content: str, tool_calls: list[dict[str, Any]] | None = None
    ) -> None:
        """Convenience: add an assistant message."""
        self.add_message(Message(
            role="assistant",
            content=content,
            tool_calls=tool_calls or [],
        ))

    def add_tool_result(self, tool_call_id: str, name: str, content: str) -> None:
        """Convenience: add a tool result message."""
        self.add_message(Message(
            role="tool",
            content=content,
            tool_call_id=tool_call_id,
            name=name,
        ))

    def get_messages_for_llm(self) -> list[dict[str, Any]]:
        """Get the full message list for an LLM API call."""
        result: list[dict[str, Any]] = []
        if self._system_prompt:
            result.append({"role": "system", "content": self._system_prompt})
        for msg in self._messages:
            result.append(msg.to_dict())
        return result

    def get_summary(self) -> str:
        """Get a text summary of the conversation so far."""
        if not self._messages:
            return ""
        parts = []
        for msg in self._messages:
            if msg.role == "user":
                parts.append(f"User: {msg.content[:200]}")
            elif msg.role == "assistant" and msg.content:
                parts.append(f"Assistant: {msg.content[:200]}")
            elif msg.role == "tool":
                parts.append(f"Tool({msg.name}): {msg.content[:100]}")
        return "\n".join(parts[-10:])  # Last 10 messages

    def needs_compression(self) -> bool:
        """Check if context needs compression."""
        sys_tokens = estimate_tokens(self._system_prompt)
        return (sys_tokens + self._total_tokens) > self._config.max_tokens * 0.8

    def _maybe_compress(self) -> None:
        """Apply compression if context exceeds threshold."""
        sys_tokens = estimate_tokens(self._system_prompt)
        total = sys_tokens + self._total_tokens
        if total <= self._config.max_tokens * 0.8:
            return

        strategy = self._config.strategy
        if strategy == ContextStrategy.truncate:
            self._compress_truncate()
        elif strategy == ContextStrategy.sliding_window:
            self._compress_sliding_window()
        elif strategy == ContextStrategy.summarize:
            # LLM-based summarization is handled by the ContextManagerModule
            # when wired into the agent runtime.  Without it, fall back to
            # sliding_window so context doesn't overflow.
            self._compress_sliding_window()

    def _compress_truncate(self) -> None:
        """Drop oldest messages until under budget."""
        keep_n = self._config.keep_last_n_messages
        if len(self._messages) > keep_n:
            dropped = self._messages[:-keep_n]
            self._messages = self._messages[-keep_n:]
            self._total_tokens -= sum(m.token_estimate for m in dropped)

    def _compress_sliding_window(self) -> None:
        """Keep only the last N messages."""
        keep_n = self._config.keep_last_n_messages
        if len(self._messages) <= keep_n:
            return

        # Build a summary message for dropped content
        dropped = self._messages[:-keep_n]
        summary_parts = []
        for msg in dropped:
            if msg.role == "user":
                summary_parts.append(f"[User asked: {msg.content[:100]}...]")
            elif msg.role == "assistant" and msg.content:
                summary_parts.append(f"[Assistant: {msg.content[:100]}...]")
            elif msg.role == "tool":
                summary_parts.append(f"[Tool {msg.name} was called]")

        # Replace dropped messages with a summary
        summary_text = "Previous conversation summary:\n" + "\n".join(summary_parts[-20:])
        summary_msg = Message(
            role="user",
            content=summary_text,
            token_estimate=estimate_tokens(summary_text),
        )

        self._messages = [summary_msg] + self._messages[-keep_n:]
        self._recalculate_tokens()

    def _recalculate_tokens(self) -> None:
        """Recalculate total token count."""
        self._total_tokens = sum(m.token_estimate for m in self._messages)

    def clear(self) -> None:
        """Clear all messages (keeps system prompt)."""
        self._messages.clear()
        self._total_tokens = 0


def estimate_tokens(text: str) -> int:
    """Estimate token count for a string (~4 chars per token)."""
    if not text:
        return 0
    return max(1, len(text) // 4)
