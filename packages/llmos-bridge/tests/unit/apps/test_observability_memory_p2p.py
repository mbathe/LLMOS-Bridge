"""Tests for tracing, metrics, procedural memory, and P2P/blackboard communication."""

import asyncio
import json

import pytest

from llmos_bridge.apps.agent_runtime import AgentRunResult, LLMProvider
from llmos_bridge.apps.expression import ExpressionContext, ExpressionEngine
from llmos_bridge.apps.memory_manager import AppMemoryManager
from llmos_bridge.apps.models import (
    AgentConfig,
    BrainConfig,
    CommunicationConfig,
    CommunicationMode,
    LoopConfig,
    MemoryConfig,
    MetricDefinition,
    MultiAgentConfig,
    MultiAgentStrategy,
    ObservabilityConfig,
    ProceduralMemoryConfig,
    TracingConfig,
)
from llmos_bridge.apps.multi_agent import AgentInstance, MultiAgentOrchestrator
from llmos_bridge.apps.observability import MetricsCollector, Span, TracingManager
from llmos_bridge.apps.tool_registry import ResolvedTool


# ── Mock LLM ────────────────────────────────────────────────────────


class MockLLM(LLMProvider):
    def __init__(self, response_text="Done"):
        self._text = response_text

    async def chat(self, *, system, messages, tools, max_tokens=4096, **kwargs):
        return {"text": self._text, "tool_calls": [], "done": True}

    async def close(self):
        pass


class MessageSendingLLM(LLMProvider):
    """LLM that sends a message to another agent, then finishes."""

    def __init__(self, target: str, message: str):
        self._target = target
        self._message = message
        self._call_count = 0

    async def chat(self, *, system, messages, tools, max_tokens=4096, **kwargs):
        self._call_count += 1
        if self._call_count == 1:
            return {
                "text": "Sending message.",
                "tool_calls": [{
                    "id": "tc1",
                    "name": "send_message",
                    "arguments": {"target": self._target, "message": self._message},
                }],
                "done": False,
            }
        return {"text": "Message sent.", "tool_calls": [], "done": True}

    async def close(self):
        pass


# ── Mock KV Store ────────────────────────────────────────────────────


class MockKVStore:
    def __init__(self):
        self._data: dict[str, str] = {}

    async def set(self, key: str, value, ttl_seconds=None):
        self._data[key] = value if isinstance(value, str) else json.dumps(value)

    async def get(self, key: str):
        return self._data.get(key)

    async def get_many(self, keys: list[str]):
        return {k: self._data.get(k) for k in keys if k in self._data}

    async def delete(self, key: str):
        self._data.pop(key, None)


# ── Mock EventBus ────────────────────────────────────────────────────


class MockEventBus:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    async def emit(self, topic: str, data: dict):
        self.events.append((topic, data))


# ══════════════════════════════════════════════════════════════════════
#   TRACING TESTS
# ══════════════════════════════════════════════════════════════════════


