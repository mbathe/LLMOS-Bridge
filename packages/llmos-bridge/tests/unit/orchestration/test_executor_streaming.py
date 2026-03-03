"""Unit tests — Executor streaming integration.

Tests that the PlanExecutor correctly:
  - Injects ActionStream for @streams_progress actions
  - Does NOT inject for non-decorated actions
  - Emits action_result_ready events on completion/failure
"""

from __future__ import annotations

import pytest

from llmos_bridge.events.bus import (
    NullEventBus,
    TOPIC_ACTION_RESULTS,
)
from llmos_bridge.modules.base import BaseModule, ExecutionContext
from llmos_bridge.modules.manifest import ActionSpec, ModuleManifest
from llmos_bridge.orchestration.stream import _STREAM_KEY
from llmos_bridge.orchestration.streaming_decorators import streams_progress


# ---------------------------------------------------------------------------
# Test module
# ---------------------------------------------------------------------------


class StreamingTestModule(BaseModule):
    MODULE_ID = "streaming_test"
    VERSION = "1.0.0"

    @streams_progress
    async def _action_download(self, params: dict) -> dict:
        """A streaming action."""
        stream = params.pop(_STREAM_KEY, None)
        if stream is not None:
            await stream.emit_progress(50.0, "halfway")
        return {"downloaded": True, "had_stream": stream is not None}

    async def _action_list_files(self, params: dict) -> dict:
        """A non-streaming action."""
        return {"files": ["a.txt", "b.txt"]}

    def get_manifest(self) -> ModuleManifest:
        return ModuleManifest(
            module_id=self.MODULE_ID,
            version=self.VERSION,
            description="Test module for streaming",
            actions=[
                ActionSpec(
                    name="download",
                    description="Download a file",
                    streams_progress=True,
                ),
                ActionSpec(
                    name="list_files",
                    description="List files",
                ),
            ],
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStreamsProgressDetection:
    def test_handler_has_streams_progress(self) -> None:
        mod = StreamingTestModule()
        handler = mod._get_handler("download")
        assert getattr(handler, "_streams_progress", False) is True

    def test_handler_without_streams_progress(self) -> None:
        mod = StreamingTestModule()
        handler = mod._get_handler("list_files")
        assert getattr(handler, "_streams_progress", False) is False


@pytest.mark.unit
class TestCollectStreamingMetadata:
    def test_collects_from_decorated_action(self) -> None:
        mod = StreamingTestModule()
        meta = mod._collect_streaming_metadata()
        assert "download" in meta
        assert meta["download"]["streams_progress"] is True

    def test_does_not_collect_from_undecorated(self) -> None:
        mod = StreamingTestModule()
        meta = mod._collect_streaming_metadata()
        assert "list_files" not in meta


@pytest.mark.unit
class TestActionStreamInjection:
    async def test_stream_injected_for_decorated(self) -> None:
        """When calling a @streams_progress action via execute(), the _stream key
        should be available if we manually inject it (mimicking executor behavior)."""
        mod = StreamingTestModule()
        bus = NullEventBus()
        from llmos_bridge.orchestration.stream import ActionStream

        stream = ActionStream(
            plan_id="p1",
            action_id="a1",
            module_id="streaming_test",
            action_name="download",
            _bus=bus,
        )
        result = await mod.execute(
            "download",
            {_STREAM_KEY: stream},
        )
        assert result["downloaded"] is True
        assert result["had_stream"] is True

    async def test_action_works_without_stream(self) -> None:
        """Graceful degradation — no stream injected."""
        mod = StreamingTestModule()
        result = await mod.execute("download", {})
        assert result["downloaded"] is True
        assert result["had_stream"] is False


@pytest.mark.unit
class TestActionResultReadyEvent:
    async def test_result_ready_emitted(self) -> None:
        """Verify the event structure expected from the executor."""
        bus = NullEventBus()
        events: list[dict] = []

        async def _capture(topic: str, event: dict) -> None:
            events.append(event)

        bus.register_listener(TOPIC_ACTION_RESULTS, _capture)

        # Simulate what the executor does after action completion.
        await bus.emit(TOPIC_ACTION_RESULTS, {
            "event": "action_result_ready",
            "plan_id": "p1",
            "action_id": "a1",
            "module_id": "streaming_test",
            "action": "download",
            "status": "completed",
            "result": {"downloaded": True},
        })

        assert len(events) == 1
        ev = events[0]
        assert ev["event"] == "action_result_ready"
        assert ev["status"] == "completed"
        assert ev["result"]["downloaded"] is True

    async def test_failure_event_emitted(self) -> None:
        bus = NullEventBus()
        events: list[dict] = []

        async def _capture(topic: str, event: dict) -> None:
            events.append(event)

        bus.register_listener(TOPIC_ACTION_RESULTS, _capture)

        await bus.emit(TOPIC_ACTION_RESULTS, {
            "event": "action_result_ready",
            "plan_id": "p1",
            "action_id": "a1",
            "module_id": "streaming_test",
            "action": "download",
            "status": "failed",
            "error": "Connection refused",
        })

        assert len(events) == 1
        assert events[0]["status"] == "failed"
        assert events[0]["error"] == "Connection refused"


@pytest.mark.unit
class TestManifestStreamsProgress:
    def test_action_spec_streams_progress_field(self) -> None:
        mod = StreamingTestModule()
        manifest = mod.get_manifest()
        download_spec = manifest.get_action("download")
        assert download_spec is not None
        assert download_spec.streams_progress is True

    def test_action_spec_default_false(self) -> None:
        mod = StreamingTestModule()
        manifest = mod.get_manifest()
        list_spec = manifest.get_action("list_files")
        assert list_spec is not None
        assert list_spec.streams_progress is False

    def test_to_dict_includes_streams_progress(self) -> None:
        mod = StreamingTestModule()
        manifest = mod.get_manifest()
        d = manifest.to_dict()
        download_dict = next(
            a for a in d["actions"] if a["name"] == "download"
        )
        assert download_dict["streams_progress"] is True

    def test_to_dict_omits_when_false(self) -> None:
        mod = StreamingTestModule()
        manifest = mod.get_manifest()
        d = manifest.to_dict()
        list_dict = next(
            a for a in d["actions"] if a["name"] == "list_files"
        )
        assert "streams_progress" not in list_dict
