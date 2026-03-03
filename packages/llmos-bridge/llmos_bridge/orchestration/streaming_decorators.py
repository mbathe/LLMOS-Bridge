"""Orchestration layer — Streaming decorator for module actions.

Provides ``@streams_progress`` — a metadata-only decorator that marks an
action as supporting real-time progress streaming.  The executor detects this
marker and injects an ``ActionStream`` into the action's params dict.

Unlike the security decorators, ``@streams_progress`` does NOT wrap the
function.  It simply sets a boolean attribute on it.  This avoids
decorator-stacking complexity with the 6 security decorators.

Usage::

    from llmos_bridge.orchestration.streaming_decorators import streams_progress

    class ApiHttpModule(BaseModule):

        @streams_progress
        @requires_permission(Permission.NETWORK_READ)
        async def _action_download_file(self, params: dict) -> Any:
            stream = params.pop("_stream", None)
            ...
"""

from __future__ import annotations

from typing import Any, Callable


# ---------------------------------------------------------------------------
# Metadata attribute tracked through decorator stacking
# ---------------------------------------------------------------------------

_STREAMING_ATTRS = ("_streams_progress",)


def _copy_streaming_metadata(source: Any, target: Any) -> None:
    """Copy streaming metadata from *source* to *target*."""
    for attr in _STREAMING_ATTRS:
        if hasattr(source, attr):
            setattr(target, attr, getattr(source, attr))


# ---------------------------------------------------------------------------
# @streams_progress
# ---------------------------------------------------------------------------


def streams_progress(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Mark an action as supporting progress streaming.

    This is a metadata-only decorator — it does **not** wrap the function.
    The PlanExecutor checks for ``_streams_progress`` on the handler and
    injects an ``ActionStream`` into ``params["_stream"]``.

    Usage::

        @streams_progress
        async def _action_download(self, params):
            stream: ActionStream = params.pop("_stream")
            await stream.emit_progress(50, "Halfway done")
            return {"ok": True}
    """
    fn._streams_progress = True  # type: ignore[attr-defined]
    return fn


# ---------------------------------------------------------------------------
# Introspection
# ---------------------------------------------------------------------------


def collect_streaming_metadata(fn: Any) -> dict[str, Any]:
    """Extract streaming decorator metadata from a (possibly wrapped) function.

    Returns::

        {"streams_progress": True}   — if decorated
        {}                           — if not decorated
    """
    meta: dict[str, Any] = {}
    if getattr(fn, "_streams_progress", False):
        meta["streams_progress"] = True
    return meta
