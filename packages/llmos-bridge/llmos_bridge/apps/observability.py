"""App-level observability — tracing and custom metrics.

Implements:
- TracingManager: span-based tracing with pluggable backends (OpenTelemetry or lightweight)
- MetricsCollector: custom metric tracking from YAML metric definitions

The tracing system wraps app runs, agent turns, tool calls, and flow steps
in hierarchical spans. Metrics are evaluated per-action and exposed via
the event bus for external collection (Prometheus, Grafana, etc.).
"""

from __future__ import annotations

import logging
import random
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from .models import MetricDefinition, ObservabilityConfig, TracingConfig

logger = logging.getLogger(__name__)


# ── Span data model ───────────────────────────────────────────────


@dataclass
class Span:
    """A single trace span — lightweight representation without external deps."""

    name: str
    trace_id: str
    span_id: str
    parent_span_id: str | None = None
    start_time: float = 0.0
    end_time: float = 0.0
    status: str = "ok"  # ok | error
    attributes: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)

    @property
    def duration_ms(self) -> float:
        return (self.end_time - self.start_time) * 1000

    def add_event(self, name: str, attributes: dict[str, Any] | None = None) -> None:
        self.events.append({
            "name": name,
            "timestamp": time.time(),
            "attributes": attributes or {},
        })

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "attributes": self.attributes,
            "events": self.events,
        }


def _gen_id(length: int = 16) -> str:
    """Generate a random hex ID."""
    return format(random.getrandbits(length * 4), f"0{length}x")


# ── Tracing Manager ──────────────────────────────────────────────


class TracingManager:
    """Manages trace spans for an app execution.

    When tracing is enabled, creates hierarchical spans for:
    - App run (root span)
    - Agent turns
    - Tool calls
    - Flow steps

    Spans are emitted to the EventBus for external collection.
    If OpenTelemetry is available and configured, also exports via OTLP.
    """

    def __init__(
        self,
        config: TracingConfig,
        *,
        event_bus: Any = None,
        app_name: str = "",
    ):
        self._config = config
        self._event_bus = event_bus
        self._app_name = app_name
        self._enabled = config.enabled
        self._sample_rate = config.sample_rate
        self._spans: list[Span] = []
        self._current_trace_id: str = ""
        self._span_stack: list[Span] = []
        # Sampling decision (made once per trace)
        self._sampled = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def spans(self) -> list[Span]:
        return list(self._spans)

    def start_trace(self, name: str, attributes: dict[str, Any] | None = None) -> Span:
        """Start a new trace (root span)."""
        if not self._enabled:
            return Span(name=name, trace_id="", span_id="")

        self._current_trace_id = _gen_id(32)
        # Sampling: decide once per trace
        self._sampled = random.random() < self._sample_rate
        if not self._sampled:
            return Span(name=name, trace_id=self._current_trace_id, span_id="not_sampled")

        span = Span(
            name=name,
            trace_id=self._current_trace_id,
            span_id=_gen_id(),
            start_time=time.time(),
            attributes={
                "app.name": self._app_name,
                **(attributes or {}),
            },
        )
        self._span_stack.append(span)
        self._spans.append(span)
        return span

    @asynccontextmanager
    async def span(self, name: str, attributes: dict[str, Any] | None = None) -> AsyncIterator[Span]:
        """Create a child span within the current trace."""
        if not self._enabled or not self._sampled:
            yield Span(name=name, trace_id=self._current_trace_id, span_id="")
            return

        parent_id = self._span_stack[-1].span_id if self._span_stack else None
        child = Span(
            name=name,
            trace_id=self._current_trace_id,
            span_id=_gen_id(),
            parent_span_id=parent_id,
            start_time=time.time(),
            attributes=attributes or {},
        )
        self._span_stack.append(child)
        self._spans.append(child)

        try:
            yield child
        except Exception as e:
            child.status = "error"
            child.add_event("exception", {"message": str(e), "type": type(e).__name__})
            raise
        finally:
            child.end_time = time.time()
            if self._span_stack and self._span_stack[-1] is child:
                self._span_stack.pop()
            # Emit span to event bus
            if self._event_bus:
                try:
                    await self._event_bus.emit("llmos.tracing", {
                        "type": "span_ended",
                        "span": child.to_dict(),
                    })
                except Exception:
                    pass

    def end_trace(self, root_span: Span, status: str = "ok") -> None:
        """End the root trace span."""
        if not self._enabled or not self._sampled:
            return
        root_span.end_time = time.time()
        root_span.status = status
        self._span_stack.clear()

    def get_trace_summary(self) -> dict[str, Any]:
        """Get a summary of all spans in the current trace."""
        return {
            "trace_id": self._current_trace_id,
            "sampled": self._sampled,
            "span_count": len(self._spans),
            "spans": [s.to_dict() for s in self._spans],
        }


