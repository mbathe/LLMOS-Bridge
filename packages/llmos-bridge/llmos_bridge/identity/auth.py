"""Identity layer — API key authentication and RBAC resolution.

The ``IdentityResolver`` extracts caller identity from incoming requests:

1. When ``identity.enabled=False`` (default / standalone):
   Returns ``IdentityContext(app_id="default", role=ADMIN)`` for all requests.
   No API key validation.  Identical to pre-distributed behaviour.

2. When ``identity.enabled=True`` but ``require_api_keys=False``:
   Extracts ``X-LLMOS-App`` and ``X-LLMOS-Agent`` headers if present,
   otherwise defaults to ``"default"`` app with ADMIN role.

3. When ``identity.enabled=True`` and ``require_api_keys=True``:
   Validates ``Authorization: Bearer llmos_...`` against the IdentityStore.
   Returns 401 if the key is missing, invalid, revoked, or expired.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from llmos_bridge.identity.models import IdentityContext, Role
from llmos_bridge.logging import get_logger

if TYPE_CHECKING:
    from llmos_bridge.identity.store import IdentityStore

log = get_logger(__name__)

# Default context used when identity system is disabled.
_DEFAULT_CONTEXT = IdentityContext(
    app_id="default",
    agent_id=None,
    session_id=None,
    role=Role.ADMIN,
)


class IdentityResolver:
    """Resolves the caller identity for an incoming API request.

    Stateless: consults the ``IdentityStore`` for key validation but
    does not cache anything per-request.
    """

    def __init__(
        self,
        store: IdentityStore | None = None,
        enabled: bool = False,
        require_api_keys: bool = False,
    ) -> None:
        self._store = store
        self._enabled = enabled
        self._require_keys = require_api_keys

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def requires_api_keys(self) -> bool:
        return self._require_keys

    async def resolve(
        self,
        authorization: str | None = None,
        x_app: str | None = None,
        x_agent: str | None = None,
        x_session: str | None = None,
    ) -> IdentityContext:
        """Resolve identity from request headers.

        Args:
            authorization: ``Authorization: Bearer <token>`` header value.
            x_app: ``X-LLMOS-App`` header (optional app_id override).
            x_agent: ``X-LLMOS-Agent`` header (optional agent_id).
            x_session: ``X-LLMOS-Session`` header (optional session_id).

        Returns:
            Resolved ``IdentityContext``.

        Raises:
            AuthenticationError: if API key is required but missing/invalid.
        """
        if not self._enabled:
            return _DEFAULT_CONTEXT

        # When API keys are required, validate the token.
        if self._require_keys and self._store is not None:
            if not authorization:
                from llmos_bridge.exceptions import AuthenticationError
                raise AuthenticationError("API key required (Authorization: Bearer <key>)")

            token = _extract_bearer(authorization)
            if not token:
                from llmos_bridge.exceptions import AuthenticationError
                raise AuthenticationError("Invalid Authorization header format")

            result = await self._store.resolve_api_key(token)
            if result is None:
                from llmos_bridge.exceptions import AuthenticationError
                raise AuthenticationError("Invalid, revoked, or expired API key")

            app_id, agent_id, role = result
            return IdentityContext(
                app_id=app_id,
                agent_id=agent_id,
                session_id=x_session,
                role=role,
            )

        # Identity enabled but keys not required: use headers or defaults.
        return IdentityContext(
            app_id=x_app or "default",
            agent_id=x_agent,
            session_id=x_session,
            role=Role.ADMIN,
        )

    def check_role(self, context: IdentityContext, minimum: Role) -> bool:
        """Check if the caller has at least the given role.

        Role hierarchy (most to least privileged):
          ADMIN > APP_ADMIN > OPERATOR > VIEWER > AGENT
        """
        hierarchy = [Role.ADMIN, Role.APP_ADMIN, Role.OPERATOR, Role.VIEWER, Role.AGENT]
        caller_level = hierarchy.index(context.role) if context.role in hierarchy else len(hierarchy)
        required_level = hierarchy.index(minimum) if minimum in hierarchy else 0
        return caller_level <= required_level


def _extract_bearer(auth_header: str) -> str | None:
    """Extract the token from ``Bearer <token>``."""
    parts = auth_header.strip().split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return None