class TestTracingManager:
    def test_start_trace_disabled(self):
        config = TracingConfig(enabled=False)
        tm = TracingManager(config)
        span = tm.start_trace("test")
        assert span.trace_id == ""
        assert span.span_id == ""

    def test_start_trace_enabled(self):
        config = TracingConfig(enabled=True, sample_rate=1.0)
        tm = TracingManager(config, app_name="myapp")
        span = tm.start_trace("app.run")
        assert span.trace_id != ""
        assert span.span_id != ""
        assert span.attributes["app.name"] == "myapp"
        assert len(tm.spans) == 1

    def test_sampling_rate_zero(self):
        config = TracingConfig(enabled=True, sample_rate=0.0)
        tm = TracingManager(config)
        span = tm.start_trace("test")
        assert span.span_id == "not_sampled"
        assert len(tm.spans) == 0

    @pytest.mark.asyncio
    async def test_child_span(self):
        config = TracingConfig(enabled=True, sample_rate=1.0)
        tm = TracingManager(config)
        root = tm.start_trace("root")
        async with tm.span("child", attributes={"key": "val"}) as child:
            assert child.parent_span_id == root.span_id
            assert child.trace_id == root.trace_id
            assert child.attributes["key"] == "val"
        assert child.end_time > 0
        assert len(tm.spans) == 2

    @pytest.mark.asyncio
    async def test_child_span_error(self):
        config = TracingConfig(enabled=True, sample_rate=1.0)
        tm = TracingManager(config)
        tm.start_trace("root")
        with pytest.raises(ValueError, match="boom"):
            async with tm.span("bad") as s:
                raise ValueError("boom")
        assert s.status == "error"
        assert any(e["name"] == "exception" for e in s.events)

    @pytest.mark.asyncio
    async def test_span_disabled(self):
        config = TracingConfig(enabled=False)
        tm = TracingManager(config)
        tm.start_trace("root")
        async with tm.span("child") as s:
            pass
        assert s.span_id == ""
        assert len(tm.spans) == 0

    def test_end_trace(self):
        config = TracingConfig(enabled=True, sample_rate=1.0)
        tm = TracingManager(config)
        root = tm.start_trace("root")
        tm.end_trace(root, status="error")
        assert root.status == "error"
        assert root.end_time > 0

    def test_trace_summary(self):
        config = TracingConfig(enabled=True, sample_rate=1.0)
        tm = TracingManager(config)
        tm.start_trace("root")
        summary = tm.get_trace_summary()
        assert summary["span_count"] == 1
        assert summary["sampled"] is True

    @pytest.mark.asyncio
    async def test_span_emits_to_event_bus(self):
        bus = MockEventBus()
        config = TracingConfig(enabled=True, sample_rate=1.0)
        tm = TracingManager(config, event_bus=bus)
        tm.start_trace("root")
        async with tm.span("child"):
            pass
        assert len(bus.events) == 1
        assert bus.events[0][0] == "llmos.tracing"
        assert bus.events[0][1]["type"] == "span_ended"

    def test_span_duration_ms(self):
        s = Span(name="test", trace_id="t1", span_id="s1", start_time=1.0, end_time=1.5)
        assert s.duration_ms == 500.0

    def test_span_add_event(self):
        s = Span(name="test", trace_id="t1", span_id="s1")
        s.add_event("checkpoint", {"step": 3})
        assert len(s.events) == 1
        assert s.events[0]["name"] == "checkpoint"

    def test_span_to_dict(self):
        s = Span(name="test", trace_id="t1", span_id="s1", start_time=0, end_time=1)
        d = s.to_dict()
        assert d["name"] == "test"
        assert d["duration_ms"] == 1000.0


# ══════════════════════════════════════════════════════════════════════
#   METRICS TESTS
# ══════════════════════════════════════════════════════════════════════


class TestMetricsCollector:
    def test_counter_increment(self):
        mc = MetricsCollector([MetricDefinition(name="calls", type="counter")])
        mc.increment("calls")
        mc.increment("calls", 5)
        metrics = mc.get_metrics()
        assert metrics["counters"]["calls"] == 6

    def test_gauge_set(self):
        mc = MetricsCollector([MetricDefinition(name="active", type="gauge")])
        mc.set_gauge("active", 42)
        metrics = mc.get_metrics()
        assert metrics["gauges"]["active"] == 42

    def test_histogram_observe(self):
        mc = MetricsCollector([MetricDefinition(name="latency", type="histogram")])
        mc.observe("latency", 100)
        mc.observe("latency", 200)
        mc.observe("latency", 300)
        metrics = mc.get_metrics()
        h = metrics["histograms"]["latency"]
        assert h["count"] == 3
        assert h["min"] == 100
        assert h["max"] == 300
        assert h["avg"] == 200

    @pytest.mark.asyncio
    async def test_record_action_duration(self):
        mc = MetricsCollector([
            MetricDefinition(name="duration", type="histogram", track="action.duration_ms"),
        ])
        await mc.record_action("fs", "read", {}, {}, 150.0)
        metrics = mc.get_metrics()
        assert metrics["histograms"]["duration"]["count"] == 1
        assert metrics["histograms"]["duration"]["sum"] == 150.0

    @pytest.mark.asyncio
    async def test_record_action_count(self):
        mc = MetricsCollector([
            MetricDefinition(name="total", type="counter", track="action.count"),
        ])
        await mc.record_action("fs", "read", {}, {}, 10)
        await mc.record_action("fs", "write", {}, {}, 20)
        metrics = mc.get_metrics()
        assert metrics["counters"]["total"] == 2

    @pytest.mark.asyncio
    async def test_record_action_error(self):
        mc = MetricsCollector([
            MetricDefinition(name="errors", type="counter", track="action.error"),
        ])
        await mc.record_action("fs", "read", {}, {"error": "fail"}, 10)
        await mc.record_action("fs", "write", {}, {"result": "ok"}, 10)
        metrics = mc.get_metrics()
        assert metrics["counters"]["errors"] == 1

    @pytest.mark.asyncio
    async def test_record_action_success(self):
        mc = MetricsCollector([
            MetricDefinition(name="success", type="counter", track="action.success"),
        ])
        await mc.record_action("fs", "read", {}, {"result": "ok"}, 10)
        await mc.record_action("fs", "write", {}, {"error": "fail"}, 10)
        metrics = mc.get_metrics()
        assert metrics["counters"]["success"] == 1

    @pytest.mark.asyncio
    async def test_metrics_emit_to_event_bus(self):
        bus = MockEventBus()
        mc = MetricsCollector(
            [MetricDefinition(name="calls", type="counter", track="action.count")],
            event_bus=bus,
        )
        await mc.record_action("fs", "read", {}, {}, 10)
        assert len(bus.events) == 1
        assert bus.events[0][0] == "llmos.metrics"

    def test_unknown_metric_ignored(self):
        mc = MetricsCollector([MetricDefinition(name="x", type="counter")])
        mc.increment("nonexistent")  # Should not raise
        mc.observe("nonexistent", 1)  # Should not raise
        assert mc.get_metrics()["counters"]["x"] == 0

    def test_empty_histogram(self):
        mc = MetricsCollector([MetricDefinition(name="h", type="histogram")])
        metrics = mc.get_metrics()
        h = metrics["histograms"]["h"]
        assert h["count"] == 0
        assert h["sum"] == 0
        assert h["avg"] == 0


