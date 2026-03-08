"""Security execution context — per-request app identity for permission checks.

A lightweight ContextVar that carries the current application ID through the
async call chain so that ``@requires_permission`` can check per-app OS grants
instead of only the global ``"default"`` namespace.

Set by ``DaemonToolExecutor.execute()`` at the start of every tool call.
Read by ``PermissionManager.check_or_raise()`` to scope permission lookups.

This module has NO imports from the rest of llmos_bridge so it can be safely
imported from both ``apps/`` and ``security/`` without circular dependencies.
"""

from __future__ import annotations

import contextvars

# Stores the app_id of the currently executing YAML app.
# None means no app context (daemon-level / global scope → use "default").
_current_app_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_security_current_app_id", default=None
)


def get_security_app_id() -> str:
    """Return the current app's ID for permission scoping, or ``'default'``."""
    return _current_app_id.get() or "default"


def set_security_app_id(app_id: str | None) -> contextvars.Token[str | None]:
    """Set the security context app ID and return a reset token."""
    return _current_app_id.set(app_id)
