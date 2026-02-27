"""Unit tests — LLM client abstraction layer."""

from __future__ import annotations

import json

import pytest

from llmos_bridge.security.llm_client import (
    LLMClient,
    LLMMessage,
    LLMResponse,
    NullLLMClient,
)


@pytest.fixture
def null_client() -> NullLLMClient:
    return NullLLMClient()


# ── NullLLMClient ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_null_client_returns_approve(null_client: NullLLMClient) -> None:
    """NullLLMClient.chat() returns JSON with verdict 'approve'."""
    messages = [LLMMessage(role="user", content="test")]
    response = await null_client.chat(messages)

    assert isinstance(response, LLMResponse)
    assert response.model == "null"

    payload = json.loads(response.content)
    assert payload["verdict"] == "approve"
    assert payload["risk_level"] == "low"


@pytest.mark.asyncio
async def test_null_client_close_is_noop(null_client: NullLLMClient) -> None:
    """close() completes without raising."""
    await null_client.close()  # should not raise


@pytest.mark.asyncio
async def test_null_client_response_is_parseable_json(
    null_client: NullLLMClient,
) -> None:
    """Response content is valid JSON with expected keys."""
    messages = [LLMMessage(role="system", content="verify")]
    response = await null_client.chat(messages)

    parsed = json.loads(response.content)
    assert "verdict" in parsed
    assert "risk_level" in parsed
    assert "reasoning" in parsed


# ── LLMMessage ─────────────────────────────────────────────────


def test_llm_message_fields() -> None:
    """LLMMessage exposes role and content fields."""
    msg = LLMMessage(role="system", content="You are a verifier.")

    assert msg.role == "system"
    assert msg.content == "You are a verifier."


# ── LLMResponse ────────────────────────────────────────────────


def test_llm_response_defaults() -> None:
    """LLMResponse provides sensible defaults for optional fields."""
    resp = LLMResponse(content="ok")

    assert resp.content == "ok"
    assert resp.model == ""
    assert resp.prompt_tokens == 0
    assert resp.completion_tokens == 0
    assert resp.latency_ms == 0.0
    assert resp.raw == {}


def test_llm_response_custom_values() -> None:
    """LLMResponse accepts and stores custom values."""
    resp = LLMResponse(
        content='{"verdict": "reject"}',
        model="gpt-4o",
        prompt_tokens=150,
        completion_tokens=42,
        latency_ms=320.5,
        raw={"id": "chatcmpl-abc123"},
    )

    assert resp.content == '{"verdict": "reject"}'
    assert resp.model == "gpt-4o"
    assert resp.prompt_tokens == 150
    assert resp.completion_tokens == 42
    assert resp.latency_ms == 320.5
    assert resp.raw == {"id": "chatcmpl-abc123"}


# ── LLMClient (abstract) ──────────────────────────────────────


def test_llm_client_cannot_instantiate() -> None:
    """LLMClient is abstract and cannot be instantiated directly."""
    with pytest.raises(TypeError):
        LLMClient()  # type: ignore[abstract]


# ── Custom implementation ──────────────────────────────────────


@pytest.mark.asyncio
async def test_custom_client_implementation() -> None:
    """A concrete subclass of LLMClient can be used normally."""

    class StubLLMClient(LLMClient):
        def __init__(self) -> None:
            self.closed = False

        async def chat(
            self,
            messages: list[LLMMessage],
            *,
            temperature: float = 0.0,
            max_tokens: int = 2048,
            timeout: float = 30.0,
        ) -> LLMResponse:
            return LLMResponse(
                content=f"echo:{messages[-1].content}",
                model="stub-v1",
                prompt_tokens=len(messages),
            )

        async def close(self) -> None:
            self.closed = True

    client = StubLLMClient()
    messages = [
        LLMMessage(role="system", content="system prompt"),
        LLMMessage(role="user", content="hello"),
    ]
    response = await client.chat(messages)

    assert response.content == "echo:hello"
    assert response.model == "stub-v1"
    assert response.prompt_tokens == 2
    assert not client.closed

    await client.close()
    assert client.closed