# ══════════════════════════════════════════════════════════════════════
#   PROCEDURAL MEMORY TESTS
# ══════════════════════════════════════════════════════════════════════


class TestProceduralMemory:
    @pytest.mark.asyncio
    async def test_learn_and_recall(self):
        kv = MockKVStore()
        cfg = MemoryConfig(procedural=ProceduralMemoryConfig())
        mgr = AppMemoryManager(config=cfg, kv_store=kv)

        await mgr.learn_procedure(
            "proc1",
            pattern="fix import error by adding missing package",
            outcome="Successfully resolved ImportError",
            success=True,
        )
        procedures = await mgr.recall_procedures()
        assert len(procedures) == 1
        assert procedures[0]["pattern"] == "fix import error by adding missing package"
        assert procedures[0]["success"] is True

    @pytest.mark.asyncio
    async def test_learn_failure(self):
        kv = MockKVStore()
        cfg = MemoryConfig(procedural=ProceduralMemoryConfig())
        mgr = AppMemoryManager(config=cfg, kv_store=kv)

        await mgr.learn_procedure(
            "proc_fail",
            pattern="tried rm -rf to fix disk space",
            outcome="Accidentally deleted important files",
            success=False,
        )
        procs = await mgr.recall_procedures()
        assert len(procs) == 1
        assert procs[0]["success"] is False

    @pytest.mark.asyncio
    async def test_learn_from_failures_disabled(self):
        kv = MockKVStore()
        cfg = MemoryConfig(
            procedural=ProceduralMemoryConfig(learn_from_failures=False)
        )
        mgr = AppMemoryManager(config=cfg, kv_store=kv)

        await mgr.learn_procedure(
            "nope", pattern="bad", outcome="bad", success=False,
        )
        procs = await mgr.recall_procedures()
        assert len(procs) == 0

    @pytest.mark.asyncio
    async def test_learn_from_successes_disabled(self):
        kv = MockKVStore()
        cfg = MemoryConfig(
            procedural=ProceduralMemoryConfig(learn_from_successes=False)
        )
        mgr = AppMemoryManager(config=cfg, kv_store=kv)

        await mgr.learn_procedure(
            "nope", pattern="good", outcome="good", success=True,
        )
        procs = await mgr.recall_procedures()
        assert len(procs) == 0

    @pytest.mark.asyncio
    async def test_suggest_procedures(self):
        kv = MockKVStore()
        cfg = MemoryConfig(
            procedural=ProceduralMemoryConfig(auto_suggest=True)
        )
        mgr = AppMemoryManager(config=cfg, kv_store=kv)

        await mgr.learn_procedure(
            "p1", pattern="fix import error python", outcome="install package", success=True,
        )
        await mgr.learn_procedure(
            "p2", pattern="delete temporary files", outcome="cleaned up", success=True,
        )

        suggestions = await mgr.suggest_procedures("I have a python import error")
        assert len(suggestions) >= 1
        assert any("import" in s["pattern"] for s in suggestions)

    @pytest.mark.asyncio
    async def test_suggest_no_match(self):
        kv = MockKVStore()
        cfg = MemoryConfig(
            procedural=ProceduralMemoryConfig(auto_suggest=True)
        )
        mgr = AppMemoryManager(config=cfg, kv_store=kv)

        await mgr.learn_procedure(
            "p1", pattern="fix database", outcome="migrated", success=True,
        )
        suggestions = await mgr.suggest_procedures("deploy to kubernetes")
        assert len(suggestions) == 0

    @pytest.mark.asyncio
    async def test_suggest_disabled(self):
        kv = MockKVStore()
        cfg = MemoryConfig(
            procedural=ProceduralMemoryConfig(auto_suggest=False)
        )
        mgr = AppMemoryManager(config=cfg, kv_store=kv)

        await mgr.learn_procedure(
            "p1", pattern="fix thing", outcome="fixed", success=True,
        )
        suggestions = await mgr.suggest_procedures("fix thing")
        assert len(suggestions) == 0

    @pytest.mark.asyncio
    async def test_no_kv_store_noop(self):
        cfg = MemoryConfig(procedural=ProceduralMemoryConfig())
        mgr = AppMemoryManager(config=cfg, kv_store=None)

        await mgr.learn_procedure(
            "p1", pattern="test", outcome="test", success=True,
        )
        procs = await mgr.recall_procedures()
        assert len(procs) == 0

    @pytest.mark.asyncio
    async def test_procedural_in_build_memory_context(self):
        kv = MockKVStore()
        cfg = MemoryConfig(
            procedural=ProceduralMemoryConfig(auto_suggest=True)
        )
        mgr = AppMemoryManager(config=cfg, kv_store=kv)

        await mgr.learn_procedure(
            "p1", pattern="fix import error", outcome="install package", success=True,
        )
        context = await mgr.build_memory_context("fix an import error")
        assert "procedural" in context
        assert len(context["procedural"]) >= 1

    @pytest.mark.asyncio
    async def test_format_procedural(self):
        mgr = AppMemoryManager()
        text = mgr.format_for_prompt({
            "procedural": [
                {"pattern": "fix bug", "outcome": "patched", "success": True},
                {"pattern": "bad approach", "outcome": "crashed", "success": False},
            ]
        })
        assert "Learned Procedures" in text
        assert "[SUCCESS]" in text
        assert "[FAILURE]" in text


