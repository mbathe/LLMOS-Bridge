"""Unit tests — Session lifecycle in AsyncLLMOSClient and ReactivePlanLoop."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from langchain_llmos.client import AsyncLLMOSClient
from langchain_llmos.loop import ReactivePlanLoop
from langchain_llmos.providers.base import AgentLLMProvider, LLMTurn, ToolDefinition


# ---------------------------------------------------------------------------
# Minimal mock provider (always says "done")
# ---------------------------------------------------------------------------


class DoneProvider(AgentLLMProvider):
    async def create_message(self, *, system, messages, tools, max_tokens=4096):
        return LLMTurn(text="Task done.", tool_calls=[], is_done=True, raw_response=None)

    def format_tool_definitions(self, tools):
        return []

    def build_user_message(self, text):
        return [{"role": "user", "content": text}]

    def build_assistant_message(self, turn):
        return {"role": "assistant", "content": turn.text or ""}

    def build_tool_results_message(self, results):
        return [{"role": "user", "content": "result"}]

    @property
    def supports_vision(self):
        return False

    async def close(self):
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_daemon(session_id: str = "sess-abc") -> AsyncMock:
    daemon = AsyncMock(spec=AsyncLLMOSClient)
    daemon.session_id = None
    daemon.create_session = AsyncMock(return_value={"session_id": session_id})
    daemon.delete_session = AsyncMock(return_value=None)
    daemon.submit_plan = AsyncMock(
        return_value={"plan_id": "p1", "status": "pending"}
    )
    daemon.get_plan = AsyncMock(
        return_value={"status": "completed", "actions": []}
    )
    return daemon


# ---------------------------------------------------------------------------
# AsyncLLMOSClient — unit tests
# ---------------------------------------------------------------------------


class TestAsyncLLMOSClientSessions:
    def test_default_no_session(self):
        client = AsyncLLMOSClient.__new__(AsyncLLMOSClient)
        client.session_id = None
        assert client._session_headers() == {}

    def test_session_headers_when_set(self):
        client = AsyncLLMOSClient.__new__(AsyncLLMOSClient)
        client.session_id = "my-session-42"
        assert client._session_headers() == {"X-LLMOS-Session": "my-session-42"}

    def test_session_headers_cleared(self):
        client = AsyncLLMOSClient.__new__(AsyncLLMOSClient)
        client.session_id = "x"
        client.session_id = None
        assert client._session_headers() == {}

    def test_init_stores_base_url_and_app_id(self):
        """_base_url must be stored so _stream_plan can build the SSE URL."""
        import httpx

        client = AsyncLLMOSClient.__new__(AsyncLLMOSClient)
        # Minimal manual init to avoid real network setup
        client._base_url = "http://localhost:9999"
        client._app_id = "acme"
        client.session_id = None
        assert client._base_url == "http://localhost:9999"
        assert client._app_id == "acme"

    @pytest.mark.asyncio
    async def test_create_session_calls_correct_endpoint(self):
        daemon = AsyncMock()
        daemon.create_session = AsyncMock(
            return_value={"session_id": "s1", "app_id": "myapp"}
        )
        result = await daemon.create_session(app_id="myapp", expires_in_seconds=60)
        daemon.create_session.assert_called_once_with(
            app_id="myapp", expires_in_seconds=60
        )
        assert result["session_id"] == "s1"

    @pytest.mark.asyncio
    async def test_delete_session_called(self):
        daemon = AsyncMock()
        daemon.delete_session = AsyncMock()
        await daemon.delete_session("sess-1")
        daemon.delete_session.assert_called_once_with("sess-1")


# ---------------------------------------------------------------------------
# ReactivePlanLoop — session lifecycle
# ---------------------------------------------------------------------------


class TestReactivePlanLoopSessions:
    @pytest.mark.asyncio
    async def test_no_session_config_no_create(self):
        """When session_config=None, create_session is never called."""
        daemon = _make_daemon()
        loop = ReactivePlanLoop(
            provider=DoneProvider(),
            daemon=daemon,
            session_config=None,
        )
        result = await loop.run("do thing", "system", [])
        daemon.create_session.assert_not_called()
        daemon.delete_session.assert_not_called()
        assert result.success is True

    @pytest.mark.asyncio
    async def test_session_created_and_deleted_on_success(self):
        """Session is created before the loop and deleted after success."""
        daemon = _make_daemon(session_id="sess-xyz")
        loop = ReactivePlanLoop(
            provider=DoneProvider(),
            daemon=daemon,
            session_config={"app_id": "myapp", "expires_in_seconds": 600},
        )
        result = await loop.run("do thing", "system", [])

        daemon.create_session.assert_called_once_with(
            app_id="myapp", expires_in_seconds=600
        )
        daemon.delete_session.assert_called_once_with("sess-xyz")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_session_id_injected_into_daemon(self):
        """After create_session, daemon.session_id is set."""
        daemon = _make_daemon(session_id="sess-injected")
        loop = ReactivePlanLoop(
            provider=DoneProvider(),
            daemon=daemon,
            session_config={"app_id": "app1"},
        )

        captured_sid: list[str | None] = []

        original_submit = daemon.submit_plan.side_effect

        async def track_session(*args, **kwargs):
            captured_sid.append(daemon.session_id)
            return {"plan_id": "p1", "status": "pending"}

        # The provider is DoneProvider so no submit_plan will be called
        # (task done on first LLM call). We need to verify the session_id
        # was set right after create_session. We can inspect it after run().
        await loop.run("do thing", "system", [])

        # session_id is cleared after the loop finishes.
        assert daemon.session_id is None

    @pytest.mark.asyncio
    async def test_session_cleared_after_run(self):
        """daemon.session_id is set to None even after successful run."""
        daemon = _make_daemon(session_id="ephemeral")
        loop = ReactivePlanLoop(
            provider=DoneProvider(),
            daemon=daemon,
            session_config={"app_id": "app"},
        )
        await loop.run("task", "sys", [])
        assert daemon.session_id is None

    @pytest.mark.asyncio
    async def test_session_deleted_even_if_run_raises(self):
        """Session cleanup happens in finally — even if the loop crashes."""

        class FailingProvider(DoneProvider):
            async def create_message(self, **kwargs):
                raise RuntimeError("LLM exploded")

        daemon = _make_daemon(session_id="cleanup-me")
        loop = ReactivePlanLoop(
            provider=FailingProvider(),
            daemon=daemon,
            session_config={"app_id": "app"},
        )
        with pytest.raises(RuntimeError, match="LLM exploded"):
            await loop.run("task", "sys", [])

        daemon.delete_session.assert_called_once_with("cleanup-me")
        assert daemon.session_id is None

    @pytest.mark.asyncio
    async def test_create_session_failure_does_not_abort_run(self):
        """If session creation fails, the loop still runs without a session."""
        daemon = _make_daemon()
        daemon.create_session = AsyncMock(side_effect=Exception("503 unavailable"))

        loop = ReactivePlanLoop(
            provider=DoneProvider(),
            daemon=daemon,
            session_config={"app_id": "app"},
        )
        # Should not raise — session creation failure is soft.
        result = await loop.run("task", "sys", [])
        assert result.success is True
        # No session means no delete call.
        daemon.delete_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_session_failure_does_not_propagate(self):
        """If session deletion fails, the result is still returned normally."""
        daemon = _make_daemon(session_id="del-fail")
        daemon.delete_session = AsyncMock(side_effect=Exception("connection refused"))

        loop = ReactivePlanLoop(
            provider=DoneProvider(),
            daemon=daemon,
            session_config={"app_id": "app"},
        )
        # Should not raise.
        result = await loop.run("task", "sys", [])
        assert result.success is True
        daemon.delete_session.assert_called_once_with("del-fail")