# ── Metrics Collector ────────────────────────────────────────────


class MetricsCollector:
    """Collects custom metrics defined in observability.metrics[].

    Supports three metric types:
    - counter: Monotonically increasing value
    - gauge: Value that can go up or down
    - histogram: Distribution of values (stores raw values)

    The `track` expression is evaluated after each action to determine
    the value to record. Metrics are emitted to the EventBus.
    """

    def __init__(
        self,
        definitions: list[MetricDefinition],
        *,
        event_bus: Any = None,
        expr_engine: Any = None,
        expr_context: Any = None,
    ):
        self._definitions = definitions
        self._event_bus = event_bus
        self._expr = expr_engine
        self._ctx = expr_context

        # Metric storage
        self._counters: dict[str, float] = {}
        self._gauges: dict[str, float] = {}
        self._histograms: dict[str, list[float]] = {}

        # Initialize
        for defn in definitions:
            if defn.type == "counter":
                self._counters[defn.name] = 0
            elif defn.type == "gauge":
                self._gauges[defn.name] = 0
            elif defn.type == "histogram":
                self._histograms[defn.name] = []

    def increment(self, name: str, value: float = 1) -> None:
        """Increment a counter."""
        if name in self._counters:
            self._counters[name] += value

    def set_gauge(self, name: str, value: float) -> None:
        """Set a gauge value."""
        self._gauges[name] = value

    def observe(self, name: str, value: float) -> None:
        """Record a histogram observation."""
        if name in self._histograms:
            self._histograms[name].append(value)

    async def record_action(
        self,
        module_id: str,
        action: str,
        params: dict[str, Any],
        result: dict[str, Any],
        duration_ms: float,
    ) -> None:
        """Evaluate track expressions and update metrics after an action."""
        for defn in self._definitions:
            if not defn.track:
                continue

            try:
                value = self._evaluate_track(defn, module_id, action, params, result, duration_ms)
                if value is None:
                    continue

                if defn.type == "counter":
                    self.increment(defn.name, float(value))
                elif defn.type == "gauge":
                    self.set_gauge(defn.name, float(value))
                elif defn.type == "histogram":
                    self.observe(defn.name, float(value))
            except Exception as e:
                logger.debug("Metric evaluation failed for %s: %s", defn.name, e)

        # Emit all metrics to event bus
        if self._event_bus:
            try:
                await self._event_bus.emit("llmos.metrics", {
                    "type": "metrics_update",
                    "counters": dict(self._counters),
                    "gauges": dict(self._gauges),
                    "histograms": {k: len(v) for k, v in self._histograms.items()},
                })
            except Exception:
                pass

    def _evaluate_track(
        self,
        defn: MetricDefinition,
        module_id: str,
        action: str,
        params: dict[str, Any],
        result: dict[str, Any],
        duration_ms: float,
    ) -> Any:
        """Evaluate a track expression for a metric."""
        track = defn.track

        # Simple built-in expressions (no engine needed)
        if track == "action.duration_ms":
            return duration_ms
        if track == "action.count":
            return 1
        if track == "action.error":
            return 1 if result.get("error") else 0
        if track == "action.success":
            return 0 if result.get("error") else 1

        # Use expression engine if available
        if self._expr and self._ctx:
            from .expression import ExpressionContext
            ctx = ExpressionContext(
                variables={
                    "module": module_id,
                    "action": action,
                    "params": params,
                    "result": result,
                    "duration_ms": duration_ms,
                },
            )
            resolved = self._expr.resolve(track, ctx)
            return resolved

        return None

    def get_metrics(self) -> dict[str, Any]:
        """Get all current metric values."""
        return {
            "counters": dict(self._counters),
            "gauges": dict(self._gauges),
            "histograms": {
                name: {
                    "count": len(values),
                    "sum": sum(values) if values else 0,
                    "min": min(values) if values else 0,
                    "max": max(values) if values else 0,
                    "avg": sum(values) / len(values) if values else 0,
                }
                for name, values in self._histograms.items()
            },
        }