# ══════════════════════════════════════════════════════════════════════
#   P2P COMMUNICATION TESTS
# ══════════════════════════════════════════════════════════════════════


def _make_agent_config(agent_id: str = "agent1") -> AgentConfig:
    return AgentConfig(
        id=agent_id,
        system_prompt="You are a test agent",
        brain=BrainConfig(provider="test", model="test"),
        loop=LoopConfig(max_turns=3),
    )


class TestPeerToPeer:
    @pytest.mark.asyncio
    async def test_p2p_agents_run_concurrently(self):
        """P2P mode runs all agents in parallel."""
        config = MultiAgentConfig(
            agents=[_make_agent_config("a1"), _make_agent_config("a2")],
            communication=CommunicationConfig(mode=CommunicationMode.peer_to_peer),
            strategy=MultiAgentStrategy.hierarchical,  # Should be ignored
        )
        agents = {
            "a1": AgentInstance(config=config.agents[0], llm=MockLLM("Agent1 done"), tools=[]),
            "a2": AgentInstance(config=config.agents[1], llm=MockLLM("Agent2 done"), tools=[]),
        }
        orch = MultiAgentOrchestrator(config=config, agents=agents)
        result = await orch.run("Do something")

        assert result.success
        assert "a1" in result.agent_results
        assert "a2" in result.agent_results
        assert "Agent1 done" in result.output
        assert "Agent2 done" in result.output

    @pytest.mark.asyncio
    async def test_p2p_empty_agents(self):
        config = MultiAgentConfig(
            agents=[],
            communication=CommunicationConfig(mode=CommunicationMode.peer_to_peer),
        )
        orch = MultiAgentOrchestrator(config=config, agents={})
        result = await orch.run("test")
        assert not result.success
        assert result.error == "No agents configured"

    @pytest.mark.asyncio
    async def test_p2p_agent_failure_partial_success(self):
        """If one agent fails, result is still success if another succeeds."""

        class FailingLLM(LLMProvider):
            async def chat(self, *, system, messages, tools, max_tokens=4096, **kwargs):
                raise RuntimeError("LLM crash")

            async def close(self):
                pass

        config = MultiAgentConfig(
            agents=[_make_agent_config("good"), _make_agent_config("bad")],
            communication=CommunicationConfig(mode=CommunicationMode.peer_to_peer),
        )
        agents = {
            "good": AgentInstance(config=config.agents[0], llm=MockLLM("Good result"), tools=[]),
            "bad": AgentInstance(config=config.agents[1], llm=FailingLLM(), tools=[]),
        }
        orch = MultiAgentOrchestrator(config=config, agents=agents)
        result = await orch.run("test")
        assert result.success  # At least one agent succeeded
        assert result.agent_results["bad"].error is not None


# ══════════════════════════════════════════════════════════════════════
#   BLACKBOARD COMMUNICATION TESTS
# ══════════════════════════════════════════════════════════════════════


class TestBlackboard:
    @pytest.mark.asyncio
    async def test_blackboard_agents_share_state(self):
        """Blackboard mode runs agents in rounds with shared context."""
        config = MultiAgentConfig(
            agents=[_make_agent_config("analyst"), _make_agent_config("reviewer")],
            communication=CommunicationConfig(mode=CommunicationMode.blackboard),
        )
        agents = {
            "analyst": AgentInstance(
                config=config.agents[0], llm=MockLLM("Analysis complete"), tools=[]
            ),
            "reviewer": AgentInstance(
                config=config.agents[1], llm=MockLLM("Review complete"), tools=[]
            ),
        }
        orch = MultiAgentOrchestrator(config=config, agents=agents)
        result = await orch.run("Analyze and review this code")

        assert result.success
        assert "analyst" in result.agent_results
        assert "reviewer" in result.agent_results
        assert "Analysis complete" in result.output
        assert "Review complete" in result.output

    @pytest.mark.asyncio
    async def test_blackboard_empty_agents(self):
        config = MultiAgentConfig(
            agents=[],
            communication=CommunicationConfig(mode=CommunicationMode.blackboard),
        )
        orch = MultiAgentOrchestrator(config=config, agents={})
        result = await orch.run("test")
        assert not result.success

    @pytest.mark.asyncio
    async def test_blackboard_context_contains_contributions(self):
        """The blackboard context passed to later agents includes prior contributions."""
        received_messages = []

        class SpyLLM(LLMProvider):
            def __init__(self, name):
                self._name = name

            async def chat(self, *, system, messages, tools, max_tokens=4096, **kwargs):
                # Record what context was passed
                for msg in messages:
                    if msg.get("role") == "user":
                        received_messages.append((self._name, msg.get("content", "")))
                return {"text": f"{self._name} output", "tool_calls": [], "done": True}

            async def close(self):
                pass

        config = MultiAgentConfig(
            agents=[_make_agent_config("first"), _make_agent_config("second")],
            communication=CommunicationConfig(mode=CommunicationMode.blackboard),
        )
        agents = {
            "first": AgentInstance(config=config.agents[0], llm=SpyLLM("first"), tools=[]),
            "second": AgentInstance(config=config.agents[1], llm=SpyLLM("second"), tools=[]),
        }
        orch = MultiAgentOrchestrator(config=config, agents=agents)
        await orch.run("analyze code")

        # Second agent should see first agent's contribution in context
        second_msgs = [msg for name, msg in received_messages if name == "second"]
        assert any("first" in msg and "output" in msg for msg in second_msgs)


# ══════════════════════════════════════════════════════════════════════
#   BUILTIN send_message TESTS
# ══════════════════════════════════════════════════════════════════════


class TestSendMessageBuiltin:
    @pytest.mark.asyncio
    async def test_send_message_with_handler(self):
        from llmos_bridge.apps.builtins import BuiltinToolExecutor

        sent = []

        async def handler(target: str, message: str):
            sent.append((target, message))
            return {"sent": True, "to": target}

        executor = BuiltinToolExecutor(send_message_handler=handler)
        result = await executor.execute("send_message", {
            "target": "agent_b",
            "message": "Hello from agent_a",
        })
        assert result["sent"] is True
        assert len(sent) == 1
        assert sent[0] == ("agent_b", "Hello from agent_a")

    @pytest.mark.asyncio
    async def test_send_message_no_handler(self):
        from llmos_bridge.apps.builtins import BuiltinToolExecutor

        executor = BuiltinToolExecutor()
        result = await executor.execute("send_message", {
            "target": "agent_b",
            "message": "Hello",
        })
        assert "error" in result

    @pytest.mark.asyncio
    async def test_send_message_missing_params(self):
        from llmos_bridge.apps.builtins import BuiltinToolExecutor

        async def handler(target, message):
            return {"sent": True}

        executor = BuiltinToolExecutor(send_message_handler=handler)
        result = await executor.execute("send_message", {"target": ""})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_send_message_is_builtin(self):
        from llmos_bridge.apps.builtins import BuiltinToolExecutor

        executor = BuiltinToolExecutor()
        assert executor.is_builtin("send_message")
